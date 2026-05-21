"""IM 消息系统 MCP 服务

提供消息发送、消息查询、群组管理、通知推送等工具，
通过 MCP 协议供 Agent 调用，底层对接企业 IM 系统（企业微信/钉钉）标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, is_mock_mode, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("im-mcp-server", host="0.0.0.0", port=9006)

# Mock 数据
MOCK_DATA = {
    "messages": [
        {"id": "MSG-2026-001", "chat_id": "CHAT-001", "sender": "张三", "content": "下午的会议改到3点了", "time": "2026-05-20 11:30:00", "msg_type": "text"},
        {"id": "MSG-2026-002", "chat_id": "CHAT-001", "sender": "李四", "content": "收到，我会准时参加", "time": "2026-05-20 11:32:00", "msg_type": "text"},
        {"id": "MSG-2026-003", "chat_id": "CHAT-001", "sender": "王五", "content": "测试报告已上传到文档中心", "time": "2026-05-20 14:15:00", "msg_type": "text"},
        {"id": "MSG-2026-004", "chat_id": "CHAT-002", "sender": "赵主管", "content": "本周五团建，大家确认参加", "time": "2026-05-19 09:00:00", "msg_type": "text"},
        {"id": "MSG-2026-005", "chat_id": "CHAT-002", "sender": "孙六", "content": "我参加!", "time": "2026-05-19 09:05:00", "msg_type": "text"},
    ],
    "group_info": {
        "id": "GRP-001", "name": "项目A讨论组", "description": "项目A的日常沟通群",
        "members": [
            {"id": "U001", "name": "张三"}, {"id": "U002", "name": "李四"},
            {"id": "U003", "name": "王五"}, {"id": "U004", "name": "赵主管"},
        ],
        "member_count": 4, "created_at": "2026-01-15 09:00:00",
    },
    "chat_list": [
        {"id": "CHAT-001", "name": "项目A讨论组", "type": "group", "last_message": "测试报告已上传到文档中心", "unread": 2, "time": "2026-05-20 14:15:00"},
        {"id": "CHAT-002", "name": "技术部全员", "type": "group", "last_message": "我参加!", "unread": 0, "time": "2026-05-19 09:05:00"},
        {"id": "CHAT-003", "name": "赵主管", "type": "private", "last_message": "明天1v1记得准备周报", "unread": 1, "time": "2026-05-20 17:30:00"},
    ],
}

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("IM")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def send_message(
    target_id: str,
    content: str,
    msg_type: str = "text",
    target_type: str = "user",
) -> str:
    """发送消息

    向指定用户或群组发送消息，此为敏感操作需确认后执行。

    Args:
        target_id: 目标ID，用户ID或群组ID
        content: 消息内容
        msg_type: 消息类型，text(文本)/markdown(Markdown)/card(卡片)，默认text
        target_type: 目标类型，user(个人)/group(群组)，默认user

    Returns:
        发送结果 JSON 字符串
    """
    valid_msg_types = ("text", "markdown", "card")
    if msg_type not in valid_msg_types:
        return format_result(False, error=f"不支持的消息类型: {msg_type}，可选 {valid_msg_types}")

    valid_target_types = ("user", "group")
    if target_type not in valid_target_types:
        return format_result(False, error=f"不支持的目标类型: {target_type}，可选 {valid_target_types}")

    if is_mock_mode():
        return format_result(True, data={"target_id": target_id, "msg_type": msg_type, "status": "sent", "message_id": "MSG-NEW-001"})

    client = _get_api_client()
    payload: dict[str, Any] = {
        "target_id": target_id,
        "content": content,
        "msg_type": msg_type,
        "target_type": target_type,
    }

    result = await client.post("/messages/send", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "发送消息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_messages(
    chat_id: str,
    keyword: str = "",
    sender: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询聊天消息

    查询指定会话的消息记录，支持关键词搜索和筛选。

    Args:
        chat_id: 会话ID
        keyword: 搜索关键词
        sender: 发送者筛选
        date_from: 起始日期，格式 YYYY-MM-DD
        date_to: 截止日期，格式 YYYY-MM-DD
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        消息列表 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["messages"], "total": len(MOCK_DATA["messages"]), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"chat_id": chat_id, "page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if sender:
        params["sender"] = sender
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    result = await client.get("/messages", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询消息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def send_notification(
    user_ids: str,
    title: str,
    content: str,
    priority: str = "normal",
) -> str:
    """发送通知

    向指定用户发送系统通知，支持批量发送。

    Args:
        user_ids: 用户ID，多个用逗号分隔
        title: 通知标题
        content: 通知内容
        priority: 优先级，low/normal/high/urgent，默认normal

    Returns:
        发送结果 JSON 字符串
    """
    valid_priorities = ("low", "normal", "high", "urgent")
    if priority not in valid_priorities:
        return format_result(False, error=f"不支持的优先级: {priority}，可选 {valid_priorities}")

    if is_mock_mode():
        return format_result(True, data={"notification_id": "NTF-NEW-001", "user_ids": user_ids.split(","), "title": title, "status": "sent"})

    client = _get_api_client()
    payload: dict[str, Any] = {
        "user_ids": [u.strip() for u in user_ids.split(",")],
        "title": title,
        "content": content,
        "priority": priority,
    }

    result = await client.post("/notifications", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "发送通知失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_group_info(group_id: str) -> str:
    """查询群组信息

    查询指定群组的基本信息，包括群名、成员列表等。

    Args:
        group_id: 群组ID

    Returns:
        群组信息 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["group_info"])

    client = _get_api_client()
    result = await client.get(f"/groups/{group_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询群组信息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def create_group(
    name: str,
    member_ids: str,
    description: str = "",
) -> str:
    """创建群组

    创建一个新的聊天群组。

    Args:
        name: 群组名称
        member_ids: 成员ID，多个用逗号分隔
        description: 群组描述

    Returns:
        创建结果 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"group_id": "GRP-NEW-001", "name": name, "member_count": len(member_ids.split(",")), "status": "created"})

    client = _get_api_client()
    payload: dict[str, Any] = {
        "name": name,
        "member_ids": [m.strip() for m in member_ids.split(",")],
    }
    if description:
        payload["description"] = description

    result = await client.post("/groups", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "创建群组失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
