"""上下文管理与三级记忆

负责管理 Agent 的对话上下文，包括：
  - L1 工作记忆: Agent 内存，单次请求生命周期
  - L2 短期记忆: Redis 会话历史，2h TTL
  - L3 长期记忆: 会话归档（Phase 5 实现）
  - 上下文窗口管理与压缩
  - Token 估算
"""

import logging
from typing import Any

from agent.core.model_client import get_lightweight_client
from agent.core.session_manager import SessionState

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

    system_msg = messages[DEFAULT_SYSTEM_MESSAGE_INDEX] if messages else None
    early_msgs = messages[1:-recent_count]
    recent_msgs = messages[-recent_count:]

    if not early_msgs:
        return messages

    summary_text = await _generate_summary(early_msgs)

    compressed = []
    if system_msg:
        compressed.append(system_msg)

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


def extract_session_history(session: SessionState) -> list[dict[str, Any]]:
    """从会话状态中提取历史消息

    将 SessionState 中的 message_history 转换为 Agent 可用的消息格式。
    同时注入已有的上下文摘要（如果有）。

    Args:
        session: 会话状态

    Returns:
        消息列表
    """
    messages: list[dict[str, Any]] = []

    if session.context_summary:
        messages.append({
            "role": "system",
            "content": f"[本轮会话前情摘要] {session.context_summary}",
        })

    for msg in session.message_history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role and content:
            messages.append({"role": role, "content": content})

    return messages


async def prepare_context_for_agent(
    session: SessionState,
    system_message: str,
    current_task: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict[str, Any]]:
    """为 Agent 准备完整的上下文

    整合三级记忆，构建 Agent 可用的上下文消息列表：
    1. 从 L2 短期记忆（SessionState）提取历史
    2. 构建初始上下文
    3. 超过窗口时压缩

    Args:
        session: 会话状态（L2 短期记忆）
        system_message: Agent 系统提示词
        current_task: 当前用户任务
        max_tokens: 最大 Token 数

    Returns:
        压缩后的完整上下文消息列表
    """
    history = extract_session_history(session)
    context = build_agent_context(system_message, history, current_task)
    compressed = await compress_context(context, max_tokens=max_tokens)
    return compressed
