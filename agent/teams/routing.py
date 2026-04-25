"""任务路由

Supervisor 的路由决策逻辑：意图分类 -> 路由表匹配 -> 团队创建 -> 上下文注入 -> 任务执行
支持同步执行和流式执行两种模式。
"""

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from autogen_agentchat.messages import ChatMessage

from agent.agents.supervisor import (
    classify_intent,
    create_supervisor_agent,
    IntentResult,
    CollaborationMode,
)
from agent.teams.team_factory import create_team
from agent.core.session_manager import SessionState, get_session_manager
from agent.core.context_manager import prepare_context_for_agent, extract_session_history
from agent.agents.domain import AGENT_PROMPTS
from observability.metrics import record_agent_call
from observability.tracing import langfuse_tracer

logger = logging.getLogger(__name__)

# 置信度阈值，低于此值需要用户确认
CONFIDENCE_THRESHOLD = 0.7


async def route_and_execute(
    user_message: str,
    session_id: str,
    user_id: str,
    session: SessionState | None = None,
) -> dict[str, Any]:
    """路由并执行用户请求

    完整流程：
    1. 意图分类
    2. 置信度校验
    3. 路由表匹配
    4. 创建团队
    5. 上下文注入（L2 短期记忆）
    6. 执行任务
    7. 返回结果

    Args:
        user_message: 用户消息
        session_id: 会话ID
        user_id: 用户ID
        session: 会话状态（可选，传入时直接使用，否则从 SessionManager 获取）

    Returns:
        执行结果字典
    """
    start_time = time.time()

    # 获取会话状态（L2 短期记忆）
    if session is None:
        session_mgr = await get_session_manager()
        session = await session_mgr.get_session(session_id)

    # 1. 意图分类
    intent = await classify_intent(user_message)
    logger.info(
        "意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
        intent.intent,
        intent.confidence,
        intent.target_agent,
        intent.collaboration_mode.value,
    )

    # 2. 置信度校验
    if intent.confidence < CONFIDENCE_THRESHOLD:
        return {
            "status": "clarification_needed",
            "message": f"我不太确定您的意图（置信度: {intent.confidence:.0%}），请更详细地描述您的需求。",
            "intent": intent.intent,
            "confidence": intent.confidence,
        }

    # 3. 创建团队
    try:
        team = await create_team(intent)
    except Exception as e:
        logger.error("创建团队失败: %s", e)
        return {
            "status": "error",
            "message": "系统暂时无法处理您的请求，请稍后重试。",
            "intent": intent.intent,
        }

    # 4. 构建带上下文的任务描述
    task = await _build_contextual_task(user_message, intent, session)

    # 5. 执行任务
    try:
        result = await team.run(task=task)
        output = result.messages[-1].content if result.messages else "处理完成"

        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "success", duration)

        # 记录 Agent 调用审计日志
        try:
            from agent.core.audit import audit_log, AuditEventType
            await audit_log(
                event_type=AuditEventType.AGENT,
                action="task_execute",
                user_id=user_id,
                session_id=session_id,
                agent_name=intent.target_agent,
                detail={
                    "intent": intent.intent,
                    "mode": intent.collaboration_mode.value,
                    "duration_ms": round(duration * 1000),
                    "status": "success",
                },
            )
        except Exception:
            pass

        langfuse_tracer.trace_agent_call(
            trace_id=session_id,
            agent_name=intent.target_agent,
            input_text=user_message,
            output_text=output,
            metadata={
                "user_id": user_id,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
                "review_required": intent.review_required,
            },
        )

        return {
            "status": "success",
            "message": output,
            "agent_name": intent.target_agent,
            "intent": intent.intent,
            "collaboration_mode": intent.collaboration_mode.value,
        }

    except Exception as e:
        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "error", duration)
        logger.error("任务执行失败: agent=%s error=%s", intent.target_agent, e)

        # 记录 Agent 调用失败审计日志
        try:
            from agent.core.audit import audit_log, AuditEventType
            await audit_log(
                event_type=AuditEventType.AGENT,
                action="task_execute_failed",
                user_id=user_id,
                session_id=session_id,
                agent_name=intent.target_agent,
                detail={
                    "intent": intent.intent,
                    "mode": intent.collaboration_mode.value,
                    "duration_ms": round(duration * 1000),
                    "status": "error",
                    "error": str(e)[:200],
                },
            )
        except Exception:
            pass

        return {
            "status": "error",
            "message": f"任务执行失败: {str(e)}",
            "agent_name": intent.target_agent,
            "intent": intent.intent,
        }


