"""日历系统 MCP 服务

提供日程查询、会议创建、日程更新等工具，
通过 MCP 协议供 Agent 调用，底层对接企业日历系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("calendar-mcp-server", host="0.0.0.0", port=9003)

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("CALENDAR")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_events(
    date_from: str = "",
    date_to: str = "",
    event_type: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询日程列表

    根据筛选条件查询日程，支持按日期范围、类型、关键词筛选。

    Args:
        date_from: 起始日期，格式 YYYY-MM-DD，为空则从今天开始
        date_to: 截止日期，格式 YYYY-MM-DD，为空则默认7天后
        event_type: 日程类型，meeting(会议)/reminder(提醒)/holiday(假期)，为空则查全部
        keyword: 搜索关键词，匹配日程标题
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        日程列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if event_type:
        params["type"] = event_type
    if keyword:
        params["keyword"] = keyword

    result = await client.get("/events", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询日程列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_event_detail(event_id: str) -> str:
    """获取日程详情

    根据日程ID获取日程的完整信息，包括时间、地点、参会人等。

    Args:
        event_id: 日程ID

    Returns:
        日程详情 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get(f"/events/{event_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取日程详情失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def create_event(
    title: str,
    start_time: str,
    end_time: str,
    location: str = "",
    description: str = "",
    attendees: str = "",
    event_type: str = "meeting",
) -> str:
    """创建日程/会议

    创建一个新的日程或会议，创建会议时可指定参会人。

    Args:
        title: 日程标题
        start_time: 开始时间，格式 YYYY-MM-DD HH:MM
        end_time: 结束时间，格式 YYYY-MM-DD HH:MM
        location: 会议地点
        description: 日程描述
        attendees: 参会人邮箱，多个用逗号分隔
        event_type: 日程类型，meeting(会议)/reminder(提醒)，默认meeting

    Returns:
        创建结果 JSON 字符串
    """
    if event_type not in ("meeting", "reminder"):
        return format_result(False, error=f"不支持的日程类型: {event_type}，可选 meeting/reminder")

    client = _get_api_client()
    payload: dict[str, Any] = {
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "type": event_type,
    }
    if location:
        payload["location"] = location
    if description:
        payload["description"] = description
    if attendees:
        payload["attendees"] = [a.strip() for a in attendees.split(",")]

    result = await client.post("/events", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "创建日程失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def update_event(
    event_id: str,
    title: str = "",
    start_time: str = "",
    end_time: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """更新日程

    更新已有日程的信息，仅传入需要修改的字段。

    Args:
        event_id: 日程ID
        title: 新标题，为空则不修改
        start_time: 新开始时间，格式 YYYY-MM-DD HH:MM，为空则不修改
        end_time: 新结束时间，格式 YYYY-MM-DD HH:MM，为空则不修改
        location: 新地点，为空则不修改
        description: 新描述，为空则不修改

    Returns:
        更新结果 JSON 字符串
    """
    client = _get_api_client()
    payload: dict[str, Any] = {}
    if title:
        payload["title"] = title
    if start_time:
        payload["start_time"] = start_time
    if end_time:
        payload["end_time"] = end_time
    if location:
        payload["location"] = location
    if description:
        payload["description"] = description

    if not payload:
        return format_result(False, error="未指定需要更新的字段")

    result = await client.put(f"/events/{event_id}", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "更新日程失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def cancel_event(event_id: str, reason: str = "") -> str:
    """取消日程

    取消指定的日程或会议，此为敏感操作需确认后执行。

    Args:
        event_id: 日程ID
        reason: 取消原因

    Returns:
        取消结果 JSON 字符串
    """
    client = _get_api_client()
    payload: dict[str, Any] = {}
    if reason:
        payload["reason"] = reason

    result = await client.post(f"/events/{event_id}/cancel", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "取消日程失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def check_time_conflict(
    start_time: str,
    end_time: str,
    user_id: str = "",
) -> str:
    """检查时间冲突

    检查指定时间段是否与已有日程冲突。

    Args:
        start_time: 开始时间，格式 YYYY-MM-DD HH:MM
        end_time: 结束时间，格式 YYYY-MM-DD HH:MM
        user_id: 用户ID，为空则检查当前用户

    Returns:
        冲突检查结果 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"start_time": start_time, "end_time": end_time}
    if user_id:
        params["user_id"] = user_id

    result = await client.get("/events/conflict", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "检查时间冲突失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
