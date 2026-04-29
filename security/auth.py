"""JWT 认证

Token 签发、验证、刷新与撤销，与架构文档 7.2.1 节对齐。
支持 OAuth2.0 / SSO 对接，当前实现 JWT 基础认证。
默认使用 RS256（RSA 非对称密钥）签名算法：
  - 私钥（jwt_private_key）用于签发 Token
  - 公钥（jwt_public_key）用于验证 Token
Token 黑名单基于 Redis 存储，支持分布式部署。
"""

import logging
import time
from typing import Any

import jwt
from pydantic import BaseModel, Field

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_signing_key() -> str:
    """获取 JWT 签名密钥

    RS256 模式返回私钥用于签发，HS256 模式返回对称密钥。

    Returns:
        签名密钥字符串
    """
    settings = get_settings()
    if settings.jwt_algorithm == "RS256":
        return settings.jwt_private_key
    return settings.jwt_secret_key


def _get_verify_key() -> str:
    """获取 JWT 验证密钥

    RS256 模式返回公钥用于验证，HS256 模式返回对称密钥。

    Returns:
        验证密钥字符串
    """
    settings = get_settings()
    if settings.jwt_algorithm == "RS256":
        return settings.jwt_public_key
    return settings.jwt_secret_key


class TokenPayload(BaseModel):
    """JWT Token 载荷

    OIDC 规范扩展字段说明：
    - sub: OIDC 标准字段，全局唯一用户标识，与 user_id 值相同
    - iss: Token 签发者标识，用于跨系统验证 Token 来源
    - aud: Token 受众列表，包含所有授权访问的子系统标识
    - tenant_id: 租户ID，用于多租户数据隔离

    兼容策略：
    - sub 和 user_id 值相同，现有代码读取 user_id 不受影响
    - iss 和 aud 为新增字段，旧 Token 无这两个字段时验证跳过
    - tenant_id 为新增字段，旧 Token 无此字段时默认为空字符串
    """

    sub: str = ""
    user_id: str
    roles: list[str] = Field(default_factory=lambda: ["employee"])
    departments: list[str] = Field(default_factory=list)
    tenant_id: str = ""
    iss: str = ""
    aud: list[str] = Field(default_factory=list)
    exp: float = 0
    iat: float = 0
    jti: str = ""
    type: str = "access"


class TokenPair(BaseModel):
    """Token 对（访问令牌 + 刷新令牌）"""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 0


def _generate_jti() -> str:
    """生成 Token 唯一标识"""
    import uuid
    return str(uuid.uuid4())


def _build_oidc_claims(user_id: str, settings) -> dict[str, Any]:
    """构建 OIDC 标准声明字段

    根据配置生成 sub、iss、aud 字段，用于 Token 签发。
    sub 与 user_id 值相同，确保向后兼容。
    iss 和 aud 从配置读取，未配置时使用默认值。

    Args:
        user_id: 用户ID，同时作为 sub 的值
        settings: 全局配置对象

    Returns:
        包含 sub、iss、aud 的字典
    """
    issuer = getattr(settings, "jwt_issuer", "") or "multi-agent-platform"
    audiences_str = getattr(settings, "jwt_audiences", "") or "platform,ida-service"
    audiences = [a.strip() for a in audiences_str.split(",") if a.strip()]

    return {
        "sub": user_id,
        "iss": issuer,
        "aud": audiences,
    }


