"""原生工具 API 路由

提供原生工具的只读查询接口，包括：
  - 列出所有原生工具（支持分类和延迟分层过滤）
  - 获取原生工具详情
  - 列出工具分类

原生工具由平台提供，不支持用户创建/修改/删除。
"""

import logging

from fastapi import APIRouter

from agent.tools.registry import get_native_tool_registry
from agent.tools.base import LatencyTier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/native-tools", tags=["Native Tools"])


@router.get("", summary="列出所有原生工具")
async def api_list_native_tools(
    category: str | None = None,
    latency_tier: str | None = None,
) -> dict:
    """列出所有原生工具

    支持按分类和延迟分层过滤。

    Args:
        category: 按分类过滤（session/data/document/search/text/report/multimodal/rag/skill/system）
        latency_tier: 按延迟分层过滤（instant/fast/slow/general）
    """
    registry = get_native_tool_registry()

    tier = None
    if latency_tier:
        try:
            tier = LatencyTier(latency_tier)
        except ValueError:
            return {
                "total": 0,
                "tools": [],
                "error": f"无效的延迟分层: {latency_tier}，可选值: {[t.value for t in LatencyTier]}",
            }

    metas = registry.list_tools(category=category, latency_tier=tier)

    tools = []
    for meta in metas:
        tools.append({
            "name": meta.name,
            "display_name": meta.display_name,
            "description": meta.description,
            "category": meta.category,
            "latency_tier": meta.latency_tier.value,
            "permission_level": meta.permission_level.value,
            "timeout_seconds": meta.timeout_seconds,
            "requires_llm": meta.requires_llm,
            "version": meta.version,
            "enabled": meta.enabled,
            "tags": meta.tags,
        })

    return {"total": len(tools), "tools": tools}


@router.get("/categories", summary="列出工具分类")
async def api_list_categories() -> dict:
    """列出所有工具分类"""
    registry = get_native_tool_registry()
    all_metas = registry.list_tools()

    categories: dict[str, int] = {}
    for meta in all_metas:
        cat = meta.category
        categories[cat] = categories.get(cat, 0) + 1

    category_list = [
        {"name": name, "count": count}
        for name, count in sorted(categories.items())
    ]

    return {"total": len(category_list), "categories": category_list}


@router.get("/{tool_name}", summary="获取原生工具详情")
async def api_get_native_tool(tool_name: str) -> dict:
    """获取原生工具详情

    Args:
        tool_name: 工具名称
    """
    registry = get_native_tool_registry()
    meta = registry.get_meta(tool_name)

    if meta is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NATIVE_TOOL_NOT_FOUND, message=f"原生工具 '{tool_name}' 不存在")

    return {
        "name": meta.name,
        "display_name": meta.display_name,
        "description": meta.description,
        "category": meta.category,
        "parameters": meta.parameters,
        "latency_tier": meta.latency_tier.value,
        "permission_level": meta.permission_level.value,
        "timeout_seconds": meta.timeout_seconds,
        "requires_llm": meta.requires_llm,
        "version": meta.version,
        "enabled": meta.enabled,
        "tags": meta.tags,
        "agent_bindings": meta.agent_bindings,
        "examples": meta.examples,
    }
