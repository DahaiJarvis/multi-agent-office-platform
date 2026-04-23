"""会话管理路由"""

import logging
from fastapi import APIRouter, HTTPException

from api.models.request import SessionCreateRequest, SessionHistoryRequest
from api.models.response import SessionResponse
from agent.core.session_manager import get_session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["Session"])


@router.post("/create", response_model=SessionResponse)
async def create_session(request: SessionCreateRequest) -> SessionResponse:
    """创建新会话"""
    session_mgr = await get_session_manager()
    session = await session_mgr.create_session(
        user_id=request.user_id, channel=request.channel
    )

    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        channel=session.channel,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.message_history),
        active_agents=session.active_agents,
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> SessionResponse:
    """获取会话信息"""
    session_mgr = await get_session_manager()
    session = await session_mgr.get_session(session_id)

    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        channel=session.channel,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.message_history),
        active_agents=session.active_agents,
    )


@router.get("/{session_id}/history")
async def get_session_history(session_id: str, limit: int = 50) -> dict:
    """获取会话消息历史"""
    session_mgr = await get_session_manager()
    session = await session_mgr.get_session(session_id)

    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = session.message_history[-limit:]
    return {
        "session_id": session_id,
        "messages": messages,
        "total": len(session.message_history),
    }


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    """删除会话"""
    session_mgr = await get_session_manager()
    await session_mgr.delete_session(session_id)
    return {"message": "会话已删除", "session_id": session_id}
