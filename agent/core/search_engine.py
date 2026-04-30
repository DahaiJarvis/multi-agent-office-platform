"""企业搜索引擎

跨数据源统一搜索，与 M365 Copilot Search 对齐。

支持的数据源：
  - 文档库：企业文档、知识库
  - OA 系统：审批单、公告
  - 邮件系统：邮件内容
  - 日历系统：日程安排
  - CRM 系统：客户信息

搜索能力：
  - 全文检索：基于关键词的全文搜索
  - 语义搜索：基于向量嵌入的语义相似度搜索
  - 混合搜索：关键词 + 语义搜索融合
  - 联邦搜索：跨多个数据源并行搜索
  - 权限过滤：仅返回用户有权访问的结果
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SearchType(str, Enum):
    """搜索类型"""

    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class DataSource(str, Enum):
    """数据源"""

    DOCUMENTS = "documents"
    OA = "oa"
    EMAIL = "email"
    CALENDAR = "calendar"
    CRM = "crm"
    ALL = "all"


class SearchHit(BaseModel):
    """搜索结果条目"""

    source: DataSource
    title: str
    content: str
    url: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    highlights: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    """搜索请求"""

    query: str = Field(min_length=1, max_length=500, description="搜索查询")
    data_sources: list[DataSource] = Field(default_factory=lambda: [DataSource.ALL], description="数据源列表")
    search_type: SearchType = Field(default=SearchType.HYBRID, description="搜索类型")
    limit: int = Field(default=10, ge=1, le=50, description="返回结果数量")
    offset: int = Field(default=0, ge=0, description="偏移量")
    user_id: str = Field(default="", description="用户ID，用于权限过滤")
    filters: dict[str, Any] = Field(default_factory=dict, description="过滤条件")


class SearchResponse(BaseModel):
    """搜索响应"""

    query: str
    total: int
    hits: list[SearchHit]
    search_type: SearchType
    latency_ms: float
    suggestions: list[str] = Field(default_factory=list)
    facets: dict[str, list[dict]] = Field(default_factory=dict)


# ==================== 数据源适配器 ====================


class BaseSearchAdapter:
    """搜索数据源适配器基类"""

    @property
    def source_type(self) -> DataSource:
        raise NotImplementedError

    @property
    def adapter_name(self) -> str:
        """MCP 适配器名称，子类可覆盖"""
        return ""

    @property
    def title_field(self) -> str:
        """结果中的标题字段名"""
        return "title"

    @property
    def content_field(self) -> str:
        """结果中的内容字段名"""
        return "content"

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        raise NotImplementedError

    async def _mcp_search(self, request: SearchRequest) -> list[SearchHit]:
        """通用 MCP 适配器搜索逻辑

        通过 MCP 适配器发送搜索请求并解析结果，消除各数据源适配器的重复代码。
        子类只需定义 adapter_name、title_field、content_field 即可复用。

        Args:
            request: 搜索请求

        Returns:
            搜索结果列表
        """
        try:
            from agent.adapters.mcp_adapters import get_adapter
            adapter = get_adapter(self.adapter_name)
            if not adapter:
                return []

            resp = await adapter._request("GET", "/api/search", params={
                "q": request.query,
                "limit": request.limit,
                "user_id": request.user_id,
            })
            if not resp.success:
                return []

            return self._parse_results(resp.data.get("results", []))
        except Exception as e:
            logger.debug("%s 搜索适配器异常: %s", self.source_type.value, e)
            return []

    def _parse_results(self, items: list[dict[str, Any]]) -> list[SearchHit]:
        """解析搜索结果

        Args:
            items: 原始结果列表

        Returns:
            SearchHit 列表
        """
        hits = []
        for item in items:
            hits.append(SearchHit(
                source=self.source_type,
                title=item.get(self.title_field, ""),
                content=item.get(self.content_field, "")[:500],
                url=item.get("url", ""),
                score=item.get("score", 0.5),
                highlights=item.get("highlights", []),
            ))
        return hits


class DocumentSearchAdapter(BaseSearchAdapter):
    """文档库搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.DOCUMENTS

    @property
    def adapter_name(self) -> str:
        return "doc"

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        return await self._mcp_search(request)


