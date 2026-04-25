"""文档系统 MCP 服务

提供文档查询、文档创建、文档搜索、文档权限管理等工具，
通过 MCP 协议供 Agent 调用，底层对接企业文档管理系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("doc-mcp-server", host="0.0.0.0", port=9007)

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("DOC")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def search_documents(
    keyword: str,
    doc_type: str = "",
    department: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """搜索文档

    根据关键词搜索企业文档，支持按类型和部门筛选。

    Args:
        keyword: 搜索关键词
        doc_type: 文档类型，policy(制度)/report(报告)/template(模板)/meeting_minutes(会议纪要)/other(其他)
        department: 所属部门
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        文档搜索结果 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"keyword": keyword, "page": page, "page_size": page_size}
    if doc_type:
        params["type"] = doc_type
    if department:
        params["department"] = department

    result = await client.get("/documents/search", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "搜索文档失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_document_detail(doc_id: str) -> str:
    """获取文档详情

    根据文档ID获取文档的完整信息，包括标题、内容摘要、作者、权限等。

    Args:
        doc_id: 文档ID

    Returns:
        文档详情 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get(f"/documents/{doc_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取文档详情失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def create_document(
    title: str,
    content: str,
    doc_type: str = "other",
    department: str = "",
    tags: str = "",
) -> str:
    """创建文档

    创建一篇新的企业文档。

    Args:
        title: 文档标题
        content: 文档内容
        doc_type: 文档类型，policy/report/template/meeting_minutes/other，默认other
        department: 所属部门
        tags: 标签，多个用逗号分隔

    Returns:
        创建结果 JSON 字符串
    """
    valid_types = ("policy", "report", "template", "meeting_minutes", "other")
    if doc_type not in valid_types:
        return format_result(False, error=f"不支持的文档类型: {doc_type}，可选 {valid_types}")

    client = _get_api_client()
    payload: dict[str, Any] = {
        "title": title,
        "content": content,
        "type": doc_type,
    }
    if department:
        payload["department"] = department
    if tags:
        payload["tags"] = [t.strip() for t in tags.split(",")]

    result = await client.post("/documents", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "创建文档失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def list_recent_documents(
    doc_type: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询最近更新的文档

    获取最近更新或创建的文档列表。

    Args:
        doc_type: 文档类型筛选，为空则查全部
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        文档列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if doc_type:
        params["type"] = doc_type

    result = await client.get("/documents/recent", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询最近文档失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def share_document(
    doc_id: str,
    target_users: str,
    permission: str = "read",
) -> str:
    """分享文档

    将文档分享给指定用户，此为敏感操作需确认后执行。

    Args:
        doc_id: 文档ID
        target_users: 目标用户ID，多个用逗号分隔
        permission: 权限级别，read(只读)/edit(编辑)/admin(管理)，默认read

    Returns:
        分享结果 JSON 字符串
    """
    valid_permissions = ("read", "edit", "admin")
    if permission not in valid_permissions:
        return format_result(False, error=f"不支持的权限级别: {permission}，可选 {valid_permissions}")

    client = _get_api_client()
    payload: dict[str, Any] = {
        "target_users": [u.strip() for u in target_users.split(",")],
        "permission": permission,
    }

    result = await client.post(f"/documents/{doc_id}/share", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "分享文档失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
