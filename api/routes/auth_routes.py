"""认证路由

提供登录、登出、Token 刷新、SSO 接口。
支持 JWT 基础认证和 OAuth2.0/OIDC SSO 企业身份集成。
"""

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
    hash_password,
    verify_password,
)
from security.audit import record_auth_audit
from security.user_store import get_user_store
from security.sso import (
    SSOProviderType,
    build_sso_authorization,
    handle_sso_callback,
    map_sso_user_to_local,
    list_sso_providers,
)

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


def _upgrade_user_password_hash(password: str) -> None:
    """将旧版 SHA-256 哈希升级为 bcrypt

    在用户存储中找到使用 SHA-256 哈希且密码匹配的用户，
    将其哈希升级为 bcrypt 并持久化到数据库。

    Args:
        password: 明文密码
    """
    import hashlib
    sha256_hash = hashlib.sha256(password.encode()).hexdigest()
    store = get_user_store()
    for user_id, user_record in store._cache.items():
        if user_record.password_hash == sha256_hash:
            new_hash = hash_password(password)
            store._cache[user_id].password_hash = new_hash
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(store.update_password_hash(user_id, new_hash))
            except RuntimeError:
                pass
            logger.info("用户密码哈希已升级为 bcrypt: user_id=%s", user_id)
            break


# 内存用户存储（启动时从数据库加载，作为 L1 缓存使用）
# 数据库不可用时作为降级存储
_USER_STORE: dict[str, dict] = {}

