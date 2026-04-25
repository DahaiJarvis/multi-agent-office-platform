"""认证路由

提供登录、登出、Token 刷新接口。
生产环境需对接企业微信/钉钉 OAuth2.0，当前为基础 JWT 实现。
"""

import hashlib
import logging
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from security.auth import (
    create_token_pair,
    refresh_access_token,
    verify_token,
    extract_token_from_header,
    revoke_token_async,
    revoke_all_user_tokens,
)
from security.audit import record_auth_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    """登录请求"""

    user_id: str = Field(..., min_length=1, max_length=64, description="用户ID")
    password: str = Field(..., min_length=1, max_length=128, description="密码")
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


class LogoutRequest(BaseModel):
    """登出请求"""

    refresh_token: str = Field(default="", description="刷新令牌（可选，提供则撤销）")


def _hash_password(password: str) -> str:
    """密码哈希（生产环境应使用 bcrypt/argon2）"""
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    return _hash_password(password) == password_hash


# 模拟用户凭证存储（生产环境替换为企业用户中心）
# key: user_id, value: {password_hash, roles}
_USER_STORE: dict[str, dict] = {
    "admin001": {"password_hash": _hash_password("admin123"), "roles": ["admin"]},
    "mgr001": {"password_hash": _hash_password("mgr123"), "roles": ["manager"]},
    "hr001": {"password_hash": _hash_password("hr123"), "roles": ["hr_specialist"]},
    "fin001": {"password_hash": _hash_password("fin123"), "roles": ["finance"]},
    "emp001": {"password_hash": _hash_password("emp123"), "roles": ["employee"]},
}

# 登录失败计数（防暴力破解）
_login_fail_count: dict[str, list[float]] = {}
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 300


def _check_login_rate(user_id: str) -> bool:
    """检查登录频率，防止暴力破解

    Args:
        user_id: 用户ID

    Returns:
        是否允许登录尝试
    """
    now = time.time()
    if user_id in _login_fail_count:
        _login_fail_count[user_id] = [
            t for t in _login_fail_count[user_id] if now - t < _LOGIN_LOCKOUT_SECONDS
        ]
        if len(_login_fail_count[user_id]) >= _MAX_LOGIN_ATTEMPTS:
            return False
    return True


def _record_login_failure(user_id: str) -> None:
    """记录登录失败"""
    if user_id not in _login_fail_count:
        _login_fail_count[user_id] = []
    _login_fail_count[user_id].append(time.time())


def _get_user_roles(user_id: str) -> list[str]:
    """获取用户角色

    优先从用户存储获取，降级使用前缀推断。

    Args:
        user_id: 用户ID

    Returns:
        角色列表
    """
    user = _USER_STORE.get(user_id)
    if user:
        return user.get("roles", ["employee"])

    if user_id.startswith("admin"):
        return ["admin"]
    if user_id.startswith("mgr"):
        return ["manager"]
    if user_id.startswith("hr"):
        return ["hr_specialist"]
    if user_id.startswith("fin"):
        return ["finance"]
    return ["employee"]


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest) -> LoginResponse:
    """用户登录

    验证流程：
    1. 检查登录频率（防暴力破解）
    2. 验证用户凭证
    3. 签发 Token 对
    4. 记录审计日志

    生产环境需对接企业微信/钉钉 OAuth2.0 或内部 SSO。
    """
    if not request.user_id:
        raise AppException(ErrorCode.INVALID_PARAMETER, message="用户ID不能为空")

    # 检查登录频率
    if not _check_login_rate(request.user_id):
        record_auth_audit(
            trace_id="",
            user_id=request.user_id,
            channel=request.channel,
            status="failed",
            detail="登录频率超限，账户临时锁定",
        )
        raise AppException(ErrorCode.LOGIN_RATE_LIMITED, message="登录尝试过于频繁，请5分钟后重试")

    # 验证用户凭证
    user = _USER_STORE.get(request.user_id)
    if user:
        if not _verify_password(request.password, user["password_hash"]):
            _record_login_failure(request.user_id)
            record_auth_audit(
                trace_id="",
                user_id=request.user_id,
                channel=request.channel,
                status="failed",
                detail="密码错误",
            )
            raise AppException(ErrorCode.LOGIN_FAILED, message="用户名或密码错误")
    else:
        # 未在用户存储中的用户，使用前缀推断角色（开发模式兼容）
        from agent.core.config import get_settings
        settings = get_settings()
        if settings.is_production:
            _record_login_failure(request.user_id)
            record_auth_audit(
                trace_id="",
                user_id=request.user_id,
                channel=request.channel,
                status="failed",
                detail="用户不存在",
            )
            raise AppException(ErrorCode.LOGIN_FAILED, message="用户名或密码错误")

    roles = _get_user_roles(request.user_id)

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


@router.post("/logout")
async def logout(request: Request, body: LogoutRequest | None = None) -> dict:
    """用户登出

    撤销当前访问令牌。如果提供刷新令牌，同时撤销刷新令牌。
    支持管理员强制下线（撤销用户所有令牌）。
    """
    auth_header = request.headers.get("Authorization")
    token = extract_token_from_header(auth_header)

    if not token:
        return {"message": "已登出"}

    payload = verify_token(token)
    if payload and payload.jti:
        # 撤销当前访问令牌
        remaining_ttl = payload.exp - time.time()
        if remaining_ttl > 0:
            await revoke_token_async(payload.jti, remaining_ttl)

    # 撤销刷新令牌
    if body and body.refresh_token:
        refresh_payload = verify_token(body.refresh_token, expected_type="refresh")
        if refresh_payload and refresh_payload.jti:
            remaining_ttl = refresh_payload.exp - time.time()
            if remaining_ttl > 0:
                await revoke_token_async(refresh_payload.jti, remaining_ttl)

    if payload:
        record_auth_audit(
            trace_id="",
            user_id=payload.user_id,
            channel="",
            status="success",
            detail="登出成功",
        )

    return {"message": "已登出"}


@router.post("/refresh")
async def refresh_token(request: RefreshRequest) -> dict:
    """刷新访问令牌"""
    token_pair = refresh_access_token(request.refresh_token)
    if token_pair is None:
        raise AppException(ErrorCode.REFRESH_TOKEN_INVALID, message="刷新令牌无效或已过期")

    return {
        "access_token": token_pair.access_token,
        "refresh_token": token_pair.refresh_token,
        "token_type": token_pair.token_type,
        "expires_in": token_pair.expires_in,
    }
