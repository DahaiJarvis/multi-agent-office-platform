"""RAG 增强检索原生工具

提供 RAG 增强检索和 RAG 问答功能，复用 rag_enhanced 模块的核心能力。

工具列表：
  -------------------------------------------------------------------------
  native_rag_search: RAG 增强检索（HyDE）
    - 延迟分层: slow
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖 LLM + 向量数据库）

  native_rag_qa: RAG 问答（CRAG）
    - 延迟分层: slow
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖 LLM + 向量数据库）
  -------------------------------------------------------------------------

数据来源：复用 agent/core/rag_enhanced.py 的 enhance_rag
策略说明：
  - HyDE: 先生成假设答案，用假设答案的 Embedding 检索，适用于短查询
  - CRAG: 检索后评估相关性，不相关时重写查询重试，适用于需要高准确率的场景
"""

import json
import logging
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)


async def _rag_search(query: str, top_k: int = 5) -> str:
    """RAG 增强检索

    使用 HyDE（假设文档嵌入）策略进行增强检索，先让 LLM 生成假设性答案，
    再用假设答案的 Embedding 去检索，提升短查询的检索准确度。

    Args:
        query: 检索查询
        top_k: 返回文档数量，默认 5 条

    Returns:
        JSON 格式的检索结果，包含文档列表和相关性评分
    """
    if not query or not query.strip():
        return json.dumps({"error": "检索查询不能为空", "documents": []}, ensure_ascii=False)

    try:
        from agent.core.rag_enhanced import enhance_rag

        result = await enhance_rag(
            query=query.strip(),
            strategy="hyde",
        )

        documents = []
        for doc in result.documents[:top_k]:
            documents.append({
                "content": doc.get("content", doc.get("text", ""))[:500],
                "title": doc.get("title", ""),
                "source": doc.get("source", ""),
                "score": doc.get("score", doc.get("relevance", doc.get("similarity", 0.0))),
            })

        output = {
            "query": query.strip(),
            "strategy": "hyde",
            "enhanced_query": result.enhanced_query,
            "documents": documents,
            "relevance_score": round(result.relevance_score, 4),
            "total_duration_ms": round(result.total_duration_ms, 2),
            "fallback_used": result.fallback_used,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        logger.error("RAG 检索失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"RAG 检索失败: {str(e)}", "documents": []}, ensure_ascii=False)


async def _rag_qa(query: str, top_k: int = 5) -> str:
    """RAG 问答

    使用 CRAG（纠正性 RAG）策略进行问答，检索后评估文档相关性，
    不相关时重写查询重新检索，确保高准确率。

    Args:
        query: 问答查询
        top_k: 返回文档数量，默认 5 条

    Returns:
        JSON 格式的问答结果，包含答案和引用来源
    """
    if not query or not query.strip():
        return json.dumps({"error": "问答查询不能为空", "answer": "", "sources": []}, ensure_ascii=False)

    try:
        from agent.core.rag_enhanced import enhance_rag

        result = await enhance_rag(
            query=query.strip(),
            strategy="crag",
        )

        documents = result.documents[:top_k]
        sources = []
        for doc in documents:
            sources.append({
                "content": doc.get("content", doc.get("text", ""))[:300],
                "title": doc.get("title", ""),
                "source": doc.get("source", ""),
                "score": doc.get("score", doc.get("relevance", doc.get("similarity", 0.0))),
            })

        if documents and result.relevance_score >= 0.3:
            from agent.core.model_client import get_lightweight_client
            from autogen_core.models import UserMessage

            client = get_lightweight_client()

            context_parts = []
            for i, doc in enumerate(documents[:3], 1):
                content = doc.get("content", doc.get("text", ""))
                context_parts.append(f"[参考文档{i}]: {content[:800]}")

            context_text = "\n\n".join(context_parts)

            prompt = (
                "请根据以下参考文档回答问题。如果参考文档中没有相关信息，"
                "请说明无法从已有知识中找到答案。\n\n"
                f"参考文档：\n{context_text}\n\n"
                f"问题：{query.strip()}\n\n"
                "请给出准确、完整的回答，并在回答末尾标注引用的文档编号。"
            )

            response = await client.create(
                messages=[UserMessage(source="user", content=prompt)],
                extra_create_args={"temperature": 0.3, "max_tokens": 1500},
            )

            answer = response.content if isinstance(response.content, str) else str(response.content)
        else:
            answer = "未找到相关的知识库文档，无法回答该问题。"

        output = {
            "query": query.strip(),
            "strategy": "crag",
            "answer": answer.strip(),
            "sources": sources,
            "relevance_score": round(result.relevance_score, 4),
            "iterations": result.iterations,
            "total_duration_ms": round(result.total_duration_ms, 2),
            "fallback_used": result.fallback_used,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        logger.error("RAG 问答失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"RAG 问答失败: {str(e)}", "answer": "", "sources": []}, ensure_ascii=False)


_RAG_SEARCH_META = NativeToolMeta(
    name="native_rag_search",
    display_name="RAG 增强检索",
    description="使用 HyDE（假设文档嵌入）策略进行增强检索，先让 AI 生成假设性答案，再用假设答案的 Embedding 检索，提升短查询的检索准确度。",
    category="rag",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索查询",
            },
            "top_k": {
                "type": "integer",
                "description": "返回文档数量，默认 5 条",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=60,
    requires_llm=True,
    tags=["rag", "search", "hyde", "llm"],
)

_RAG_QA_META = NativeToolMeta(
    name="native_rag_qa",
    display_name="RAG 问答",
    description="使用 CRAG（纠正性 RAG）策略进行问答，检索后评估文档相关性，不相关时重写查询重试，确保高准确率。返回答案和引用来源。",
    category="rag",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "问答查询",
            },
            "top_k": {
                "type": "integer",
                "description": "返回文档数量，默认 5 条",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=90,
    requires_llm=True,
    tags=["rag", "qa", "crag", "llm"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有 RAG 增强检索工具

    RAG 工具依赖 LLM 和向量数据库，使用懒注册模式。

    Args:
        registry: 工具注册中心实例
    """

    def _create_rag_search_tool() -> FunctionTool:
        return FunctionTool(
            func=_rag_search,
            name="native_rag_search",
            description=_RAG_SEARCH_META.description,
        )

    def _create_rag_qa_tool() -> FunctionTool:
        return FunctionTool(
            func=_rag_qa,
            name="native_rag_qa",
            description=_RAG_QA_META.description,
        )

    registry.register_lazy("native_rag_search", _create_rag_search_tool, _RAG_SEARCH_META)
    registry.register_lazy("native_rag_qa", _create_rag_qa_tool, _RAG_QA_META)

    logger.debug("RAG 增强检索工具注册完成: native_rag_search, native_rag_qa(均懒注册)")
