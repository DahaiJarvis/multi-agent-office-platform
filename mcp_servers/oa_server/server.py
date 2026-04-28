"""OA 审批系统 MCP 服务

提供审批查询、审批详情获取、审批操作提交等工具，
通过 MCP 协议供 Agent 调用，底层对接 OA 系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("oa-mcp-server", host="0.0.0.0", port=9001)

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("OA")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_approvals(
    status: str = "",
    approval_type: str = "",
    applicant: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询审批列表

    根据筛选条件查询审批记录，支持按状态、类型、申请人筛选。

    Args:
        status: 审批状态，可选 pending/approved/rejected，为空则查全部
        approval_type: 审批类型，可选 leave/expense/purchase/travel/contract，为空则查全部
        applicant: 申请人姓名或工号，为空则查全部
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        审批列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if status:
        params["status"] = status
    if approval_type:
        params["type"] = approval_type
    if applicant:
        params["applicant"] = applicant

    result = await client.get("/approvals", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询审批列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_approval_detail(approval_id: str) -> str:
    """获取审批详情

    根据审批ID获取审批的完整信息，包括审批内容、审批流程、审批意见等。

    Args:
        approval_id: 审批单ID

    Returns:
        审批详情 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get(f"/approvals/{approval_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取审批详情失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def submit_approval_action(
    approval_id: str,
    action: str,
    comment: str = "",
) -> str:
    """提交审批操作

    对指定审批单执行审批操作，此为敏感操作需确认后执行。

    Args:
        approval_id: 审批单ID
        action: 审批操作，approve(同意)/reject(拒绝)/transfer(转审)
        comment: 审批意见

    Returns:
        操作结果 JSON 字符串
    """
    if action not in ("approve", "reject", "transfer"):
        return format_result(False, error=f"不支持的审批操作: {action}，可选 approve/reject/transfer")

    client = _get_api_client()
    payload = {"action": action, "comment": comment}
    result = await client.post(f"/approvals/{approval_id}/action", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "提交审批操作失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_approval_types() -> str:
    """查询审批类型列表

    获取系统中所有可用的审批类型，如请假、报销、采购等。

    Returns:
        审批类型列表 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get("/approvals/types")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询审批类型失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_my_pending_approvals(user_id: str, page: int = 1, page_size: int = 20) -> str:
    """查询待我审批的列表

    获取指定用户待处理的审批任务列表。

    Args:
        user_id: 用户ID
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        待审批列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"user_id": user_id, "page": page, "page_size": page_size}
    result = await client.get("/approvals/pending", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询待审批列表失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
