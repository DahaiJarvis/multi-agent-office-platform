"""会话管理路由

提供会话的创建、查询、归档和历史记录接口。
支持 L2/L3 两级存储：活跃会话在 Redis，归档会话在 PostgreSQL。
"""

import logging
from fastapi import APIRouter

from api.errors import AppException, ErrorCode
from api.models.request import SessionCreateRequest
from api.models.response import SessionResponse
from agent.core.session_manager import get_session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["Session"])


@router.post("/create", response_model=SessionResponse, summary="创建会话")
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


@router.get("/{session_id}", response_model=SessionResponse, summary="获取会话信息")
async def get_session(session_id: str) -> SessionResponse:
    """获取会话信息（优先 L2 Redis，未命中则从 L3 PostgreSQL 恢复）"""
    session_mgr = await get_session_manager()
    session = await session_mgr.get_session(session_id)

    if session is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND)

    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        channel=session.channel,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.message_history),
        active_agents=session.active_agents,
    )


@router.get("/{session_id}/history", summary="获取会话消息历史")
async def get_session_history(session_id: str, limit: int = 50) -> dict:
    """获取会话消息历史"""
    session_mgr = await get_session_manager()
    session = await session_mgr.get_session(session_id)

    if session is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND)

    messages = session.message_history[-limit:]
    return {
        "session_id": session_id,
        "messages": messages,
        "total": len(session.message_history),
    }


@router.post("/{session_id}/archive", summary="归档会话")
async def archive_session(session_id: str) -> dict:
    """归档会话到 L3 长期存储

    将活跃会话从 L2 Redis 持久化到 L3 PostgreSQL，
    用于会话过期后的历史恢复。
    """
    session_mgr = await get_session_manager()
    success = await session_mgr.archive_session(session_id)

    if not success:
        raise AppException(ErrorCode.INTERNAL_ERROR, message="会话归档失败")

    return {"message": "会话已归档", "session_id": session_id}


@router.get("/user/{user_id}/history", summary="查询用户历史会话")
async def list_user_sessions(
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """查询用户的归档会话列表

    从 L3 PostgreSQL 查询用户的历史会话，返回摘要信息。
    """
    session_mgr = await get_session_manager()
    sessions = await session_mgr.list_archived_sessions(user_id, limit, offset)

    return {
        "user_id": user_id,
        "sessions": sessions,
        "count": len(sessions),
    }


@router.delete("/{session_id}", summary="删除会话")
async def delete_session(session_id: str) -> dict:
    """删除会话"""
    session_mgr = await get_session_manager()
    await session_mgr.delete_session(session_id)
    return {"message": "会话已删除", "session_id": session_id}