# 统一身份映射表：SSO 外部标识 -> 本地 uid
# 用于将不同 SSO 提供者的用户映射到统一的全局用户标识
# key 格式: "{provider_type}:{external_id}"，value 为本地 user_id
_USER_IDENTITIES: dict[str, str] = {}

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

    优先从数据库用户存储获取，降级使用前缀推断。

    Args:
        user_id: 用户ID

    Returns:
        角色列表
    """
    store = get_user_store()
    record = store._cache.get(user_id)
    if record:
        return record.roles

    if user_id.startswith("admin"):
        return ["admin"]
    if user_id.startswith("mgr"):
        return ["manager"]
    if user_id.startswith("hr"):
        return ["hr_specialist"]
    if user_id.startswith("fin"):
        return ["finance"]
    return ["employee"]


@router.post("/login", response_model=LoginResponse, summary="用户登录")
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

    # 验证用户凭证（从数据库查询）
    store = get_user_store()
    user_record = await store.get_user(request.user_id)
    if user_record:
        if not verify_password(request.password, user_record.password_hash):
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
            _upgrade_user_password_hash(request.password)
    else:
        # 未在用户存储中的用户，使用前缀推断角色（开发模式兼容）
        from agent.core.infrastructure.config import get_settings
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


@router.post("/logout", summary="用户登出")
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


@router.post("/refresh", summary="刷新令牌")
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


# ==================== SSO 企业身份集成 ====================


class SSOAuthorizeRequest(BaseModel):
    """SSO 授权请求"""

    provider: str = Field(..., description="SSO 提供者类型: entra_id/okta/wecom/dingtalk")
    redirect_uri: str | None = Field(default=None, description="可选的回调地址覆盖")


class SSOAuthorizeResponse(BaseModel):
    """SSO 授权响应"""

    authorization_url: str = Field(description="授权 URL，前端需重定向到此地址")
    state: str = Field(description="CSRF 防护 state 参数")
    provider: str = Field(description="SSO 提供者类型")


class SSOCallbackRequest(BaseModel):
    """SSO 回调请求"""

    provider: str = Field(..., description="SSO 提供者类型")
    code: str = Field(..., description="IdP 返回的授权码")
    state: str = Field(..., description="CSRF 防护 state 参数")


class SSOProvidersResponse(BaseModel):
    """SSO 提供者列表响应"""

    providers: list[str] = Field(description="已注册的 SSO 提供者类型列表")


@router.get("/sso/providers", response_model=SSOProvidersResponse, summary="获取SSO提供者列表")
async def get_sso_providers() -> SSOProvidersResponse:
    """获取已注册的 SSO 提供者列表

    返回当前系统支持的所有 SSO 身份提供者。
    前端可据此动态渲染 SSO 登录按钮。
    """
    providers = list_sso_providers()
    return SSOProvidersResponse(providers=providers)


@router.post("/sso/authorize", response_model=SSOAuthorizeResponse, summary="发起SSO授权")
async def sso_authorize(request: SSOAuthorizeRequest) -> SSOAuthorizeResponse:
    """发起 SSO 授权

    构建 SSO 授权 URL 并返回给前端，前端需将用户重定向到此 URL。
    授权完成后 IdP 会回调 /auth/sso/callback 端点。

    支持的提供者：
    - entra_id: Microsoft Entra ID (Azure AD)
    - okta: Okta
    - wecom: 企业微信
    - dingtalk: 钉钉
    """
    valid_providers = [e.value for e in SSOProviderType]
    if request.provider not in valid_providers:
        raise AppException(
            ErrorCode.SSO_PROVIDER_NOT_FOUND,
            message=f"不支持的 SSO 提供者: {request.provider}，支持: {', '.join(valid_providers)}",
        )

    try:
        auth_params = build_sso_authorization(request.provider, request.redirect_uri)
    except ValueError as e:
        raise AppException(ErrorCode.SSO_PROVIDER_NOT_FOUND, message=str(e))

    record_auth_audit(
        trace_id="",
        user_id="",
        channel=request.provider,
        status="success",
        detail=f"SSO 授权发起: provider={request.provider}",
    )

    return SSOAuthorizeResponse(
        authorization_url=auth_params.authorization_url,
        state=auth_params.state,
        provider=request.provider,
    )


@router.post("/sso/callback", response_model=LoginResponse, summary="SSO回调处理")
async def sso_callback(request: SSOCallbackRequest) -> LoginResponse:
    """处理 SSO 授权回调

    IdP 认证完成后回调此端点，用授权码交换 Token 并提取用户信息，
    映射到本地用户后签发 JWT Token 对。

    流程：
    1. 验证 state 参数（CSRF 防护）
    2. 用授权码交换 IdP Token
    3. 提取用户信息
    4. 映射到本地用户
    5. 签发 JWT Token 对
    """
    try:
        sso_result = await handle_sso_callback(request.provider, request.code, request.state)
    except ValueError as e:
        record_auth_audit(
            trace_id="",
            user_id="",
            channel=request.provider,
            status="failed",
            detail=f"SSO 回调失败: {str(e)}",
        )
        raise AppException(ErrorCode.SSO_CALLBACK_FAILED, message=str(e))
    except Exception as e:
        record_auth_audit(
            trace_id="",
            user_id="",
            channel=request.provider,
            status="failed",
            detail=f"SSO 回调异常: {str(e)}",
        )
        raise AppException(ErrorCode.SSO_CALLBACK_FAILED, message=f"SSO 认证失败: {str(e)}")

    try:
        local_user = map_sso_user_to_local(sso_result.user_info, request.provider)
    except Exception as e:
        logger.error("SSO 用户映射失败: %s", e)
        raise AppException(ErrorCode.SSO_USER_MAPPING_FAILED, message="SSO 用户映射失败")

    # 统一身份映射：确保 SSO 用户的 user_id 作为全局唯一的 sub
    # 查找或注册 SSO 外部标识到本地 uid 的映射
    identity_key = f"{request.provider}:{sso_result.user_info.external_id}"
    if identity_key in _USER_IDENTITIES:
        unified_uid = _USER_IDENTITIES[identity_key]
        logger.info("SSO 用户身份映射命中: %s -> %s", identity_key, unified_uid)
    else:
        unified_uid = local_user["user_id"]
        _USER_IDENTITIES[identity_key] = unified_uid
        logger.info("SSO 用户身份映射注册: %s -> %s", identity_key, unified_uid)

    # 使用统一 uid 签发 Token，确保 sub 与 user_id 一致
    token_pair = create_token_pair(
        user_id=unified_uid,
        roles=local_user["roles"],
        departments=local_user.get("departments", []),
    )

    record_auth_audit(
        trace_id="",
        user_id=unified_uid,
        channel=request.provider,
        status="success",
        detail=f"SSO 登录成功: provider={request.provider} external_id={sso_result.user_info.external_id}",
    )

    return LoginResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        token_type=token_pair.token_type,
        expires_in=token_pair.expires_in,
        user_id=unified_uid,
        roles=local_user["roles"],
    )
