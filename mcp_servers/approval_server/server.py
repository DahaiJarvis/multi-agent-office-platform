"""审批系统 MCP 服务

提供审批流程管理、审批统计、批量审批等工具，
通过 MCP 协议供 Agent 调用，底层对接企业审批系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, is_mock_mode, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("approval-mcp-server", host="0.0.0.0", port=9005)

# Mock 数据
MOCK_DATA = {
    "approval_flows": [
        {"id": "FLOW-001", "name": "请假审批流", "steps": 3, "description": "员工请假审批流程", "status": "active", "applicable_types": ["annual", "sick", "personal"]},
        {"id": "FLOW-002", "name": "报销审批流", "steps": 4, "description": "费用报销审批流程", "status": "active", "applicable_types": ["travel", "office", "meal"]},
        {"id": "FLOW-003", "name": "采购审批流", "steps": 3, "description": "物资采购审批流程", "status": "active", "applicable_types": ["purchase"]},
        {"id": "FLOW-004", "name": "合同审批流", "steps": 5, "description": "合同签署审批流程", "status": "active", "applicable_types": ["contract"]},
        {"id": "FLOW-005", "name": "出差审批流", "steps": 2, "description": "出差申请审批流程", "status": "inactive", "applicable_types": ["travel"]},
    ],
    "approval_stats": {
        "total": 156, "pending": 12, "approved": 130, "rejected": 14,
        "avg_process_time": "2.3天",
        "by_type": {"leave": 45, "expense": 52, "purchase": 23, "travel": 18, "contract": 12, "overtime": 6},
        "by_month": [
            {"month": "2026-01", "count": 28}, {"month": "2026-02", "count": 22},
            {"month": "2026-03", "count": 35}, {"month": "2026-04", "count": 31},
            {"month": "2026-05", "count": 40},
        ],
    },
}

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("APPROVAL")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_approval_flows(
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询审批流程列表

    获取系统中所有可用的审批流程配置。

    Args:
        status: 流程状态筛选，active/inactive，为空则查全部
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        审批流程列表 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["approval_flows"], "total": len(MOCK_DATA["approval_flows"]), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if status:
        params["status"] = status

    result = await client.get("/approval-flows", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询审批流程失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_approval_stats() -> str:
    """获取审批统计信息

    获取当前用户的审批统计数据，包括待处理数、已处理数等。

    Returns:
        审批统计信息 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["approval_stats"])

    client = _get_api_client()
    result = await client.get("/approval-stats")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取审批统计失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def batch_approve(
    approval_ids: str,
    action: str,
    comment: str = "",
) -> str:
    """批量审批操作

    对多个审批单执行相同的审批操作，此为敏感操作需确认后执行。

    Args:
        approval_ids: 审批单ID列表，多个用逗号分隔
        action: 审批操作，approve(同意)/reject(拒绝)
        comment: 审批意见

    Returns:
        批量审批结果 JSON 字符串
    """
    if action not in ("approve", "reject"):
        return format_result(False, error=f"不支持的审批操作: {action}，可选 approve/reject")

    if is_mock_mode():
        ids = approval_ids.split(",")
        return format_result(True, data={"total": len(ids), "success_count": len(ids), "failed_count": 0, "action": action})

    client = _get_api_client()
    payload: dict[str, Any] = {
        "approval_ids": [aid.strip() for aid in approval_ids.split(",")],
        "action": action,
        "comment": comment,
    }
    result = await client.post("/approvals/batch-action", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "批量审批操作失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
