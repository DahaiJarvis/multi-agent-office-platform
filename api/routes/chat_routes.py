"""Agent 对话路由

提供同步和流式两种对话接口:
  - POST /agent/chat: 同步对话，等待完整响应后返回
  - POST /agent/chat/stream: 流式对话，SSE 逐 Token 推送响应
  - GET /agent/events/{session_id}: SSE 事件流，实时推送事件总线事件
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.errors import AppException, ErrorCode
from api.models.request import ChatRequest
from api.models.response import ChatResponse
from agent.core.session.session_manager import get_session_manager
from agent.teams.routing import route_and_execute, route_and_execute_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent-Chat"])


def format_sse(event: str, data: str) -> str:
    """格式化 SSE 事件

    Args:
        event: 事件类型
        data: 事件数据

    Returns:
        SSE 格式字符串
    """
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


@router.post("/chat", response_model=ChatResponse, summary="同步对话")
async def chat(request: ChatRequest) -> ChatResponse:
    """处理用户对话请求（同步模式）

    流程：输入护栏检查 -> 获取/创建会话 -> 追加用户消息 -> Supervisor 路由 -> Agent 执行 -> 输出护栏检查 -> 返回结果
    """
    from security.guardrails import check_input_guardrails, check_output_guardrails

    input_result = await check_input_guardrails(
        content=request.message,
        conversation_history=None,
    )
    if not input_result.passed:
        raise AppException(ErrorCode.GUARDRAIL_BLOCKED, message=input_result.reason)

    actual_message = input_result.redacted_content or request.message

    session_mgr = await get_session_manager()

    if request.session_id:
        session = await session_mgr.get_session(request.session_id)
        if session is None:
            raise AppException(ErrorCode.SESSION_NOT_FOUND)
    else:
        session = await session_mgr.create_session(
            user_id=request.user_id, channel=request.channel
        )

    await session_mgr.append_message(
        session_id=session.session_id,
        role="user",
        content=actual_message,
    )

    result = await route_and_execute(
        user_message=actual_message,
        session_id=session.session_id,
        user_id=request.user_id,
        session=session,
        knowledge_base_id=request.knowledge_base_id,
    )

    reply = result.get("message", "处理完成")

    output_result = await check_output_guardrails(content=reply)
    if not output_result.passed:
        raise AppException(ErrorCode.GUARDRAIL_BLOCKED, message=output_result.reason)
    if output_result.redacted_content:
        reply = output_result.redacted_content

    agent_name = result.get("agent_name", "Supervisor")
    intent = result.get("intent")
    collaboration_mode = result.get("collaboration_mode")

    await session_mgr.append_message(
        session_id=session.session_id,
        role="assistant",
        content=reply,
        metadata={
            "agent": agent_name,
            "intent": intent,
            "collaboration_mode": collaboration_mode,
        },
    )

    try:
        await session_mgr.archive_session(session.session_id)
    except Exception:
        logger.debug("自动归档会话失败: %s", session.session_id)

    return ChatResponse(
        session_id=session.session_id,
        message=reply,
        agent_name=agent_name,
        intent=intent,
        collaboration_mode=collaboration_mode,
    )


@router.post("/chat/stream", summary="流式对话")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """处理用户对话请求（流式模式）

    通过 SSE (Server-Sent Events) 逐 Token 推送 Agent 响应。
    使用 AutoGen 的 run_stream() 实现真正的 Token 级流式输出，
    LLM 每生成一个 Token 即推送到客户端，显著降低首 Token 延迟。

    流程：输入护栏检查 -> 获取/创建会话 -> 追加用户消息 -> 流式执行 -> 输出护栏检查
    """
    from security.guardrails import check_input_guardrails, check_output_guardrails

    input_result = await check_input_guardrails(
        content=request.message,
        conversation_history=None,
    )
    if not input_result.passed:
        raise AppException(ErrorCode.GUARDRAIL_BLOCKED, message=input_result.reason)

    actual_message = input_result.redacted_content or request.message

    session_mgr = await get_session_manager()

    if request.session_id:
        session = await session_mgr.get_session(request.session_id)
        if session is None:
            raise AppException(ErrorCode.SESSION_NOT_FOUND)
    else:
        session = await session_mgr.create_session(
            user_id=request.user_id, channel=request.channel
        )

    await session_mgr.append_message(
        session_id=session.session_id,
        role="user",
        content=actual_message,
    )

    async def event_generator():
        try:
            yield format_sse("session_id", session.session_id)

            full_message = ""
            final_agent = "Supervisor"
            final_intent = ""
            final_mode = ""

            event_bus_gen: AsyncGenerator[str, None] | None = None
            try:
                from agent.core.infrastructure.event_bus import subscribe_events, EventType

                _event_types = [
                    EventType.TOOL_CALL,
                    EventType.TOOL_RESULT,
                    EventType.GUARDRAIL_BLOCK,
                    EventType.APPROVAL_PENDING,
                    EventType.CONTEXT_COMPACTION,
                    EventType.DEGRADATION,
                ]

                async def _listen_event_bus() -> AsyncGenerator[str, None]:
                    async for event in subscribe_events(session.session_id, _event_types):
                        if event.data.get("heartbeat"):
                            continue
                        yield format_sse("bus_event", json.dumps({
                            "event_type": event.event_type.value,
                            "data": event.data,
                            "timestamp": event.timestamp,
                        }, ensure_ascii=False))

                event_bus_gen = _listen_event_bus()
            except Exception:
                event_bus_gen = None

            stream_gen = route_and_execute_stream(
                user_message=actual_message,
                session_id=session.session_id,
                user_id=request.user_id,
                session=session,
                knowledge_base_id=request.knowledge_base_id,
            )

            async def _consume_stream() -> AsyncGenerator[str, None]:
                nonlocal full_message, final_agent, final_intent, final_mode
                async for event in stream_gen:
                    event_type = event.get("type")

                    if event_type == "intent":
                        yield format_sse("intent", json.dumps({
                            "intent": event["intent"],
                            "confidence": event["confidence"],
                            "agent": event["agent"],
                            "mode": event["mode"],
                        }, ensure_ascii=False))

                    elif event_type == "clarification":
                        yield format_sse("chunk", event["message"])
                        yield format_sse("status", "clarification_needed")
                        full_message = event["message"]

                    elif event_type == "chunk":
                        full_message += event["content"]
                        yield format_sse("chunk", event["content"])
                        final_agent = event.get("agent_name", final_agent)

                    elif event_type == "tool_call":
                        yield format_sse("tool_call", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "tools": event.get("tools", []),
                        }, ensure_ascii=False))

                    elif event_type == "tool_result":
                        yield format_sse("tool_result", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "tool_name": event.get("tool_name", ""),
                            "is_error": event.get("is_error", False),
                            "content": event.get("content", ""),
                        }, ensure_ascii=False))

                    elif event_type == "thought":
                        yield format_sse("thought", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "content": event.get("content", ""),
                        }, ensure_ascii=False))

                    elif event_type == "handoff":
                        yield format_sse("handoff", json.dumps({
                            "from_agent": event.get("from_agent", ""),
                            "to_agent": event.get("to_agent", ""),
                        }, ensure_ascii=False))

                    elif event_type == "execution_id":
                        yield format_sse("execution_id", event.get("execution_id", ""))

                    elif event_type == "step_start":
                        yield format_sse("step_start", json.dumps({
                            "step_name": event.get("step_name", ""),
                            "agent_name": event.get("agent_name", ""),
                            "step_index": event.get("step_index", 0),
                            "total_steps": event.get("total_steps", 0),
                        }, ensure_ascii=False))

                    elif event_type == "step_done":
                        step_done_data = {
                            "step_name": event.get("step_name", ""),
                            "agent_name": event.get("agent_name", ""),
                            "step_index": event.get("step_index", 0),
                            "total_steps": event.get("total_steps", 0),
                            "status": event.get("status", ""),
                        }
                        if event.get("message"):
                            step_done_data["message"] = event["message"]
                        if event.get("error"):
                            step_done_data["error"] = event["error"]
                        yield format_sse("step_done", json.dumps(step_done_data, ensure_ascii=False))

                    elif event_type == "complete":
                        final_agent = event.get("agent_name", final_agent)
                        final_intent = event.get("intent", "")
                        final_mode = event.get("mode", "")
                        full_message = event.get("full_message", full_message)
                        yield format_sse("agent_name", final_agent)
                        yield format_sse("intent", final_intent)
                        yield format_sse("collaboration_mode", final_mode)
                        yield format_sse("status", "completed")

                    elif event_type == "paused":
                        final_agent = event.get("agent_name", final_agent)
                        final_intent = event.get("intent", "")
                        final_mode = event.get("mode", "")
                        if event.get("full_message"):
                            full_message = event["full_message"]
                        yield format_sse("agent_name", final_agent)
                        yield format_sse("intent", final_intent)
                        yield format_sse("collaboration_mode", final_mode)
                        yield format_sse("status", "paused")

                    elif event_type == "error":
                        full_message += event["message"]
                        yield format_sse("error", event["message"])
                        yield format_sse("status", "error")

            try:
                async for sse_data in _consume_stream():
                    yield sse_data

                    if event_bus_gen is not None:
                        try:
                            while True:
                                bus_sse = await asyncio.wait_for(
                                    event_bus_gen.__anext__(),
                                    timeout=0.01,
                                )
                                yield bus_sse
                        except (asyncio.TimeoutError, StopAsyncIteration):
                            pass
            finally:
                if event_bus_gen is not None:
                    try:
                        await event_bus_gen.aclose()
                    except Exception as e:
                        logger.debug("操作失败，已忽略: %s", e)

            if full_message:
                try:
                    output_result = await check_output_guardrails(content=full_message)
                    if not output_result.passed:
                        yield format_sse("guardrail_block", output_result.reason)
                        yield format_sse("status", "blocked")
                        return
                    if output_result.redacted_content:
                        full_message = output_result.redacted_content
                except Exception as output_err:
                    logger.warning("输出护栏检查异常，放行: %s", output_err)

                await session_mgr.append_message(
                    session_id=session.session_id,
                    role="assistant",
                    content=full_message,
                    metadata={
                        "agent": final_agent,
                        "intent": final_intent,
                        "collaboration_mode": final_mode,
                    },
                )

                try:
                    await session_mgr.archive_session(session.session_id)
                except Exception:
                    logger.debug("自动归档会话失败: %s", session.session_id)

        except Exception as e:
            logger.error("流式响应异常: %s", e)
            yield format_sse("error", str(e))
            yield format_sse("status", "error")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/{session_id}", summary="SSE事件流")
async def event_stream(session_id: str) -> StreamingResponse:
    """SSE 事件流端点

    实时推送事件总线中该会话的所有事件，
    包括工具调用、护栏拦截、审批挂起、上下文压缩等。

    前端可通过 EventSource API 订阅此端点，
    实时展示 Agent 执行过程中的关键事件。
    """

    async def _generate():
        try:
            from agent.core.infrastructure.event_bus import subscribe_events, EventType

            event_types = [
                EventType.AGENT_START,
                EventType.AGENT_END,
                EventType.TOOL_CALL,
                EventType.TOOL_RESULT,
                EventType.GUARDRAIL_BLOCK,
                EventType.APPROVAL_PENDING,
                EventType.CONTEXT_COMPACTION,
                EventType.DEGRADATION,
                EventType.INTENT_CLASSIFIED,
                EventType.RETRY,
                EventType.ERROR,
            ]

            async for event in subscribe_events(session_id, event_types):
                if event.data.get("heartbeat"):
                    yield format_sse("heartbeat", "")
                    continue

                yield format_sse(event.event_type.value, json.dumps({
                    "session_id": event.session_id,
                    "data": event.data,
                    "timestamp": event.timestamp,
                }, ensure_ascii=False))

        except Exception as e:
            logger.error("事件流异常: %s", e)
            yield format_sse("error", str(e))

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
