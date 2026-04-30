"""RAG 增强模块（HyDE + CRAG）

提供两种高级 RAG 策略，提升知识库检索的准确性和相关性：

1. HyDE (Hypothetical Document Embeddings)
   - 先让 LLM 生成假设性答案
   - 用假设答案的 Embedding 去检索，而非原始问题
   - 适用于：问题短、检索词不精确的场景

2. CRAG (Corrective RAG)
   - 检索后评估文档相关性
   - 相关 -> 直接使用
   - 不相关 -> 重写查询重新检索
   - 仍不相关 -> 放弃检索，依赖 LLM 自身知识
   - 适用于：需要高准确率、减少幻觉的场景

使用方式：
    from agent.core.rag_enhanced import enhance_rag

    result = await enhance_rag(
        query="公司年假政策是什么",
        strategy="hyde",
        search_fn=search_knowledge_base,
    )
"""

import json
import logging
import time
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RAGResult(BaseModel):
    """RAG 增强结果"""

    query: str = Field(..., description="原始查询")
    enhanced_query: str = Field(default="", description="增强后的查询")
    strategy: str = Field(default="", description="使用的策略: hyde/crag/standard")
    documents: list[dict[str, Any]] = Field(default_factory=list, description="检索到的文档")
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0, description="相关性评分")
    iterations: int = Field(default=1, description="检索迭代次数")
    total_duration_ms: float = Field(default=0, description="总耗时(毫秒)")
    fallback_used: bool = Field(default=False, description="是否降级到标准检索")


class HyDEConfig(BaseModel):
    """HyDE 配置"""

    max_hypothetical_length: int = Field(default=200, description="假设答案最大长度")
    temperature: float = Field(default=0.3, description="生成假设答案的温度")


class CRAGConfig(BaseModel):
    """CRAG 配置"""

    max_retries: int = Field(default=2, description="最大重试次数")
    relevance_threshold: float = Field(default=0.6, description="相关性阈值")
    rewrite_temperature: float = Field(default=0.5, description="查询重写温度")


async def enhance_rag(
    query: str,
    strategy: str = "auto",
    search_fn: Callable[[str, int], Awaitable[list[dict[str, Any]]]] | None = None,
    hyde_config: HyDEConfig | None = None,
    crag_config: CRAGConfig | None = None,
) -> RAGResult:
    """RAG 增强检索入口

    Args:
        query: 用户查询
        strategy: 策略选择 "hyde"/"crag"/"auto"/"standard"
        search_fn: 检索函数，签名为 async (query, top_k) -> list[dict]
        hyde_config: HyDE 配置
        crag_config: CRAG 配置

    Returns:
        RAGResult 增强检索结果
    """
    start_time = time.time()

    if search_fn is None:
        search_fn = _default_search_fn

    # 自动选择策略
    effective_strategy = strategy
    if strategy == "auto":
        effective_strategy = _select_strategy(query)

    try:
        if effective_strategy == "hyde":
            result = await _hyde_search(query, search_fn, hyde_config or HyDEConfig())
        elif effective_strategy == "crag":
            result = await _crag_search(query, search_fn, crag_config or CRAGConfig())
        else:
            result = await _standard_search(query, search_fn)
    except Exception as e:
        logger.warning("RAG 增强检索失败，降级到标准检索: %s", e)
        result = await _standard_search(query, search_fn)
        result.fallback_used = True

    result.total_duration_ms = (time.time() - start_time) * 1000
    return result


def _select_strategy(query: str) -> str:
    """根据查询特征自动选择策略

    - 短查询（<10字）或疑问词开头 -> HyDE（需要扩展查询语义）
    - 长查询或包含专业术语 -> CRAG（需要验证检索质量）
    - 其他 -> standard
    """
    query_len = len(query.strip())
    question_starts = ("什么", "怎么", "如何", "为什么", "哪", "是否", "能不能", "可以")

    if query_len < 10 or query.startswith(question_starts):
        return "hyde"

    if query_len > 30 or any(kw in query for kw in ("政策", "流程", "规定", "制度", "标准")):
        return "crag"

    return "standard"


async def _hyde_search(
    query: str,
    search_fn: Callable[[str, int], Awaitable[list[dict[str, Any]]]],
    config: HyDEConfig,
) -> RAGResult:
    """HyDE 检索策略

    步骤：
    1. 用 LLM 生成假设性答案
    2. 用假设答案作为检索查询
    3. 返回检索结果
    """
    # 生成假设答案
    hypothetical_answer = await _generate_hypothetical_answer(query, config)

    if hypothetical_answer:
        # 用假设答案检索
        docs = await search_fn(hypothetical_answer, 5)
        enhanced_query = hypothetical_answer
    else:
        # 降级到原始查询
        docs = await search_fn(query, 5)
        enhanced_query = query

    return RAGResult(
        query=query,
        enhanced_query=enhanced_query[:200],
        strategy="hyde",
        documents=docs,
        relevance_score=_compute_relevance(docs),
    )


