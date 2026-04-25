"""协议转换器

将各渠道消息统一转换为平台内部标准格式，
并处理消息的序列化与反序列化。
"""

import json
import logging
from typing import Any

from gateway.adapters.channel_adapter import (
    StandardMessage,
    parse_channel_message,
    format_channel_reply,
)

logger = logging.getLogger(__name__)


def convert_incoming_message(channel: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    """将渠道原始消息转换为平台内部请求格式

    完整流程：渠道原始数据 -> StandardMessage -> 平台请求格式

    Args:
        channel: 来源渠道
        raw_data: 渠道原始数据

    Returns:
        平台内部请求格式字典，可直接传入 Agent 路由层
    """
    try:
        standard_msg = parse_channel_message(channel, raw_data)
    except ValueError as e:
        logger.warning("消息解析失败: channel=%s error=%s", channel, e)
        return {
            "status": "parse_error",
            "error": str(e),
            "channel": channel,
        }

    platform_request = {
        "status": "ok",
        "message_id": standard_msg.message_id,
        "channel": standard_msg.channel,
        "user_id": standard_msg.user_id,
        "user_name": standard_msg.user_name,
        "query": standard_msg.content,
        "content_type": standard_msg.content_type,
        "session_id": standard_msg.session_id,
        "timestamp": standard_msg.timestamp,
        "reply_url": standard_msg.reply_url,
    }

    logger.info(
        "消息转换成功: channel=%s user=%s session=%s",
        standard_msg.channel,
        standard_msg.user_id,
        standard_msg.session_id,
    )

    return platform_request


def convert_outgoing_message(
    channel: str,
    agent_response: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    """将 Agent 响应转换为渠道回复格式

    Args:
        channel: 目标渠道
        agent_response: Agent 响应数据
        session_id: 会话ID

    Returns:
        渠道格式的回复数据
    """
    message = agent_response.get("message", "")
    if not message:
        message = "处理完成，但无具体内容返回。"

    try:
        reply = format_channel_reply(channel, message, session_id)
    except ValueError as e:
        logger.warning("回复格式化失败: channel=%s error=%s", channel, e)
        reply = {"type": "text", "content": message}

    reply["agent_status"] = agent_response.get("status", "unknown")
    reply["agent_name"] = agent_response.get("agent_name", "")

    return reply


def validate_message(raw_data: dict[str, Any]) -> bool:
    """校验消息数据的基本完整性

    Args:
        raw_data: 原始消息数据

    Returns:
        数据是否有效
    """
    if not isinstance(raw_data, dict):
        return False
    if not raw_data:
        return False
    return True


def serialize_message(message: StandardMessage) -> str:
    """将标准消息序列化为 JSON 字符串

    Args:
        message: 标准消息对象

    Returns:
        JSON 字符串
    """
    return json.dumps(message.model_dump(mode="json"), ensure_ascii=False, default=str)


def deserialize_message(json_str: str) -> StandardMessage:
    """将 JSON 字符串反序列化为标准消息

    Args:
        json_str: JSON 字符串

    Returns:
        标准消息对象
    """
    data = json.loads(json_str)
    return StandardMessage(**data)
