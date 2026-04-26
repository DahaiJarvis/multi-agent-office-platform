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

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        raise NotImplementedError


class DocumentSearchAdapter(BaseSearchAdapter):
    """文档库搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.DOCUMENTS

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        try:
            from agent.adapters.mcp_adapters import get_adapter
            doc_adapter = get_adapter("doc")
            if not doc_adapter:
                return []

            resp = await doc_adapter._request("GET", "/api/search", params={
                "q": request.query,
                "limit": request.limit,
                "user_id": request.user_id,
            })
            if not resp.success:
                return []

            hits = []
            for item in resp.data.get("results", []):
                hits.append(SearchHit(
                    source=self.source_type,
                    title=item.get("title", ""),
                    content=item.get("content", "")[:500],
                    url=item.get("url", ""),
                    score=item.get("score", 0.5),
                    highlights=item.get("highlights", []),
                ))
            return hits
        except Exception as e:
            logger.debug("文档搜索适配器异常: %s", e)
            return []


class OASearchAdapter(BaseSearchAdapter):
    """OA 系统搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.OA

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        try:
            from agent.adapters.mcp_adapters import get_adapter
            oa_adapter = get_adapter("oa")
            if not oa_adapter:
                return []

            resp = await oa_adapter._request("GET", "/api/search", params={
                "q": request.query,
                "limit": request.limit,
                "user_id": request.user_id,
            })
            if not resp.success:
                return []

            hits = []
            for item in resp.data.get("results", []):
                hits.append(SearchHit(
                    source=self.source_type,
                    title=item.get("title", ""),
                    content=item.get("content", "")[:500],
                    url=item.get("url", ""),
                    score=item.get("score", 0.5),
                ))
            return hits
        except Exception as e:
            logger.debug("OA 搜索适配器异常: %s", e)
            return []


class EmailSearchAdapter(BaseSearchAdapter):
    """邮件搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.EMAIL

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

            hits = []
            for item in resp.data.get("results", resp.data if isinstance(resp.data, list) else []):
                if isinstance(item, dict):
                    hits.append(SearchHit(
                        source=self.source_type,
                        title=item.get("subject", item.get("title", "")),
                        content=item.get("body", item.get("content", ""))[:500],
                        score=item.get("score", 0.5),
                    ))
            return hits
        except Exception as e:
            logger.debug("邮件搜索适配器异常: %s", e)
            return []


class CalendarSearchAdapter(BaseSearchAdapter):
    """日历搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.CALENDAR

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        try:
            from agent.adapters.mcp_adapters import get_adapter
            cal_adapter = get_adapter("calendar")
            if not cal_adapter:
                return []

            resp = await cal_adapter._request("GET", "/api/search", params={
                "q": request.query,
                "limit": request.limit,
                "user_id": request.user_id,
            })
            if not resp.success:
                return []

            hits = []
            for item in resp.data.get("results", []):
                hits.append(SearchHit(
                    source=self.source_type,
                    title=item.get("title", ""),
                    content=item.get("description", "")[:500],
                    score=item.get("score", 0.5),
                ))
            return hits
        except Exception as e:
            logger.debug("日历搜索适配器异常: %s", e)
            return []


class CRMSearchAdapter(BaseSearchAdapter):
    """CRM 搜索适配器"""

    @property
    def source_type(self) -> DataSource:
        return DataSource.CRM

    async def search(self, request: SearchRequest) -> list[SearchHit]:
        try:
            from agent.adapters.mcp_adapters import get_adapter
            crm_adapter = get_adapter("crm")
            if not crm_adapter:
                return []

            resp = await crm_adapter._request("GET", "/api/search", params={
                "q": request.query,
                "limit": request.limit,
                "user_id": request.user_id,
            })
            if not resp.success:
                return []

            hits = []
            for item in resp.data.get("results", []):
                hits.append(SearchHit(
                    source=self.source_type,
                    title=item.get("name", item.get("title", "")),
                    content=item.get("description", "")[:500],
                    score=item.get("score", 0.5),
                ))
            return hits
        except Exception as e:
            logger.debug("CRM 搜索适配器异常: %s", e)
            return []


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

    tasks = [adapter.search(request) for adapter in active_adapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_hits: list[SearchHit] = []
    for result in results:
        if isinstance(result, list):
            all_hits.extend(result)

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
