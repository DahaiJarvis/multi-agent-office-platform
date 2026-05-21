"""HR 人事系统 MCP 服务

提供考勤查询、请假申请、薪资查询、员工信息查询等工具，
通过 MCP 协议供 Agent 调用，底层对接企业 HR 系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, is_mock_mode, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("hr-mcp-server", host="0.0.0.0", port=9008)

# Mock 数据
MOCK_DATA = {
    "attendance": [
        {"date": "2026-05-01", "clock_in": "08:55", "clock_out": "18:10", "status": "normal"},
        {"date": "2026-05-02", "clock_in": "09:15", "clock_out": "18:05", "status": "late"},
        {"date": "2026-05-05", "clock_in": "08:50", "clock_out": "18:30", "status": "normal"},
        {"date": "2026-05-06", "clock_in": "08:58", "clock_out": "18:15", "status": "normal"},
        {"date": "2026-05-07", "clock_in": "09:22", "clock_out": "18:00", "status": "late"},
        {"date": "2026-05-08", "clock_in": "08:45", "clock_out": "19:00", "status": "normal"},
        {"date": "2026-05-09", "clock_in": "08:52", "clock_out": "18:20", "status": "normal"},
        {"date": "2026-05-12", "clock_in": "08:48", "clock_out": "18:10", "status": "normal"},
        {"date": "2026-05-13", "clock_in": "--", "clock_out": "--", "status": "absent"},
        {"date": "2026-05-14", "clock_in": "08:55", "clock_out": "18:05", "status": "normal"},
        {"date": "2026-05-15", "clock_in": "09:05", "clock_out": "18:30", "status": "late"},
        {"date": "2026-05-16", "clock_in": "08:50", "clock_out": "18:00", "status": "normal"},
        {"date": "2026-05-19", "clock_in": "08:57", "clock_out": "18:25", "status": "normal"},
        {"date": "2026-05-20", "clock_in": "08:53", "clock_out": "18:10", "status": "normal"},
    ],
    "attendance_summary": {
        "total_days": 20,
        "normal_days": 15,
        "late_days": 3,
        "absent_days": 1,
        "leave_days": 1,
        "overtime_hours": 8.5,
    },
    "leave_balance": {
        "annual": 5, "annual_used": 3, "annual_total": 8,
        "sick": 10, "sick_used": 0, "sick_total": 10,
        "personal": 3, "personal_used": 1, "personal_total": 4,
        "maternity": 0, "maternity_used": 0, "maternity_total": 0,
        "marriage": 0, "marriage_used": 0, "marriage_total": 0,
    },
    "leave_records": [
        {"id": "LEAVE-2026-001", "user_id": "EMP-001", "leave_type": "annual", "start_date": "2026-04-28", "end_date": "2026-04-30", "days": 3, "reason": "回老家探亲", "status": "approved"},
        {"id": "LEAVE-2026-002", "user_id": "EMP-001", "leave_type": "personal", "start_date": "2026-05-13", "end_date": "2026-05-13", "days": 1, "reason": "家中有事", "status": "approved"},
        {"id": "LEAVE-2026-003", "user_id": "EMP-002", "leave_type": "sick", "start_date": "2026-05-08", "end_date": "2026-05-09", "days": 2, "reason": "感冒发烧", "status": "approved"},
        {"id": "LEAVE-2026-004", "user_id": "EMP-003", "leave_type": "annual", "start_date": "2026-05-19", "end_date": "2026-05-23", "days": 5, "reason": "旅游休假", "status": "pending"},
    ],
    "employee_info": {
        "id": "EMP-001", "name": "张三", "department": "技术部", "position": "高级工程师",
        "email": "zhangsan@company.com", "phone": "13800001111",
        "join_date": "2022-03-15", "manager": "赵主管",
        "work_years": 4, "level": "P7",
    },
    "department_members": [
        {"id": "EMP-001", "name": "张三", "position": "高级工程师", "email": "zhangsan@company.com", "status": "active"},
        {"id": "EMP-002", "name": "李四", "position": "产品经理", "email": "lisi@company.com", "status": "active"},
        {"id": "EMP-003", "name": "王五", "position": "测试工程师", "email": "wangwu@company.com", "status": "active"},
        {"id": "EMP-004", "name": "赵主管", "position": "技术经理", "email": "zhaozg@company.com", "status": "active"},
        {"id": "EMP-005", "name": "钱经理", "position": "技术总监", "email": "qianjl@company.com", "status": "active"},
        {"id": "EMP-006", "name": "孙六", "position": "前端工程师", "email": "sunliu@company.com", "status": "active"},
        {"id": "EMP-007", "name": "周七", "position": "后端工程师", "email": "zhouqi@company.com", "status": "active"},
        {"id": "EMP-008", "name": "吴八", "position": "运维工程师", "email": "wuba@company.com", "status": "leave"},
    ],
    "salary": {
        "month": "2026-04", "base": "25000", "bonus": "5000",
        "overtime_pay": "1200", "deduction": "1500",
        "social_insurance": "2800", "housing_fund": "3000",
        "tax": "1850", "net": "22050",
    },
    "salary_history": [
        {"month": "2026-03", "base": "25000", "bonus": "4500", "net": "21850"},
        {"month": "2026-02", "base": "25000", "bonus": "6000", "net": "23100"},
        {"month": "2026-01", "base": "25000", "bonus": "5000", "net": "22050"},
        {"month": "2025-12", "base": "25000", "bonus": "8000", "net": "24800"},
    ],
    "department_attendance": {
        "department": "技术部", "month": "2026-05",
        "total_members": 8, "attendance_rate": "95.2%",
        "late_count": 5, "absent_count": 1, "leave_count": 3,
        "top_leaver": {"name": "王五", "leave_days": 5},
    },
}

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
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["attendance"], "total": len(MOCK_DATA["attendance"]), "page": page, "page_size": page_size})

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

    if is_mock_mode():
        return format_result(True, data={"leave_id": "LEAVE-NEW-001", "user_id": user_id, "leave_type": leave_type, "start_date": start_date, "end_date": end_date, "status": "submitted"})

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
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["leave_balance"])

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
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["salary"])

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
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["employee_info"])

    client = _get_api_client()
    result = await client.get(f"/employees/{user_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询员工信息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_attendance_summary(
    user_id: str,
    month: str = "",
) -> str:
    """查询考勤统计汇总

    查询指定月份的考勤统计信息，包括正常出勤天数、迟到天数、缺勤天数等。

    Args:
        user_id: 用户ID
        month: 月份，格式 YYYY-MM，为空则查询当月

    Returns:
        考勤统计 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["attendance_summary"])

    client = _get_api_client()
    params: dict[str, Any] = {"user_id": user_id}
    if month:
        params["month"] = month

    result = await client.get("/attendance/summary", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询考勤统计失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_leave_records(
    user_id: str = "",
    leave_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询请假记录列表

    根据筛选条件查询请假记录，支持按用户、类型、状态筛选。

    Args:
        user_id: 用户ID，为空则查全部
        leave_type: 请假类型，annual/sick/personal/maternity/marriage，为空则查全部
        status: 审批状态，pending/approved/rejected，为空则查全部
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        请假记录列表 JSON 字符串
    """
    if is_mock_mode():
        records = MOCK_DATA["leave_records"]
        if user_id:
            records = [r for r in records if r["user_id"] == user_id]
        if leave_type:
            records = [r for r in records if r["leave_type"] == leave_type]
        if status:
            records = [r for r in records if r["status"] == status]
        return format_result(True, data={"items": records, "total": len(records), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if user_id:
        params["user_id"] = user_id
    if leave_type:
        params["leave_type"] = leave_type
    if status:
        params["status"] = status

    result = await client.get("/leaves", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询请假记录失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_department_attendance(
    department: str,
    month: str = "",
) -> str:
    """查询部门考勤汇总

    查询指定部门的考勤汇总信息，包括出勤率、迟到人数、请假人数等。

    Args:
        department: 部门名称
        month: 月份，格式 YYYY-MM，为空则查询当月

    Returns:
        部门考勤汇总 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["department_attendance"])

    client = _get_api_client()
    params: dict[str, Any] = {"department": department}
    if month:
        params["month"] = month

    result = await client.get("/attendance/department", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询部门考勤汇总失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_salary_history(
    user_id: str,
    months: int = 6,
) -> str:
    """查询薪资历史记录

    查询指定用户最近几个月的薪资历史。

    Args:
        user_id: 用户ID
        months: 查询月数，默认6

    Returns:
        薪资历史 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["salary_history"][:months], "total": min(months, len(MOCK_DATA["salary_history"]))})

    client = _get_api_client()
    params: dict[str, Any] = {"user_id": user_id, "months": months}
    result = await client.get("/salary/history", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询薪资历史失败"))

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
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["department_members"], "total": len(MOCK_DATA["department_members"]), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"department": department, "page": page, "page_size": page_size}
    result = await client.get("/employees", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询部门成员失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