async def _build_contextual_task(
    user_message: str,
    intent: IntentResult,
    session: SessionState | None,
) -> str:
    """构建带上下文的任务描述

    将会话历史注入到任务描述中，使 Agent 能够理解对话上下文。
    对于 DIRECT 模式，注入简短的历史摘要；
    对于 SELECTOR/SWARM 模式，注入更完整的上下文。

    Args:
        user_message: 用户原始消息
        intent: 意图分类结果
        session: 会话状态

    Returns:
        带上下文的任务描述
    """
    if session is None or not session.message_history:
        return user_message

    # 提取最近的对话历史（取最近几轮，避免过长）
    recent_history = session.message_history[-6:]
    history_parts = []
    for msg in recent_history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role and content:
            role_label = "用户" if role == "user" else "助手"
            history_parts.append(f"{role_label}: {content}")

    if not history_parts:
        return user_message

    history_text = "\n".join(history_parts)

    # 注入上下文摘要（如果有）
    summary_prefix = ""
    if session.context_summary:
        summary_prefix = f"[前情摘要] {session.context_summary}\n\n"

    task = (
        f"{summary_prefix}"
        f"[对话历史]\n{history_text}\n\n"
        f"[当前请求] {user_message}"
    )

    return task


async def route_and_execute_stream(
    user_message: str,
    session_id: str,
    user_id: str,
    session: SessionState | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """路由并流式执行用户请求

    与 route_and_execute 流程一致，但使用 AutoGen 的 run_stream()
    逐 Token 产出中间结果，实现真正的流式输出。

    Yields:
        流式事件字典，包含以下类型:
        - {"type": "intent", "intent": ..., "confidence": ..., "agent": ..., "mode": ...}
        - {"type": "clarification", "message": ...}
        - {"type": "error", "message": ...}
        - {"type": "chunk", "agent_name": ..., "content": ...}
        - {"type": "complete", "agent_name": ..., "intent": ..., "mode": ...}
    """
    start_time = time.time()

    if session is None:
        session_mgr = await get_session_manager()
        session = await session_mgr.get_session(session_id)

    # 1. 意图分类
    intent = await classify_intent(user_message)
    logger.info(
        "流式-意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
        intent.intent,
        intent.confidence,
        intent.target_agent,
        intent.collaboration_mode.value,
    )

    yield {
        "type": "intent",
        "intent": intent.intent,
        "confidence": intent.confidence,
        "agent": intent.target_agent,
        "mode": intent.collaboration_mode.value,
    }

    # 2. 置信度校验
    if intent.confidence < CONFIDENCE_THRESHOLD:
        yield {
            "type": "clarification",
            "message": f"我不太确定您的意图（置信度: {intent.confidence:.0%}），请更详细地描述您的需求。",
        }
        return

    # 3. 创建团队
    try:
        team = await create_team(intent)
    except Exception as e:
        logger.error("流式-创建团队失败: %s", e)
        yield {
            "type": "error",
            "message": "系统暂时无法处理您的请求，请稍后重试。",
        }
        return

    # 4. 构建带上下文的任务描述
    task = await _build_contextual_task(user_message, intent, session)

    # 5. 流式执行任务
    full_response = ""
    current_agent = intent.target_agent

    try:
        async for message in team.run_stream(task=task):
            if isinstance(message, ChatMessage):
                content = message.content if hasattr(message, "content") else str(message)
                if content and isinstance(content, str):
                    source_name = getattr(message, "source", current_agent)
                    if source_name:
                        current_agent = source_name

                    delta = content[len(full_response):]
                    if delta:
                        full_response = content
                        yield {
                            "type": "chunk",
                            "agent_name": current_agent,
                            "content": delta,
                        }

        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "success", duration)

        langfuse_tracer.trace_agent_call(
            trace_id=session_id,
            agent_name=intent.target_agent,
            input_text=user_message,
            output_text=full_response,
            metadata={
                "user_id": user_id,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
                "review_required": intent.review_required,
            },
        )

        yield {
            "type": "complete",
            "agent_name": current_agent,
            "intent": intent.intent,
            "mode": intent.collaboration_mode.value,
            "full_message": full_response,
        }

    except Exception as e:
        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "error", duration)
        logger.error("流式-任务执行失败: agent=%s error=%s", intent.target_agent, e)

        yield {
            "type": "error",
            "message": f"任务执行失败: {str(e)}",
        }
