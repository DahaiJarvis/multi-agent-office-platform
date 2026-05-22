"""搜索引擎原生工具

提供跨数据源统一搜索、文档库搜索和知识库搜索功能，复用 search_engine 模块的核心能力。

工具列表：
  -------------------------------------------------------------------------
  native_search_all: 跨数据源统一搜索
    - 延迟分层: fast
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖搜索引擎索引）

  native_search_documents: 搜索文档库
    - 延迟分层: fast
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖搜索引擎索引）

  native_search_knowledge: 搜索知识库
    - 延迟分层: fast
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖搜索引擎索引）
  -------------------------------------------------------------------------

数据来源：复用 agent/core/search_engine.py 的 enterprise_search
"""

import json
import logging
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)


async def _search_all(query: str, sources: list[str] | None = None, limit: int = 10) -> str:
    """跨数据源统一搜索

    在多个数据源中并行搜索，合并排序返回结果。

    Args:
        query: 搜索查询关键词
        sources: 数据源列表，为空时搜索全部数据源。可选: documents, oa, email, calendar, crm
        limit: 返回结果数量，默认 10 条

    Returns:
        JSON 格式的搜索结果
    """
    if not query or not query.strip():
        return json.dumps({"error": "搜索关键词不能为空", "hits": []}, ensure_ascii=False)

    try:
        from agent.core.search_engine import SearchRequest, DataSource, enterprise_search

        data_sources = [DataSource.ALL]
        if sources:
            valid_sources = []
            for s in sources:
                try:
                    valid_sources.append(DataSource(s))
                except ValueError:
                    logger.warning("忽略无效数据源: %s", s)
            if valid_sources:
                data_sources = valid_sources

        request = SearchRequest(
            query=query.strip(),
            data_sources=data_sources,
            limit=min(limit, 50),
        )
        response = await enterprise_search(request)

        hits = []
        for hit in response.hits:
            hits.append({
                "source": hit.source.value,
                "title": hit.title,
                "content": hit.content,
                "url": hit.url,
                "score": round(hit.score, 4),
                "highlights": hit.highlights,
            })

        result = {
            "query": query,
            "total": response.total,
            "hits": hits,
            "search_type": response.search_type.value,
            "latency_ms": response.latency_ms,
            "suggestions": response.suggestions,
            "facets": response.facets,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("统一搜索失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"搜索失败: {str(e)}", "hits": []}, ensure_ascii=False)


async def _search_documents(query: str, limit: int = 10) -> str:
    """搜索文档库

    在企业文档库中搜索相关文档。

    Args:
        query: 搜索查询关键词
        limit: 返回结果数量，默认 10 条

    Returns:
        JSON 格式的搜索结果
    """
    if not query or not query.strip():
        return json.dumps({"error": "搜索关键词不能为空", "hits": []}, ensure_ascii=False)

    try:
        from agent.core.search_engine import SearchRequest, DataSource, enterprise_search

        request = SearchRequest(
            query=query.strip(),
            data_sources=[DataSource.DOCUMENTS],
            limit=min(limit, 50),
        )
        response = await enterprise_search(request)

        hits = []
        for hit in response.hits:
            hits.append({
                "source": hit.source.value,
                "title": hit.title,
                "content": hit.content,
                "url": hit.url,
                "score": round(hit.score, 4),
                "highlights": hit.highlights,
            })

        result = {
            "query": query,
            "total": response.total,
            "hits": hits,
            "latency_ms": response.latency_ms,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("文档搜索失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"文档搜索失败: {str(e)}", "hits": []}, ensure_ascii=False)


async def _search_knowledge(query: str, limit: int = 10) -> str:
    """搜索知识库

    在企业知识库中搜索相关知识条目。

    Args:
        query: 搜索查询关键词
        limit: 返回结果数量，默认 10 条

    Returns:
        JSON 格式的搜索结果
    """
    if not query or not query.strip():
        return json.dumps({"error": "搜索关键词不能为空", "hits": []}, ensure_ascii=False)

    try:
        from agent.core.search_engine import SearchRequest, DataSource, enterprise_search

        request = SearchRequest(
            query=query.strip(),
            data_sources=[DataSource.DOCUMENTS],
            search_type="semantic",
            limit=min(limit, 50),
        )
        response = await enterprise_search(request)

        hits = []
        for hit in response.hits:
            hits.append({
                "source": hit.source.value,
                "title": hit.title,
                "content": hit.content,
                "url": hit.url,
                "score": round(hit.score, 4),
                "highlights": hit.highlights,
            })

        result = {
            "query": query,
            "total": response.total,
            "hits": hits,
            "latency_ms": response.latency_ms,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("知识库搜索失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"知识库搜索失败: {str(e)}", "hits": []}, ensure_ascii=False)


_SEARCH_ALL_META = NativeToolMeta(
    name="native_search_all",
    display_name="跨数据源统一搜索",
    description="在多个数据源（文档库、OA、邮件、日历、CRM）中并行搜索，合并排序返回结果。支持关键词、语义和混合搜索。",
    category="search",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询关键词",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "数据源列表，可选: documents, oa, email, calendar, crm。为空时搜索全部",
                "default": [],
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认 10 条",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=15,
    requires_llm=False,
    tags=["search", "unified", "cross-source"],
)

_SEARCH_DOCUMENTS_META = NativeToolMeta(
    name="native_search_documents",
    display_name="文档库搜索",
    description="在企业文档库中搜索相关文档，返回匹配的文档标题、内容和相关性评分。",
    category="search",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询关键词",
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认 10 条",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=15,
    requires_llm=False,
    tags=["search", "documents"],
)

_SEARCH_KNOWLEDGE_META = NativeToolMeta(
    name="native_search_knowledge",
    display_name="知识库搜索",
    description="在企业知识库中搜索相关知识条目，使用语义搜索提升匹配准确度。",
    category="search",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询关键词",
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认 10 条",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=15,
    requires_llm=False,
    tags=["search", "knowledge"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有搜索引擎工具

    搜索引擎工具依赖搜索引擎索引，使用懒注册模式。

    Args:
        registry: 工具注册中心实例
    """

    def _create_search_all_tool() -> FunctionTool:
        return FunctionTool(
            func=_search_all,
            name="native_search_all",
            description=_SEARCH_ALL_META.description,
        )

    def _create_search_documents_tool() -> FunctionTool:
        return FunctionTool(
            func=_search_documents,
            name="native_search_documents",
            description=_SEARCH_DOCUMENTS_META.description,
        )

    def _create_search_knowledge_tool() -> FunctionTool:
        return FunctionTool(
            func=_search_knowledge,
            name="native_search_knowledge",
            description=_SEARCH_KNOWLEDGE_META.description,
        )

    registry.register_lazy("native_search_all", _create_search_all_tool, _SEARCH_ALL_META)
    registry.register_lazy("native_search_documents", _create_search_documents_tool, _SEARCH_DOCUMENTS_META)
    registry.register_lazy("native_search_knowledge", _create_search_knowledge_tool, _SEARCH_KNOWLEDGE_META)

    logger.debug("搜索引擎工具注册完成: native_search_all, native_search_documents, native_search_knowledge(均懒注册)")
