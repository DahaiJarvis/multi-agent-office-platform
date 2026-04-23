"""上下文管理与压缩

负责管理 Agent 的对话上下文，包括：
  - 上下文窗口管理
  - 对话历史压缩
  - Token 估算
"""

import logging
from typing import Any

from agent.core.model_client import get_lightweight_client

logger = logging.getLogger(__name__)

# 上下文窗口默认配置
DEFAULT_MAX_TOKENS = 4000
DEFAULT_RECENT_MESSAGES = 6
DEFAULT_SYSTEM_MESSAGE_INDEX = 0


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算消息列表的 Token 数

    中文约 1.5 字符/Token，英文约 4 字符/Token，
    取平均值约 2 字符/Token 作为估算。

    Args:
        messages: 消息列表

    Returns:
        估算的 Token 数
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        total_chars += len(content)
        # 额外计算 role 和元数据的开销
        total_chars += 20
    return max(1, total_chars // 2)


async def compress_context(
    messages: list[dict[str, Any]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    recent_count: int = DEFAULT_RECENT_MESSAGES,
) -> list[dict[str, Any]]:
    """压缩对话历史，保留关键信息

    当对话历史超过 max_tokens 时，将早期消息压缩为摘要，
    保留最近 recent_count 条消息不变。

    Args:
        messages: 原始消息列表
        max_tokens: 最大 Token 数
        recent_count: 保留的最近消息数

    Returns:
        压缩后的消息列表
    """
    if estimate_tokens(messages) <= max_tokens:
        return messages

    if len(messages) <= recent_count + 1:
        return messages

    # 分离系统消息、早期消息和近期消息
    system_msg = messages[DEFAULT_SYSTEM_MESSAGE_INDEX] if messages else None
    early_msgs = messages[1:-recent_count]
    recent_msgs = messages[-recent_count:]

    if not early_msgs:
        return messages

    # 使用轻量级模型生成摘要
    summary_text = await _generate_summary(early_msgs)

    compressed = []
    if system_msg:
        compressed.append(system_msg)

    # 插入摘要作为系统上下文
    compressed.append(
        {
            "role": "system",
            "content": f"[历史对话摘要] {summary_text}",
        }
    )

    compressed.extend(recent_msgs)

    original_tokens = estimate_tokens(messages)
    compressed_tokens = estimate_tokens(compressed)
    logger.info(
        "上下文压缩完成: %d -> %d tokens (压缩率 %.1f%%)",
        original_tokens,
        compressed_tokens,
        (1 - compressed_tokens / original_tokens) * 100,
    )

    return compressed


async def _generate_summary(messages: list[dict[str, Any]]) -> str:
    """使用 LLM 生成对话摘要

    Args:
        messages: 需要摘要的消息列表

    Returns:
        摘要文本
    """
    # 构建待摘要的文本
    conversation = ""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        conversation += f"[{role}] {content}\n"

    prompt = (
        "请将以下对话历史压缩为简洁摘要，保留关键信息、决策和待办事项：\n\n"
        f"{conversation}"
    )

    try:
        client = get_lightweight_client()
        response = await client.create(
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("生成对话摘要失败: %s", e)
        # 降级方案：截取每条消息的前100字
        fallback_parts = []
        for msg in messages:
            content = msg.get("content", "")[:100]
            fallback_parts.append(content)
        return " | ".join(fallback_parts)


def build_agent_context(
    system_message: str,
    session_history: list[dict[str, Any]],
    current_task: str | None = None,
) -> list[dict[str, Any]]:
    """构建 Agent 的完整上下文

    Args:
        system_message: 系统提示词
        session_history: 会话历史消息
        current_task: 当前任务描述

    Returns:
        完整的消息列表
    """
    context: list[dict[str, Any]] = [
        {"role": "system", "content": system_message}
    ]

    context.extend(session_history)

    if current_task:
        context.append({"role": "user", "content": current_task})

    return context