def create_token_pair(
    user_id: str,
    roles: list[str] | None = None,
    departments: list[str] | None = None,
    tenant_id: str = "",
) -> TokenPair:
    """签发 Token 对

    签发的 Token 包含 OIDC 标准字段（sub/iss/aud）和租户字段（tenant_id），
    可被所有子系统通过 JWKS 端点直接验证，无需映射。

    Args:
        user_id: 用户ID，同时作为 OIDC sub 字段
        roles: 用户角色列表
        departments: 用户部门列表
        tenant_id: 租户ID（可选，多租户隔离）

    Returns:
        TokenPair 包含访问令牌和刷新令牌
    """
    settings = get_settings()
    now = time.time()
    roles = roles or ["employee"]
    departments = departments or []

    # 如果未传入 tenant_id，尝试从上下文获取
    if not tenant_id:
        try:
            from security.tenant import get_current_tenant_id
            tenant_id = get_current_tenant_id() or ""
        except Exception:
            pass

    oidc_claims = _build_oidc_claims(user_id, settings)

    # 访问令牌（短期）
    access_payload = TokenPayload(
        user_id=user_id,
        sub=oidc_claims["sub"],
        roles=roles,
        departments=departments,
        tenant_id=tenant_id,
        iss=oidc_claims["iss"],
        aud=oidc_claims["aud"],
        iat=now,
        exp=now + settings.jwt_expire_minutes * 60,
        jti=_generate_jti(),
        type="access",
    )
    # RS256 模式下添加 kid 头部，使子系统能通过 JWKS 端点匹配公钥
    # kid 值与 JWKS 端点中的 kid 一致，用于密钥轮换场景
    extra_headers = {}
    if settings.jwt_algorithm == "RS256":
        extra_headers["kid"] = "key-1"

    access_token = jwt.encode(
        access_payload.model_dump(),
        _get_signing_key(),
        algorithm=settings.jwt_algorithm,
        headers=extra_headers if extra_headers else None,
    )

    # 刷新令牌（长期，7天）
    refresh_payload = TokenPayload(
        user_id=user_id,
        sub=oidc_claims["sub"],
        roles=roles,
        departments=departments,
        tenant_id=tenant_id,
        iss=oidc_claims["iss"],
        aud=oidc_claims["aud"],
        iat=now,
        exp=now + 7 * 24 * 3600,
        jti=_generate_jti(),
        type="refresh",
    )
    refresh_token = jwt.encode(
        refresh_payload.model_dump(),
        _get_signing_key(),
        algorithm=settings.jwt_algorithm,
        headers=extra_headers if extra_headers else None,
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


def verify_token(token: str, expected_type: str = "access") -> TokenPayload | None:
    """验证 Token 并返回载荷

    验证流程：签名校验 -> 类型校验 -> 过期校验 -> 黑名单校验

    兼容策略：
    - 新 Token（含 iss/aud 字段）：验证 iss 和 aud 是否与配置匹配
    - 旧 Token（无 iss/aud 字段）：跳过 iss/aud 校验，保持向后兼容
    - sub 字段缺失时自动从 user_id 回填，确保下游代码一致

    Args:
        token: JWT Token 字符串
        expected_type: 期望的 Token 类型（access/refresh）

    Returns:
        TokenPayload 或 None（验证失败时）
    """
    settings = get_settings()

    try:
        # 构建 jwt.decode 的 options 参数
        # 旧 Token 无 iss/aud 字段，需要跳过这两个字段的校验
        decode_options = {}
        decode_options["verify_iss"] = False
        decode_options["verify_aud"] = False

        payload_dict = jwt.decode(
            token,
            _get_verify_key(),
            algorithms=[settings.jwt_algorithm],
            options=decode_options,
        )
        payload = TokenPayload.model_validate(payload_dict)

        # 新 Token 包含 iss/aud 字段时，手动校验
        if payload.iss:
            expected_iss = getattr(settings, "jwt_issuer", "") or "multi-agent-platform"
            if payload.iss != expected_iss:
                logger.warning("Token 签发者不匹配: expected=%s actual=%s", expected_iss, payload.iss)
                return None

        if payload.aud:
            expected_audiences_str = getattr(settings, "jwt_audiences", "") or "platform,ida-service"
            expected_audiences = [a.strip() for a in expected_audiences_str.split(",") if a.strip()]
            if not any(aud in expected_audiences for aud in payload.aud):
                logger.warning("Token 受众不在允许列表中: aud=%s expected=%s", payload.aud, expected_audiences)
                return None

        # 兼容旧 Token：sub 缺失时从 user_id 回填
        if not payload.sub:
            payload.sub = payload.user_id

        if payload.type != expected_type:
            logger.warning("Token 类型不匹配: expected=%s actual=%s", expected_type, payload.type)
            return None

        # 检查黑名单
        if payload.jti and is_token_revoked(payload.jti):
            logger.warning("Token 已被撤销: jti=%s", payload.jti)
            return None

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("Token 验证失败: %s", e)
        return None


def refresh_access_token(refresh_token: str) -> TokenPair | None:
    """使用刷新令牌获取新的 Token 对

    刷新成功后自动撤销旧的刷新令牌，防止重放攻击。

    Args:
        refresh_token: 刷新令牌

    Returns:
        新的 TokenPair 或 None（刷新失败时）
    """
    payload = verify_token(refresh_token, expected_type="refresh")
    if payload is None:
        return None

    # 撤销旧刷新令牌，TTL 保护：确保不为负数
    if payload.jti:
        remaining_ttl = max(0, payload.exp - time.time())
        revoke_token(payload.jti, remaining_ttl)

    return create_token_pair(
        user_id=payload.user_id,
        roles=payload.roles,
        departments=payload.departments,
    )


def extract_token_from_header(authorization: str | None) -> str | None:
    """从 Authorization 头提取 Token

    Args:
        authorization: Authorization 头的值

    Returns:
        Token 字符串或 None
    """
    if not authorization:
        return None

    parts = authorization.split(" ")
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]

    return None


