"""Agent 交互路由

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
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from api.models.request import ChatRequest
from api.models.response import ChatResponse
from agent.core.feedback import FeedbackType
from agent.core.session_manager import get_session_manager
from agent.teams.routing import route_and_execute, route_and_execute_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse, summary="同步对话")
async def chat(request: ChatRequest) -> ChatResponse:
    """处理用户对话请求（同步模式）

    流程：获取/创建会话 -> 追加用户消息 -> Supervisor 路由 -> Agent 执行 -> 返回结果
    """
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
        content=request.message,
    )

    result = await route_and_execute(
        user_message=request.message,
        session_id=session.session_id,
        user_id=request.user_id,
        session=session,
        knowledge_base_id=request.knowledge_base_id,
    )

    reply = result.get("message", "处理完成")
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

    # 自动归档到 L3，确保历史列表可查询
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
    """
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
        content=request.message,
    )

    async def event_generator():
        try:
            yield _format_sse("session_id", session.session_id)

            full_message = ""
            final_agent = "Supervisor"
            final_intent = ""
            final_mode = ""

            # 创建事件总线订阅，并行监听事件总线事件
            event_bus_gen: AsyncGenerator[str, None] | None = None
            try:
                from agent.core.event_bus import subscribe_events, EventType

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
                        yield _format_sse("bus_event", json.dumps({
                            "event_type": event.event_type.value,
                            "data": event.data,
                            "timestamp": event.timestamp,
                        }, ensure_ascii=False))

                event_bus_gen = _listen_event_bus()
            except Exception:
                event_bus_gen = None

            # 使用 asyncio 合并流式对话和事件总线事件
            stream_gen = route_and_execute_stream(
                user_message=request.message,
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
                        yield _format_sse("intent", json.dumps({
                            "intent": event["intent"],
                            "confidence": event["confidence"],
                            "agent": event["agent"],
                            "mode": event["mode"],
                        }, ensure_ascii=False))

                    elif event_type == "clarification":
                        yield _format_sse("chunk", event["message"])
                        yield _format_sse("status", "clarification_needed")
                        full_message = event["message"]

                    elif event_type == "chunk":
                        yield _format_sse("chunk", event["content"])
                        final_agent = event.get("agent_name", final_agent)

                    elif event_type == "tool_call":
                        yield _format_sse("tool_call", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "tools": event.get("tools", []),
                        }, ensure_ascii=False))

                    elif event_type == "tool_result":
                        yield _format_sse("tool_result", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "tool_name": event.get("tool_name", ""),
                            "is_error": event.get("is_error", False),
                            "content": event.get("content", ""),
                        }, ensure_ascii=False))

                    elif event_type == "thought":
                        yield _format_sse("thought", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "content": event.get("content", ""),
                        }, ensure_ascii=False))

                    elif event_type == "handoff":
                        yield _format_sse("handoff", json.dumps({
                            "from_agent": event.get("from_agent", ""),
                            "to_agent": event.get("to_agent", ""),
                        }, ensure_ascii=False))

                    elif event_type == "complete":
                        final_agent = event.get("agent_name", final_agent)
                        final_intent = event.get("intent", "")
                        final_mode = event.get("mode", "")
                        full_message = event.get("full_message", full_message)
                        yield _format_sse("agent_name", final_agent)
                        yield _format_sse("intent", final_intent)
                        yield _format_sse("collaboration_mode", final_mode)
                        yield _format_sse("status", "completed")

                    elif event_type == "error":
                        yield _format_sse("error", event["message"])
                        yield _format_sse("status", "error")

            # 优先处理流式对话，同时尝试获取事件总线事件
            try:
                async for sse_data in _consume_stream():
                    yield sse_data

                    # 在每个 chunk 之后非阻塞地检查事件总线是否有事件
                    # 使用 asyncio.wait_for 设置短超时，避免阻塞主流程
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
                # 确保关闭事件总线订阅，防止内存泄漏
                if event_bus_gen is not None:
                    try:
                        await event_bus_gen.aclose()
                    except Exception:
                        pass

            if full_message:
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

                # 自动归档到 L3，确保历史列表可查询
                try:
                    await session_mgr.archive_session(session.session_id)
                except Exception:
                    logger.debug("自动归档会话失败: %s", session.session_id)

        except Exception as e:
            logger.error("流式响应异常: %s", e)
            yield _format_sse("error", str(e))
            yield _format_sse("status", "error")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event: str, data: str) -> str:
    """格式化 SSE 事件

    Args:
        event: 事件类型
        data: 事件数据

    Returns:
        SSE 格式字符串
    """
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


# ==================== 事件流 ====================


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
            from agent.core.event_bus import subscribe_events, EventType

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
                    yield _format_sse("heartbeat", "")
                    continue

                yield _format_sse(event.event_type.value, json.dumps({
                    "session_id": event.session_id,
                    "data": event.data,
                    "timestamp": event.timestamp,
                }, ensure_ascii=False))

        except Exception as e:
            logger.error("事件流异常: %s", e)
            yield _format_sse("error", str(e))

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 对话反馈 ====================


class FeedbackRequestBody(BaseModel):
    """反馈请求体"""

    session_id: str = Field(..., description="会话ID")
    message_index: int = Field(..., description="消息在会话中的索引位置")
    feedback_type: FeedbackType = Field(..., description="反馈类型: thumbs_up / thumbs_down")
    comment: str | None = Field(default=None, description="用户补充说明")
    agent_name: str | None = Field(default=None, description="Agent名称")
    intent: str | None = Field(default=None, description="意图标签")


@router.post("/feedback", summary="提交对话反馈")
async def submit_feedback(
    body: FeedbackRequestBody,
    user_id: str = "anonymous",
) -> dict:
    """提交对话反馈（点赞/点踩）"""
    from agent.core.feedback import get_feedback_service, FeedbackRequest

    service = get_feedback_service()
    request = FeedbackRequest(
        session_id=body.session_id,
        message_index=body.message_index,
        feedback_type=body.feedback_type,
        comment=body.comment,
        user_id=user_id,
        agent_name=body.agent_name,
        intent=body.intent,
    )

    success = await service.submit_feedback(request)
    if not success:
        raise AppException(ErrorCode.INTERNAL_ERROR, message="反馈提交失败")

    return {"status": "ok", "message": "反馈已提交"}


@router.get("/feedback/stats", summary="查询反馈统计")
async def get_feedback_stats(date: str | None = None) -> dict:
    """查询反馈统计"""
    from agent.core.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_daily_stats(date)
    return stats.model_dump()


@router.get("/feedback/stats/{agent_name}", summary="查询指定Agent反馈统计")
async def get_agent_feedback_stats(agent_name: str, date: str | None = None) -> dict:
    """查询指定 Agent 的反馈统计"""
    from agent.core.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_agent_stats(agent_name, date)
    return stats.model_dump()
