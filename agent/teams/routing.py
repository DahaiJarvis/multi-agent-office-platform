"""任务路由

Supervisor 的路由决策逻辑：意图分类 -> 路由表匹配 -> 团队创建 -> 上下文注入 -> 任务执行
支持同步执行和流式执行两种模式。
集成 ExecutionController 实现超时控制、重试逻辑和上下文压缩。
"""

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from autogen_agentchat.messages import TextMessage, ToolCallSummaryMessage, HandoffMessage

from agent.agents.supervisor import (
    classify_intent,
    IntentResult,
    CollaborationMode,
)
from agent.teams.team_factory import create_team
from agent.teams.execution_controller import get_execution_controller
from agent.core.session_manager import SessionState, get_session_manager
from agent.core.event_bus import publish_event, EventType
from observability.metrics import record_agent_call
from observability.tracing import langfuse_tracer, span_cache

logger = logging.getLogger(__name__)

# 置信度阈值，低于此值需要用户确认
CONFIDENCE_THRESHOLD = 0.7


async def route_and_execute(
    user_message: str,
    session_id: str,
    user_id: str,
    session: SessionState | None = None,
    knowledge_base_id: str | None = None,
) -> dict[str, Any]:
    """路由并执行用户请求

    完整流程：
    1. 意图分类（选择知识库时跳过，直接路由到 KnowledgeAgent）
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
        knowledge_base_id: 知识库ID（可选，选择知识库后直接路由到 KnowledgeAgent）

    Returns:
        执行结果字典
    """
    start_time = time.time()

    # 获取会话状态（L2 短期记忆）
    if session is None:
        session_mgr = await get_session_manager()
        session = await session_mgr.get_session(session_id)

    # 1. 意图分类（选择知识库时跳过，直接路由到 KnowledgeAgent）
    if knowledge_base_id:
        intent = IntentResult(
            intent="knowledge_query",
            confidence=1.0,
            target_agent="KnowledgeAgent",
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )
        logger.info(
            "知识库直路由: kb_id=%s agent=%s",
            knowledge_base_id,
            intent.target_agent,
        )
    else:
        intent = await classify_intent(user_message)
        logger.info(
            "意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
            intent.intent,
            intent.confidence,
            intent.target_agent,
            intent.collaboration_mode.value,
        )

    # 记录意图分类 Span
    try:
        intent_duration = (time.time() - start_time) * 1000
        langfuse_tracer.trace_intent_classification(
            trace_id=session_id,
            user_message=user_message,
            intent=intent.intent,
            confidence=intent.confidence,
            target_agent=intent.target_agent,
            duration_ms=intent_duration,
        )
        await span_cache.store_span(
            session_id=session_id,
            span_type="intent_classification",
            input_data={"user_message": user_message},
            output_data={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "mode": intent.collaboration_mode.value,
            },
            duration_ms=intent_duration,
        )
    except Exception:
        pass

    # 发布意图分类事件
    try:
        await publish_event(
            EventType.INTENT_CLASSIFIED,
            session_id,
            {
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "mode": intent.collaboration_mode.value,
            },
        )
    except Exception:
        pass

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
    task = await _build_contextual_task(user_message, intent, session, knowledge_base_id)

    # 发布 Agent 启动事件
    try:
        await publish_event(
            EventType.AGENT_START,
            session_id,
            {
                "agent_name": intent.target_agent,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
            },
        )
    except Exception:
        pass

    # 5. 执行任务（集成 ExecutionController：超时控制、重试、上下文压缩）
    try:
        controller = get_execution_controller()
        result, exec_meta = await controller.execute_with_control(
            team, task, session_id, user_id,
        )

        if exec_meta.status == "timeout":
            duration = time.time() - start_time
            record_agent_call(intent.target_agent, "error", duration)
            try:
                await publish_event(EventType.ERROR, session_id, {
                    "agent_name": intent.target_agent,
                    "error": "timeout",
                    "duration_ms": round(duration * 1000),
                })
            except Exception:
                pass
            return {
                "status": "error",
                "message": f"任务执行超时（超过 {controller._config.max_runtime}s），请简化请求或稍后重试。",
                "agent_name": intent.target_agent,
                "intent": intent.intent,
            }

        if exec_meta.status == "error" and result is None:
            duration = time.time() - start_time
            record_agent_call(intent.target_agent, "error", duration)
            logger.error("任务执行失败: agent=%s error=%s", intent.target_agent, exec_meta.message)
            try:
                await publish_event(EventType.ERROR, session_id, {
                    "agent_name": intent.target_agent,
                    "error": exec_meta.message,
                    "duration_ms": round(duration * 1000),
                })
            except Exception:
                pass
            return {
                "status": "error",
                "message": f"任务执行失败: {exec_meta.message}",
                "agent_name": intent.target_agent,
                "intent": intent.intent,
            }

        output = result.messages[-1].content if result and result.messages else "处理完成"

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
                    "retries": exec_meta.retries,
                    "compacted": exec_meta.compacted,
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
                "retries": exec_meta.retries,
                "compacted": exec_meta.compacted,
            },
        )

        # 记录 Agent 调用 Span 和统计
        try:
            await span_cache.store_span(
                session_id=session_id,
                span_type="agent_call",
                input_data={"user_message": user_message, "intent": intent.intent},
                output_data={"message": output[:500], "agent": intent.target_agent},
                duration_ms=duration * 1000,
                metadata={"retries": exec_meta.retries, "compacted": exec_meta.compacted},
            )
            await span_cache.increment_agent_stats(
                intent.target_agent, duration * 1000, success=True,
            )
        except Exception:
            pass

        # 发布 Agent 完成事件
        try:
            await publish_event(EventType.AGENT_END, session_id, {
                "agent_name": intent.target_agent,
                "status": "success",
                "duration_ms": round(duration * 1000),
                "retries": exec_meta.retries,
                "compacted": exec_meta.compacted,
            })
        except Exception:
            pass

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

        # 记录 Agent 调用失败统计
        try:
            await span_cache.increment_agent_stats(
                intent.target_agent, duration * 1000, success=False,
            )
        except Exception:
            pass

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
    finally:
        # After-turn 知识提取（异步，不阻塞主流程）
        try:
            from agent.core.context_manager import extract_and_store_knowledge
            tenant_id = ""
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass
            if session and session.message_history:
                await extract_and_store_knowledge(
                    user_id=user_id,
                    session_id=session_id,
                    messages=session.message_history,
                    tenant_id=tenant_id,
                )
        except Exception:
            pass