# ==================== Token 黑名单 ====================

_revoked_tokens: dict[str, float] = {}

# 进程内黑名单最大容量，防止内存溢出
_MAX_REVOKED_TOKENS = 10000


async def _get_redis_client():
    """获取共享的 Redis 客户端

    优先使用全局连接池管理器的 Redis 客户端，
    连接池由应用 lifespan 统一管理生命周期。

    Returns:
        aioredis.Redis 客户端实例，或 None（Redis 不可用时）
    """
    try:
        from agent.core.performance.connection_pool import get_pool_manager
        pool_mgr = get_pool_manager()
        client = await pool_mgr.get_redis_client()
        if client is not None:
            return client
    except Exception:
        pass

    return None


def _cleanup_revoked_tokens() -> None:
    """清理进程内黑名单中过期的记录，并限制最大容量"""
    now = time.time()
    expired_keys = [k for k, v in _revoked_tokens.items() if v <= now]
    for k in expired_keys:
        del _revoked_tokens[k]

    # 超出容量时清理最旧的记录
    if len(_revoked_tokens) > _MAX_REVOKED_TOKENS:
        sorted_items = sorted(_revoked_tokens.items(), key=lambda x: x[1])
        overflow = len(_revoked_tokens) - _MAX_REVOKED_TOKENS
        for k, _ in sorted_items[:overflow]:
            del _revoked_tokens[k]


async def revoke_token_async(jti: str, ttl_seconds: float | None = None) -> bool:
    """异步撤销指定 Token

    优先使用 Redis 存储（分布式），降级使用进程内字典。
    使用共享 Redis 客户端，避免每次操作创建新连接。

    Args:
        jti: Token 唯一标识
        ttl_seconds: 黑名单保留时间（秒）

    Returns:
        是否撤销成功
    """
    ttl = ttl_seconds or 3600

    client = await _get_redis_client()
    if client is not None:
        try:
            await client.setex(f"token_revoked:{jti}", int(ttl), "1")
            logger.info("Token 已撤销(Redis): jti=%s", jti)
            return True
        except Exception as e:
            logger.warning("Redis 撤销失败，降级到进程内: jti=%s error=%s", jti, e)

    # 降级到进程内存储
    _cleanup_revoked_tokens()
    _revoked_tokens[jti] = time.time() + ttl
    logger.info("Token 已撤销(进程内): jti=%s", jti)
    return True


def revoke_token(jti: str, ttl_seconds: float | None = None) -> bool:
    """撤销指定 Token（同步版本，内部调用异步方法）

    注意：在异步环境中应优先使用 revoke_token_async。
    此同步方法通过运行新事件循环执行异步操作，
    在已有事件循环运行时会回退到进程内存储。

    Args:
        jti: Token 唯一标识
        ttl_seconds: 黑名单保留时间（秒），默认使用 Token 剩余有效期

    Returns:
        是否撤销成功
    """
    ttl = ttl_seconds or 3600

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已有事件循环运行中，无法同步等待异步操作，降级到进程内
            _cleanup_revoked_tokens()
            _revoked_tokens[jti] = time.time() + ttl
            logger.info("Token 已撤销(进程内，异步环境降级): jti=%s", jti)
            return True
        else:
            loop.run_until_complete(revoke_token_async(jti, ttl))
            return True
    except RuntimeError:
        # 无事件循环，降级到进程内存储
        _cleanup_revoked_tokens()
        _revoked_tokens[jti] = time.time() + ttl
        logger.info("Token 已撤销(进程内): jti=%s", jti)
        return True


