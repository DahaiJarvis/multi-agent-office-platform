"""企业搜索路由"""

import logging

from fastapi import APIRouter

from agent.core.search_engine import (
    enterprise_search,
    SearchRequest,
    SearchResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["企业搜索"])


@router.post("", response_model=SearchResponse, summary="执行企业搜索")
async def api_search(request: SearchRequest) -> SearchResponse:
    """执行企业搜索

    支持跨数据源统一搜索，包括文档库、OA、邮件、日历、CRM 等。
    """
    return await enterprise_search(request)


@router.get("/sources", summary="列出可搜索数据源")
async def list_search_sources() -> dict:
    """列出可搜索的数据源"""
    return {
        "sources": [
            {"id": "documents", "name": "文档库", "description": "企业文档和知识库"},
            {"id": "oa", "name": "OA系统", "description": "审批单和公告"},
            {"id": "email", "name": "邮件系统", "description": "邮件内容"},
            {"id": "calendar", "name": "日历系统", "description": "日程安排"},
            {"id": "crm", "name": "CRM系统", "description": "客户信息"},
        ]
    }