async def _build_contextual_task(
    user_message: str,
    intent: IntentResult,
    session: SessionState | None,
    knowledge_base_id: str | None = None,
) -> str:
    """构建带上下文的任务描述

    将会话历史注入到任务描述中，使 Agent 能够理解对话上下文。
    对于 DIRECT 模式，注入简短的历史摘要；
    对于 SELECTOR/SWARM 模式，注入更完整的上下文。
    当指定知识库时，在任务描述中注入知识库标识，引导 Agent 使用对应知识库。

    Args:
        user_message: 用户原始消息
        intent: 意图分类结果
        session: 会话状态
        knowledge_base_id: 知识库ID（可选，指定后注入到任务上下文）

    Returns:
        带上下文的任务描述
    """
    parts = []

    # 注入知识库上下文
    if knowledge_base_id:
        parts.append(f"[知识库ID] {knowledge_base_id}")
        parts.append("请使用知识库检索工具在指定知识库中搜索相关信息来回答用户问题。")

    if session is not None and session.message_history:
        # 提取最近的对话历史（取最近几轮，避免过长）
        recent_history = session.message_history[-6:]
        history_parts = []
        for msg in recent_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                role_label = "用户" if role == "user" else "助手"
                history_parts.append(f"{role_label}: {content}")

        if history_parts:
            history_text = "\n".join(history_parts)

            if session.context_summary:
                parts.append(f"[前情摘要] {session.context_summary}")

            parts.append(f"[对话历史]\n{history_text}")

    parts.append(f"[当前请求] {user_message}")

    return "\n\n".join(parts)