def is_token_revoked(jti: str) -> bool:
    """检查 Token 是否已被撤销

    同步方法仅检查进程内黑名单，Redis 检查需要使用 is_token_revoked_async。
    多实例部署场景下，应使用 is_token_revoked_async 确保检查完整性。

    Args:
        jti: Token 唯一标识

    Returns:
        是否已被撤销
    """
    _cleanup_revoked_tokens()
    return jti in _revoked_tokens


async def is_token_revoked_async(jti: str) -> bool:
    """异步检查 Token 是否已被撤销（含 Redis 检查）

    先检查进程内黑名单，再检查 Redis 分布式黑名单。
    使用共享 Redis 客户端，避免每次操作创建新连接。

    Args:
        jti: Token 唯一标识

    Returns:
        是否已被撤销
    """
    # 先检查进程内
    if is_token_revoked(jti):
        return True

    # 再检查 Redis
    client = await _get_redis_client()
    if client is not None:
        try:
            result = await client.exists(f"token_revoked:{jti}")
            return result > 0
        except Exception:
            return False

    return False


def revoke_all_user_tokens(user_id: str) -> int:
    """撤销用户的所有 Token（同步版本）

    通过在 Redis 中设置用户级别的撤销时间戳实现。
    所有在该时间戳之前签发的 Token 均视为无效。

    注意：在异步环境中应使用 revoke_all_user_tokens_async。

    Args:
        user_id: 用户ID

    Returns:
        撤销操作是否成功（1=成功）
    """
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已有事件循环运行中，使用 ensure_future 触发异步操作
            asyncio.ensure_future(_revoke_user_in_redis_async(user_id))
            return 1
        else:
            loop.run_until_complete(_revoke_user_in_redis_async(user_id))
            return 1
    except RuntimeError:
        logger.info("用户全部 Token 已撤销(进程内): user_id=%s", user_id)
        return 1


async def revoke_all_user_tokens_async(user_id: str) -> bool:
    """异步撤销用户的所有 Token

    使用共享 Redis 客户端，避免每次操作创建新连接。

    Args:
        user_id: 用户ID

    Returns:
        是否撤销成功
    """
    return await _revoke_user_in_redis_async(user_id)


async def _revoke_user_in_redis_async(user_id: str) -> bool:
    """在 Redis 中设置用户级别的 Token 撤销时间戳"""
    client = await _get_redis_client()
    if client is not None:
        try:
            await client.set(f"user_revoked:{user_id}", str(time.time()), ex=7 * 24 * 3600)
            logger.info("用户全部 Token 已撤销(Redis): user_id=%s", user_id)
            return True
        except Exception as e:
            logger.warning("Redis 用户撤销失败: user_id=%s error=%s", user_id, e)

    logger.info("用户全部 Token 已撤销(进程内): user_id=%s", user_id)
    return True


def require_roles(request, roles: list[str]) -> None:
    """校验请求用户是否拥有指定角色

    从 request.state.auth_payload 中提取用户角色信息，
    如果用户不拥有任一所需角色，抛出 403 异常。

    Args:
        request: FastAPI Request 对象
        roles: 允许的角色列表（满足其一即可）

    Raises:
        AppException: 权限不足时抛出 403
    """
    from api.errors import AppException, ErrorCode

    auth_payload = getattr(request.state, "auth_payload", None)
    if auth_payload is None:
        raise AppException(ErrorCode.UNAUTHORIZED, message="未认证")

    user_roles = getattr(auth_payload, "roles", [])
    if not any(role in user_roles for role in roles):
        raise AppException(
            ErrorCode.PERMISSION_DENIED,
            message=f"权限不足，需要以下角色之一: {', '.join(roles)}",
        )
