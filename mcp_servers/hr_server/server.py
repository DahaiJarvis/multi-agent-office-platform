"""HR 人事系统 MCP 服务

提供考勤查询、请假申请、薪资查询、员工信息查询等工具，
通过 MCP 协议供 Agent 调用，底层对接企业 HR 系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("hr-mcp-server", host="0.0.0.0", port=9008)

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("HR")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_attendance(
    user_id: str,
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询考勤记录

    根据用户ID和日期范围查询考勤记录，包括打卡时间、状态等。

    Args:
        user_id: 用户ID
        date_from: 起始日期，格式 YYYY-MM-DD，为空则默认当月1号
        date_to: 截止日期，格式 YYYY-MM-DD，为空则默认今天
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        考勤记录列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"user_id": user_id, "page": page, "page_size": page_size}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    result = await client.get("/attendance", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询考勤记录失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def submit_leave_request(
    user_id: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    reason: str = "",
) -> str:
    """提交请假申请

    提交一条请假申请，此为敏感操作需确认后执行。

    Args:
        user_id: 用户ID
        leave_type: 请假类型，annual(年假)/sick(病假)/personal(事假)/maternity(产假)/marriage(婚假)
        start_date: 开始日期，格式 YYYY-MM-DD
        end_date: 结束日期，格式 YYYY-MM-DD
        reason: 请假原因

    Returns:
        申请结果 JSON 字符串
    """
    valid_types = ("annual", "sick", "personal", "maternity", "marriage")
    if leave_type not in valid_types:
        return format_result(False, error=f"不支持的请假类型: {leave_type}，可选 {valid_types}")

    client = _get_api_client()
    payload: dict[str, Any] = {
        "user_id": user_id,
        "leave_type": leave_type,
        "start_date": start_date,
        "end_date": end_date,
    }
    if reason:
        payload["reason"] = reason

    result = await client.post("/leaves", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "提交请假申请失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_leave_balance(user_id: str) -> str:
    """查询假期余额

    查询指定用户的各类假期剩余天数。

    Args:
        user_id: 用户ID

    Returns:
        假期余额 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get(f"/leaves/balance/{user_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询假期余额失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_salary(
    user_id: str,
    month: str = "",
) -> str:
    """查询薪资信息

    查询指定月份的薪资详情，此为敏感数据需权限校验。

    Args:
        user_id: 用户ID
        month: 月份，格式 YYYY-MM，为空则查询最近一个月

    Returns:
        薪资信息 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"user_id": user_id}
    if month:
        params["month"] = month

    result = await client.get("/salary", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询薪资信息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_employee_info(user_id: str) -> str:
    """查询员工基本信息

    查询指定员工的基本信息，包括姓名、部门、职位、入职日期等。

    Args:
        user_id: 用户ID或工号

    Returns:
        员工信息 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get(f"/employees/{user_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询员工信息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_department_members(
    department: str,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询部门成员列表

    查询指定部门下的所有成员。

    Args:
        department: 部门名称
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        部门成员列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"department": department, "page": page, "page_size": page_size}
    result = await client.get("/employees", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询部门成员失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
