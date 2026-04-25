"""Agent 交互路由

提供同步和流式两种对话接口:
  - POST /agent/chat: 同步对话，等待完整响应后返回
  - POST /agent/chat/stream: 流式对话，SSE 逐 Token 推送响应
"""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.errors import AppException, ErrorCode
from api.models.request import ChatRequest
from api.models.response import ChatResponse
from agent.core.session_manager import get_session_manager
from agent.teams.routing import route_and_execute, route_and_execute_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse)
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

    return ChatResponse(
        session_id=session.session_id,
        message=reply,
        agent_name=agent_name,
        intent=intent,
        collaboration_mode=collaboration_mode,
    )


@router.post("/chat/stream")
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

            async for event in route_and_execute_stream(
                user_message=request.message,
                session_id=session.session_id,
                user_id=request.user_id,
                session=session,
            ):
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


# ==================== 对话反馈 ====================

from pydantic import BaseModel, Field
from agent.core.feedback import FeedbackType


class FeedbackRequestBody(BaseModel):
    """反馈请求体"""

    session_id: str = Field(..., description="会话ID")
    message_index: int = Field(..., description="消息在会话中的索引位置")
    feedback_type: FeedbackType = Field(..., description="反馈类型: thumbs_up / thumbs_down")
    comment: str | None = Field(default=None, description="用户补充说明")
    agent_name: str | None = Field(default=None, description="Agent名称")
    intent: str | None = Field(default=None, description="意图标签")


@router.post("/feedback")
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


@router.get("/feedback/stats")
async def get_feedback_stats(date: str | None = None) -> dict:
    """查询反馈统计"""
    from agent.core.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_daily_stats(date)
    return stats.model_dump()


@router.get("/feedback/stats/{agent_name}")
async def get_agent_feedback_stats(agent_name: str, date: str | None = None) -> dict:
    """查询指定 Agent 的反馈统计"""
    from agent.core.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_agent_stats(agent_name, date)
    return stats.model_dump()
