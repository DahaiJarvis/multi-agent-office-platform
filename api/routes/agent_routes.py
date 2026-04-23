"""Agent 交互路由"""

import logging
from fastapi import APIRouter, HTTPException

from api.models.request import ChatRequest
from api.models.response import ChatResponse
from agent.core.session_manager import get_session_manager
from agent.teams.routing import route_and_execute

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """处理用户对话请求

    流程：获取/创建会话 -> 追加用户消息 -> Supervisor 路由 -> Agent 执行 -> 返回结果
    """
    session_mgr = await get_session_manager()

    if request.session_id:
        session = await session_mgr.get_session(request.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
    else:
        session = await session_mgr.create_session(
            user_id=request.user_id, channel=request.channel
        )

    await session_mgr.append_message(
        session_id=session.session_id,
        role="user",
        content=request.message,
    )

    # 通过 Supervisor 路由并执行任务（传入会话状态以注入上下文）
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
