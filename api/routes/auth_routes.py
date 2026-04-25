"""认证路由

提供登录、Token 刷新接口。
生产环境需对接企业微信/钉钉 OAuth2.0，当前为基础 JWT 实现。
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from security.auth import create_token_pair, refresh_access_token, verify_token
from security.audit import record_auth_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    """登录请求"""

    user_id: str = Field(..., description="用户ID")
    password: str = Field(..., description="密码")
    channel: str = Field(default="web", description="接入渠道")


class LoginResponse(BaseModel):
    """登录响应"""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 0
    user_id: str
    roles: list[str]


class RefreshRequest(BaseModel):
    """Token 刷新请求"""

    refresh_token: str = Field(..., description="刷新令牌")


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest) -> LoginResponse:
    """用户登录

    当前为基础实现，验证 user_id 非空即通过。
    生产环境需对接企业微信/钉钉 OAuth2.0 或内部 SSO。
    """
    if not request.user_id:
        raise HTTPException(status_code=400, detail="用户ID不能为空")

    # TODO: 对接企业微信/钉钉 OAuth2.0 或内部 SSO 验证用户身份
    # 当前基础实现：根据 user_id 分配默认角色
    roles = _get_default_roles(request.user_id)

    token_pair = create_token_pair(
        user_id=request.user_id,
        roles=roles,
    )

    record_auth_audit(
        trace_id="",
        user_id=request.user_id,
        channel=request.channel,
        status="success",
        detail="登录成功",
    )

    return LoginResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        token_type=token_pair.token_type,
        expires_in=token_pair.expires_in,
        user_id=request.user_id,
        roles=roles,
    )


@router.post("/refresh")
async def refresh_token(request: RefreshRequest) -> dict:
    """刷新访问令牌"""
    token_pair = refresh_access_token(request.refresh_token)
    if token_pair is None:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")

    return {
        "access_token": token_pair.access_token,
        "refresh_token": token_pair.refresh_token,
        "token_type": token_pair.token_type,
        "expires_in": token_pair.expires_in,
    }


def _get_default_roles(user_id: str) -> list[str]:
    """获取用户默认角色

    当前为简化实现，生产环境应从企业用户中心获取。

    Args:
        user_id: 用户ID

    Returns:
        角色列表
    """
    if user_id.startswith("admin"):
        return ["admin"]
    if user_id.startswith("mgr"):
        return ["manager"]
    if user_id.startswith("hr"):
        return ["hr_specialist"]
    if user_id.startswith("fin"):
        return ["finance"]
    return ["employee"]