async def _crag_search(
    query: str,
    search_fn: Callable[[str, int], Awaitable[list[dict[str, Any]]]],
    config: CRAGConfig,
) -> RAGResult:
    """CRAG 检索策略

    步骤：
    1. 初次检索
    2. 评估文档相关性
    3. 不相关时重写查询重新检索
    4. 仍不相关时放弃检索
    """
    # 初次检索
    docs = await search_fn(query, 5)
    relevance = _compute_relevance(docs)

    if relevance >= config.relevance_threshold:
        return RAGResult(
            query=query,
            enhanced_query=query,
            strategy="crag",
            documents=docs,
            relevance_score=relevance,
            iterations=1,
        )

    # 重写查询重试
    for attempt in range(config.max_retries):
        rewritten_query = await _rewrite_query(query, docs, config)

        if not rewritten_query or rewritten_query == query:
            break

        docs = await search_fn(rewritten_query, 5)
        relevance = _compute_relevance(docs)

        if relevance >= config.relevance_threshold:
            return RAGResult(
                query=query,
                enhanced_query=rewritten_query,
                strategy="crag",
                documents=docs,
                relevance_score=relevance,
                iterations=attempt + 2,
            )

    # 所有重试都不理想，返回最佳结果
    return RAGResult(
        query=query,
        enhanced_query=query,
        strategy="crag",
        documents=docs,
        relevance_score=relevance,
        iterations=config.max_retries + 1,
    )


async def _standard_search(
    query: str,
    search_fn: Callable[[str, int], Awaitable[list[dict[str, Any]]]],
) -> RAGResult:
    """标准检索（无增强）"""
    docs = await search_fn(query, 5)
    return RAGResult(
        query=query,
        enhanced_query=query,
        strategy="standard",
        documents=docs,
        relevance_score=_compute_relevance(docs),
    )


async def _generate_hypothetical_answer(query: str, config: HyDEConfig) -> str:
    """生成假设性答案

    使用轻量级 LLM 生成一个可能的答案，用于语义检索。
    """
    prompt = f"""请针对以下问题，生成一段可能出现在相关文档中的答案片段。
不要添加"根据文档"等引用语，直接给出答案内容。
答案长度控制在{config.max_hypothetical_length}字以内。

问题：{query}

假设答案："""

    try:
        from autogen_core.models import UserMessage
        from agent.core.model_client import get_lightweight_client

        client = get_lightweight_client()
        response = await client.create(
            messages=[UserMessage(source="user", content=prompt)],
            extra_create_args={
                "temperature": config.temperature,
                "max_tokens": config.max_hypothetical_length,
            },
        )

        content = response.content
        if isinstance(content, list):
            content = "".join(
                part.text for part in content if hasattr(part, "text")
            )
        if content:
            return content.strip()
    except Exception as e:
        logger.debug("生成假设答案失败: %s", e)

    return ""


async def _rewrite_query(
    original_query: str,
    irrelevant_docs: list[dict[str, Any]],
    config: CRAGConfig,
) -> str:
    """重写查询

    基于原始查询和不相关文档，生成更精确的查询。
    """
    doc_summary = ""
    if irrelevant_docs:
        doc_summary = "\n".join(
            doc.get("content", doc.get("text", ""))[:100]
            for doc in irrelevant_docs[:2]
        )

    prompt = f"""原始查询：{original_query}

以下检索结果与查询不相关：
{doc_summary}

请重写查询，使其更精确地匹配相关文档。只输出重写后的查询，不要解释。

重写查询："""

    try:
        from autogen_core.models import UserMessage
        from agent.core.model_client import get_lightweight_client

        client = get_lightweight_client()
        response = await client.create(
            messages=[UserMessage(source="user", content=prompt)],
            extra_create_args={
                "temperature": config.rewrite_temperature,
                "max_tokens": 100,
            },
        )

        content = response.content
        if isinstance(content, list):
            content = "".join(
                part.text for part in content if hasattr(part, "text")
            )
        if content:
            return content.strip()
    except Exception as e:
        logger.debug("重写查询失败: %s", e)

    return original_query


def _compute_relevance(docs: list[dict[str, Any]]) -> float:
    """计算文档集合的平均相关性"""
    if not docs:
        return 0.0

    scores = []
    for doc in docs:
        score = doc.get("score", doc.get("relevance", doc.get("similarity", 0.0)))
        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            scores.append(0.0)

    return sum(scores) / len(scores) if scores else 0.0


async def _default_search_fn(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """默认检索函数

    通过 MCP knowledge 服务进行检索。
    """
    try:
        from agent.core.mcp_integration import load_agent_tools
        tools = await load_agent_tools(["knowledge"])

        for tool in tools:
            if hasattr(tool, "name") and "search" in tool.name.lower():
                result = await tool.run(json.dumps({"query": query, "top_k": top_k}))
                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        return parsed.get("data", parsed.get("results", []))
                    except json.JSONDecodeError:
                        pass
                elif isinstance(result, list):
                    return result
    except Exception as e:
        logger.debug("默认检索失败: %s", e)

    return []
