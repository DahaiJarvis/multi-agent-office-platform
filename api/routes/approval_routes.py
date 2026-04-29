"""审批流路由

提供审批单的创建、审批、拒绝、查询和取消接口。
与 Guardrails 联动，敏感操作自动创建审批单。
"""

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.core.approval_flow import get_approval_flow_manager, ApprovalStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approval", tags=["Approval"])


class ApprovalCreateRequest(BaseModel):
    """创建审批单请求"""

    session_id: str = Field(default="", description="关联会话ID")
    user_id: str = Field(default="", description="发起用户ID")
    agent_name: str = Field(default="", description="Agent 名称")
    tool_name: str = Field(min_length=1, description="敏感操作工具名称")
    tool_input: dict[str, Any] = Field(default_factory=dict, description="工具输入参数")
    reason: str = Field(default="", description="审批原因")
    approval_chain: list[dict[str, Any]] = Field(default_factory=list, description="多级审批链配置")
    timeout_hours: int = Field(default=24, ge=1, le=168, description="审批超时时间(小时)")


class ApprovalActionRequest(BaseModel):
    """审批操作请求"""

    approver: str = Field(min_length=1, description="审批人")
    comment: str = Field(default="", description="审批备注/拒绝原因")


class ApprovalResponse(BaseModel):
    """审批单响应"""

    approval_id: str
    session_id: str = ""
    user_id: str = ""
    agent_name: str = ""
    tool_name: str = ""
    reason: str = ""
    status: str = ""
    approver: str = ""
    approver_role: str = ""
    created_at: float = 0
    resolved_at: float = 0
    expires_at: float = 0
    current_step: int = 0
    total_steps: int = 0


class ApprovalListResponse(BaseModel):
    """审批列表响应"""

    items: list[ApprovalResponse]
    total: int


def _to_approval_response(approval: Any) -> ApprovalResponse:
    """将 ApprovalRequest 转换为 API 响应"""
    return ApprovalResponse(
        approval_id=approval.approval_id,
        session_id=approval.session_id,
        user_id=approval.user_id,
        agent_name=approval.agent_name,
        tool_name=approval.tool_name,
        reason=approval.reason,
        status=approval.status.value if isinstance(approval.status, ApprovalStatus) else str(approval.status),
        approver=approval.approver,
        approver_role=approval.approver_role,
        created_at=approval.created_at,
        resolved_at=approval.resolved_at,
        expires_at=approval.expires_at,
        current_step=approval.current_step,
        total_steps=len(approval.approval_chain) if approval.approval_chain else 0,
    )


@router.post("/create", response_model=ApprovalResponse)
async def create_approval(request: ApprovalCreateRequest) -> ApprovalResponse:
    """创建审批单

    手动创建审批单，或由 Guardrails 自动调用。
    """
    mgr = get_approval_flow_manager()
    approval = await mgr.create_approval(
        session_id=request.session_id,
        user_id=request.user_id,
        agent_name=request.agent_name,
        tool_name=request.tool_name,
        tool_input=request.tool_input,
        reason=request.reason or f"敏感操作: {request.tool_name}",
        approval_chain=request.approval_chain or None,
        timeout_hours=request.timeout_hours,
    )
    return _to_approval_response(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval(approval_id: str, request: ApprovalActionRequest) -> ApprovalResponse:
    """审批通过

    如果是多级审批，推进到下一步；如果是最后一步，状态变为 APPROVED。
    """
    mgr = get_approval_flow_manager()
    approval = await mgr.approve(
        approval_id=approval_id,
        approver=request.approver,
        comment=request.comment,
    )
    if approval is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)
    return _to_approval_response(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(approval_id: str, request: ApprovalActionRequest) -> ApprovalResponse:
    """审批拒绝"""
    mgr = get_approval_flow_manager()
    approval = await mgr.reject(
        approval_id=approval_id,
        approver=request.approver,
        reason=request.comment,
    )
    if approval is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)
    return _to_approval_response(approval)


@router.get("/pending", response_model=ApprovalListResponse)
async def list_pending_approvals(
    role: str = "",
    user_id: str = "",
    limit: int = 20,
) -> ApprovalListResponse:
    """查询待审批列表

    支持按审批人角色或发起用户过滤。
    """
    mgr = get_approval_flow_manager()
    approvals = await mgr.get_pending_approvals(
        approver_role=role or None,
        user_id=user_id or None,
        limit=limit,
    )
    return ApprovalListResponse(
        items=[_to_approval_response(a) for a in approvals],
        total=len(approvals),
    )


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(approval_id: str) -> ApprovalResponse:
    """获取审批单详情"""
    mgr = get_approval_flow_manager()
    approval = await mgr.get_approval(approval_id)
    if approval is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)
    return _to_approval_response(approval)


@router.post("/{approval_id}/cancel", response_model=ApprovalResponse)
async def cancel_approval(approval_id: str) -> ApprovalResponse:
    """取消审批单"""
    mgr = get_approval_flow_manager()
    approval = await mgr.cancel(approval_id)
    if approval is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)
    return _to_approval_response(approval)


@router.post("/check-expired", response_model=ApprovalListResponse)
async def check_expired_approvals() -> ApprovalListResponse:
    """检查并标记过期审批

    扫描所有 PENDING 状态的审批单，将超时的标记为 EXPIRED。
    """
    mgr = get_approval_flow_manager()
    expired = await mgr.check_expired()
    return ApprovalListResponse(
        items=[_to_approval_response(a) for a in expired],
        total=len(expired),
    )