async def route_and_execute_stream(
    user_message: str,
    session_id: str,
    user_id: str,
    session: SessionState | None = None,
    knowledge_base_id: str | None = None,
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

    # 1. 意图分类（选择知识库时跳过，直接路由到 KnowledgeAgent）
    if knowledge_base_id:
        intent = IntentResult(
            intent="knowledge_query",
            confidence=1.0,
            target_agent="KnowledgeAgent",
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )
        logger.info(
            "流式-知识库直路由: kb_id=%s agent=%s",
            knowledge_base_id,
            intent.target_agent,
        )
    else:
        intent = await classify_intent(user_message)
        logger.info(
            "流式-意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
            intent.intent,
            intent.confidence,
            intent.target_agent,
            intent.collaboration_mode.value,
        )

    # 记录意图分类 Span
    try:
        intent_duration = (time.time() - start_time) * 1000
        langfuse_tracer.trace_intent_classification(
            trace_id=session_id,
            user_message=user_message,
            intent=intent.intent,
            confidence=intent.confidence,
            target_agent=intent.target_agent,
            duration_ms=intent_duration,
        )
        await span_cache.store_span(
            session_id=session_id,
            span_type="intent_classification",
            input_data={"user_message": user_message},
            output_data={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "mode": intent.collaboration_mode.value,
            },
            duration_ms=intent_duration,
        )
    except Exception:
        pass

    yield {
        "type": "intent",
        "intent": intent.intent,
        "confidence": intent.confidence,
        "agent": intent.target_agent,
        "mode": intent.collaboration_mode.value,
    }

    # 发布意图分类事件
    try:
        await publish_event(EventType.INTENT_CLASSIFIED, session_id, {
            "intent": intent.intent,
            "confidence": intent.confidence,
            "target_agent": intent.target_agent,
            "mode": intent.collaboration_mode.value,
        })
    except Exception:
        pass

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
    task = await _build_contextual_task(user_message, intent, session, knowledge_base_id)

    # 发布 Agent 启动事件
    try:
        await publish_event(EventType.AGENT_START, session_id, {
            "agent_name": intent.target_agent,
            "intent": intent.intent,
            "mode": intent.collaboration_mode.value,
        })
    except Exception:
        pass

    # 5. 流式执行任务（集成 ExecutionController：超时控制、重试、上下文压缩）
    full_response = ""
    current_agent = intent.target_agent

    try:
        controller = get_execution_controller()
        async for message in controller.execute_stream_with_control(
            team, task, session_id, user_id,
        ):
            # 处理 ExecutionController 的控制事件
            if isinstance(message, dict):
                msg_type = message.get("type", "")
                if msg_type == "timeout":
                    duration = time.time() - start_time
                    record_agent_call(intent.target_agent, "error", duration)
                    yield {
                        "type": "error",
                        "message": message.get("message", "任务执行超时"),
                    }
                    return
                elif msg_type == "retry":
                    logger.info(
                        "流式执行重试: attempt=%d/%d",
                        message.get("attempt", 0),
                        message.get("max_retries", 0),
                    )
                    continue
                elif msg_type == "compacted":
                    logger.info(
                        "流式执行上下文压缩: %d -> %d tokens",
                        message.get("original_tokens", 0),
                        message.get("compacted_tokens", 0),
                    )
                    continue
                elif msg_type == "error":
                    duration = time.time() - start_time
                    record_agent_call(intent.target_agent, "error", duration)
                    yield {
                        "type": "error",
                        "message": message.get("message", "任务执行失败"),
                    }
                    return
                # 其他 dict 类型事件直接传递
                continue

            # 处理 AutoGen 消息
            if isinstance(message, (TextMessage, ToolCallSummaryMessage, HandoffMessage)):
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

        # 记录 Agent 调用 Span 和统计
        try:
            await span_cache.store_span(
                session_id=session_id,
                span_type="agent_call",
                input_data={"user_message": user_message, "intent": intent.intent},
                output_data={"message": full_response[:500], "agent": intent.target_agent},
                duration_ms=duration * 1000,
            )
            await span_cache.increment_agent_stats(
                intent.target_agent, duration * 1000, success=True,
            )
        except Exception:
            pass

        # 发布 Agent 完成事件
        try:
            await publish_event(EventType.AGENT_END, session_id, {
                "agent_name": intent.target_agent,
                "status": "success",
                "duration_ms": round(duration * 1000),
            })
        except Exception:
            pass

        yield {
            "type": "complete",
            "agent_name": current_agent,
            "intent": intent.intent,
            "mode": intent.collaboration_mode.value,
            "full_message": full_response,
        }

        # After-turn 知识提取（流式完成后异步执行，不阻塞主流程）
        try:
            from agent.core.context_manager import extract_and_store_knowledge
            tenant_id = ""
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass
            if session and session.message_history:
                await extract_and_store_knowledge(
                    user_id=user_id,
                    session_id=session_id,
                    messages=session.message_history,
                    tenant_id=tenant_id,
                )
        except Exception:
            pass

    except Exception as e:
        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "error", duration)
        logger.error("流式-任务执行失败: agent=%s error=%s", intent.target_agent, e)

        # 记录 Agent 调用失败统计
        try:
            await span_cache.increment_agent_stats(
                intent.target_agent, duration * 1000, success=False,
            )
        except Exception:
            pass

        yield {
            "type": "error",
            "message": f"任务执行失败: {str(e)}",
        }
