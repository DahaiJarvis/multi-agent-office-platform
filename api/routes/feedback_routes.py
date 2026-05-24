"""Agent 对话反馈路由

提供对话反馈相关接口:
  - POST /agent/feedback: 提交对话反馈（点赞/点踩）
  - GET /agent/feedback/stats: 查询反馈统计
  - GET /agent/feedback/stats/{agent_name}: 查询指定Agent反馈统计
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from agent.core.observability.feedback import FeedbackType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent-Feedback"])


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
    from agent.core.observability.feedback import get_feedback_service, FeedbackRequest

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
    from agent.core.observability.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_daily_stats(date)
    return stats.model_dump()


@router.get("/feedback/stats/{agent_name}", summary="查询指定Agent反馈统计")
async def get_agent_feedback_stats(agent_name: str, date: str | None = None) -> dict:
    """查询指定 Agent 的反馈统计"""
    from agent.core.observability.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_agent_stats(agent_name, date)
    return stats.model_dump()