class OASearchAdapter(BaseSearchAdapter):
    """OA 系统搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.OA

    @property
    def adapter_name(self) -> str:
        return "oa"

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        return await self._mcp_search(request)


class EmailSearchAdapter(BaseSearchAdapter):
    """邮件搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.EMAIL

    @property
    def adapter_name(self) -> str:
        return "email"

    @property
    def title_field(self) -> str:
        return "subject"

    @property
    def content_field(self) -> str:
        return "body"

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        try:
            from agent.adapters.mcp_adapters import get_adapter
            email_adapter = get_adapter("email")
            if not email_adapter:
                return []

            resp = await email_adapter.search_mails(
                user_id=request.user_id,
                query=request.query,
                page_size=request.limit,
            )
            if not resp.success:
                return []

            items = resp.data.get("results", resp.data if isinstance(resp.data, list) else [])
            return self._parse_results([i for i in items if isinstance(i, dict)])
        except Exception as e:
            logger.debug("邮件搜索适配器异常: %s", e)
            return []


class CalendarSearchAdapter(BaseSearchAdapter):
    """日历搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.CALENDAR

    @property
    def adapter_name(self) -> str:
        return "calendar"

    @property
    def content_field(self) -> str:
        return "description"

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        return await self._mcp_search(request)


class CRMSearchAdapter(BaseSearchAdapter):
    """CRM 搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.CRM

    @property
    def adapter_name(self) -> str:
        return "crm"

    @property
    def title_field(self) -> str:
        return "name"

    @property
    def content_field(self) -> str:
        return "description"

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        return await self._mcp_search(request)


# ==================== 搜索引擎 ====================


_adapters: list[BaseSearchAdapter] = []


def _init_adapters() -> None:
    """初始化搜索适配器"""
    if _adapters:
        return
    _adapters.extend([
        DocumentSearchAdapter(),
        OASearchAdapter(),
        EmailSearchAdapter(),
        CalendarSearchAdapter(),
        CRMSearchAdapter(),
    ])


async def enterprise_search(request: SearchRequest) -> SearchResponse:
    """执行企业搜索

    联邦搜索策略：并行查询多个数据源，合并排序返回。
    根据搜索类型选择不同的搜索方式：
      - KEYWORD: 仅关键词搜索
      - SEMANTIC: 仅语义搜索（基于向量嵌入）
      - HYBRID: 关键词 + 语义搜索融合，取并集后按混合分数排序

    Args:
        request: 搜索请求

    Returns:
        SearchResponse
    """
    _init_adapters()
    start = time.time()

    sources = request.data_sources
    if DataSource.ALL in sources:
        active_adapters = _adapters
    else:
        active_adapters = [a for a in _adapters if a.source_type in sources]

    if request.search_type == SearchType.KEYWORD:
        all_hits = await _keyword_search(active_adapters, request)
    elif request.search_type == SearchType.SEMANTIC:
        all_hits = await _semantic_search(active_adapters, request)
    elif request.search_type == SearchType.HYBRID:
        keyword_hits, semantic_hits = await asyncio.gather(
            _keyword_search(active_adapters, request),
            _semantic_search(active_adapters, request),
        )
        all_hits = _merge_hybrid_results(keyword_hits, semantic_hits)
    else:
        all_hits = await _keyword_search(active_adapters, request)

    all_hits.sort(key=lambda h: h.score, reverse=True)

    total = len(all_hits)
    paginated = all_hits[request.offset:request.offset + request.limit]

    facets = _compute_facets(all_hits)

    suggestions = _generate_suggestions(request.query, all_hits)

    latency = (time.time() - start) * 1000

    return SearchResponse(
        query=request.query,
        total=total,
        hits=paginated,
        search_type=request.search_type,
        latency_ms=round(latency, 2),
        suggestions=suggestions,
        facets=facets,
    )


