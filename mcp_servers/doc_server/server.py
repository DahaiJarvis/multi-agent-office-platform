"""文档系统 MCP 服务

提供文档查询、文档创建、文档搜索、文档权限管理等工具，
通过 MCP 协议供 Agent 调用，底层对接企业文档管理系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, is_mock_mode, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("doc-mcp-server", host="0.0.0.0", port=9007)

# Mock 数据
MOCK_DATA = {
    "documents": [
        {"id": "DOC-001", "title": "2026年度差旅报销制度", "type": "policy", "department": "财务部", "author": "管理员", "updated_at": "2026-03-15 10:00:00", "tags": ["报销", "差旅"]},
        {"id": "DOC-002", "title": "Q1季度运营报告", "type": "report", "department": "运营部", "author": "张三", "updated_at": "2026-04-20 16:30:00", "tags": ["季度报告", "运营"]},
        {"id": "DOC-003", "title": "项目A启动会议纪要", "type": "meeting_minutes", "department": "技术部", "author": "李四", "updated_at": "2026-05-01 14:00:00", "tags": ["会议纪要", "项目A"]},
        {"id": "DOC-004", "title": "技术方案评审模板", "type": "template", "department": "技术部", "author": "管理员", "updated_at": "2026-02-10 09:00:00", "tags": ["模板", "评审"]},
        {"id": "DOC-005", "title": "员工手册2026版", "type": "policy", "department": "人力资源部", "author": "管理员", "updated_at": "2026-01-05 10:00:00", "tags": ["员工手册", "制度"]},
        {"id": "DOC-006", "title": "V2.0产品需求文档", "type": "other", "department": "产品部", "author": "李四", "updated_at": "2026-05-18 11:00:00", "tags": ["产品", "需求"]},
    ],
    "document_detail": {
        "id": "DOC-001", "title": "2026年度差旅报销制度", "type": "policy", "department": "财务部",
        "author": "管理员", "updated_at": "2026-03-15 10:00:00",
        "content": "一、差旅报销标准\n1. 机票：经济舱\n2. 住宿：一线城市500元/天，二线城市300元/天\n3. 餐饮：100元/天\n\n二、报销流程\n1. 出差前提交申请\n2. 出差后7日内提交报销\n3. 主管审批后财务处理\n\n三、注意事项\n1. 超标准需额外审批\n2. 发票需为合规增值税发票",
        "tags": ["报销", "差旅"], "permissions": {"read": ["全员"], "edit": ["财务部"]},
    },
}

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
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["documents"], "total": len(MOCK_DATA["documents"]), "page": page, "page_size": page_size})

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
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["document_detail"])

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

    if is_mock_mode():
        return format_result(True, data={"doc_id": "DOC-NEW-001", "title": title, "type": doc_type, "status": "created"})

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
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["documents"], "total": len(MOCK_DATA["documents"]), "page": page, "page_size": page_size})

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

    if is_mock_mode():
        return format_result(True, data={"doc_id": doc_id, "shared_with": target_users.split(","), "permission": permission, "status": "shared"})

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