async def _keyword_search(
    adapters: list[BaseSearchAdapter],
    request: SearchRequest,
) -> list[SearchHit]:
    """关键词搜索：通过 MCP 适配器查询各数据源"""
    tasks = [adapter.search(request) for adapter in adapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    hits: list[SearchHit] = []
    for result in results:
        if isinstance(result, list):
            hits.extend(result)
    return hits


async def _semantic_search(
    adapters: list[BaseSearchAdapter],
    request: SearchRequest,
) -> list[SearchHit]:
    """语义搜索：基于向量嵌入的相似度搜索

    通过 LLM 生成查询向量，然后在各数据源中执行向量相似度检索。
    如果数据源不支持语义搜索，则回退到关键词搜索并降低权重。
    """
    query_embedding = await _get_query_embedding(request.query)
    if not query_embedding:
        return []

    tasks = [_semantic_adapter_search(adapter, request, query_embedding) for adapter in adapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    hits: list[SearchHit] = []
    for result in results:
        if isinstance(result, list):
            hits.extend(result)
    return hits


async def _semantic_adapter_search(
    adapter: BaseSearchAdapter,
    request: SearchRequest,
    query_embedding: list[float],
) -> list[SearchHit]:
    """对单个适配器执行语义搜索

    优先使用数据源的向量搜索接口，不支持时回退到关键词搜索并降权。
    """
    try:
        from agent.adapters.mcp_adapters import get_adapter
        mcp_adapter = get_adapter(adapter.adapter_name)
        if not mcp_adapter:
            return []

        resp = await mcp_adapter._request("POST", "/api/search/semantic", json={
            "query": request.query,
            "query_embedding": query_embedding,
            "limit": request.limit,
            "user_id": request.user_id,
        })
        if resp.success:
            return adapter._parse_results(resp.data.get("results", []))
    except Exception:
        pass

    fallback_hits = await adapter.search(request)
    for hit in fallback_hits:
        hit.score *= 0.6
    return fallback_hits


async def _get_query_embedding(query: str) -> list[float]:
    """获取查询文本的向量嵌入

    通过配置的 LLM 嵌入模型生成查询向量。
    """
    try:
        from agent.core.config import get_settings
        settings = get_settings()

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.embedding_api_url,
                json={"input": query, "model": settings.embedding_model},
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [{}])[0].get("embedding", [])
    except Exception as e:
        logger.debug("获取查询嵌入失败: %s", e)
    return []


def _merge_hybrid_results(
    keyword_hits: list[SearchHit],
    semantic_hits: list[SearchHit],
) -> list[SearchHit]:
    """融合关键词搜索和语义搜索结果

    使用 Reciprocal Rank Fusion (RRF) 算法合并两路结果，
    对同一文档取较高分数，避免重复。

    Args:
        keyword_hits: 关键词搜索结果
        semantic_hits: 语义搜索结果

    Returns:
        合并后的结果列表
    """
    KEYWORD_WEIGHT = 0.5
    SEMANTIC_WEIGHT = 0.5
    RRF_K = 60

    score_map: dict[str, float] = {}
    hit_map: dict[str, SearchHit] = {}

    for rank, hit in enumerate(sorted(keyword_hits, key=lambda h: h.score, reverse=True)):
        key = f"{hit.source.value}:{hit.url or hit.title}"
        rrf_score = KEYWORD_WEIGHT / (RRF_K + rank + 1)
        score_map[key] = score_map.get(key, 0) + rrf_score
        hit_map[key] = hit

    for rank, hit in enumerate(sorted(semantic_hits, key=lambda h: h.score, reverse=True)):
        key = f"{hit.source.value}:{hit.url or hit.title}"
        rrf_score = SEMANTIC_WEIGHT / (RRF_K + rank + 1)
        score_map[key] = score_map.get(key, 0) + rrf_score
        if key not in hit_map:
            hit_map[key] = hit

    merged: list[SearchHit] = []
    for key, hit in hit_map.items():
        merged.append(SearchHit(
            source=hit.source,
            title=hit.title,
            content=hit.content,
            url=hit.url,
            score=score_map[key],
            highlights=hit.highlights,
            metadata=hit.metadata,
        ))

    return merged


def _compute_facets(hits: list[SearchHit]) -> dict[str, list[dict]]:
    """计算分面统计"""
    source_counts: dict[str, int] = {}
    for hit in hits:
        key = hit.source.value
        source_counts[key] = source_counts.get(key, 0) + 1

    return {
        "source": [{"name": k, "count": v} for k, v in sorted(source_counts.items(), key=lambda x: -x[1])],
    }


def _generate_suggestions(query: str, hits: list[SearchHit]) -> list[str]:
    """生成搜索建议"""
    suggestions: list[str] = []
    if len(query) < 2:
        return suggestions

    seen_titles: set[str] = set()
    for hit in hits[:5]:
        if hit.title and hit.title not in seen_titles:
            suggestions.append(hit.title)
            seen_titles.add(hit.title)

    return suggestions[:3]
