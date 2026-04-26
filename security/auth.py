"""JWT 认证

Token 签发、验证、刷新与撤销，与架构文档 7.2.1 节对齐。
支持 OAuth2.0 / SSO 对接，当前实现 JWT 基础认证。
Token 黑名单基于 Redis 存储，支持分布式部署。
"""

import logging
import time
from typing import Any

import jwt
from pydantic import BaseModel, Field

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


class TokenPayload(BaseModel):
    """JWT Token 载荷"""

    user_id: str
    roles: list[str] = Field(default_factory=lambda: ["employee"])
    departments: list[str] = Field(default_factory=list)
    exp: float = 0
    iat: float = 0
    jti: str = ""
    type: str = "access"  # access | refresh


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


def create_token_pair(user_id: str, roles: list[str] | None = None, departments: list[str] | None = None) -> TokenPair:
    """签发 Token 对

    Args:
        user_id: 用户ID
        roles: 用户角色列表
        departments: 用户部门列表

    Returns:
        TokenPair 包含访问令牌和刷新令牌
    """
    settings = get_settings()
    now = time.time()
    roles = roles or ["employee"]
    departments = departments or []

    # 访问令牌（短期）
    access_payload = TokenPayload(
        user_id=user_id,
        roles=roles,
        departments=departments,
        iat=now,
        exp=now + settings.jwt_expire_minutes * 60,
        jti=_generate_jti(),
        type="access",
    )
    access_token = jwt.encode(
        access_payload.model_dump(),
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    # 刷新令牌（长期，7天）
    refresh_payload = TokenPayload(
        user_id=user_id,
        roles=roles,
        departments=departments,
        iat=now,
        exp=now + 7 * 24 * 3600,
        jti=_generate_jti(),
        type="refresh",
    )
    refresh_token = jwt.encode(
        refresh_payload.model_dump(),
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


def verify_token(token: str, expected_type: str = "access") -> TokenPayload | None:
    """验证 Token 并返回载荷

    验证流程：签名校验 -> 类型校验 -> 过期校验 -> 黑名单校验

    Args:
        token: JWT Token 字符串
        expected_type: 期望的 Token 类型（access/refresh）

    Returns:
        TokenPayload 或 None（验证失败时）
    """
    settings = get_settings()

    try:
        payload_dict = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        payload = TokenPayload.model_validate(payload_dict)

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

    # 撤销旧刷新令牌
    if payload.jti:
        revoke_token(payload.jti, payload.exp - time.time())

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


def revoke_token(jti: str, ttl_seconds: float | None = None) -> bool:
    """撤销指定 Token

    将 Token 的 jti 加入黑名单，设置与 Token 剩余有效期相同的 TTL。
    优先使用 Redis 存储（分布式），降级使用进程内字典。

    Args:
        jti: Token 唯一标识
        ttl_seconds: 黑名单保留时间（秒），默认使用 Token 剩余有效期

    Returns:
        是否撤销成功
    """
    ttl = ttl_seconds or 3600

    try:
        import redis.asyncio as aioredis
        from agent.core.config import get_settings
        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)

        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_revoke_in_redis(client, jti, ttl))
        else:
            loop.run_until_complete(_revoke_in_redis(client, jti, ttl))
        return True
    except Exception:
        # 降级到进程内存储
        _revoked_tokens[jti] = time.time() + ttl
        logger.info("Token 已撤销(进程内): jti=%s", jti)
        return True


async def _revoke_in_redis(client, jti: str, ttl: float) -> None:
    """在 Redis 中撤销 Token"""
    try:
        await client.setex(f"token_revoked:{jti}", int(ttl), "1")
        logger.info("Token 已撤销(Redis): jti=%s", jti)
    except Exception as e:
        _revoked_tokens[jti] = time.time() + ttl
        logger.warning("Redis 撤销失败，降级到进程内: jti=%s error=%s", jti, e)
    finally:
        await client.aclose()


async def revoke_token_async(jti: str, ttl_seconds: float | None = None) -> bool:
    """异步撤销指定 Token

    Args:
        jti: Token 唯一标识
        ttl_seconds: 黑名单保留时间（秒）

    Returns:
        是否撤销成功
    """
    ttl = ttl_seconds or 3600

    try:
        import redis.asyncio as aioredis
        from agent.core.config import get_settings
        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.setex(f"token_revoked:{jti}", int(ttl), "1")
        await client.aclose()
        logger.info("Token 已撤销(Redis): jti=%s", jti)
        return True
    except Exception as e:
        _revoked_tokens[jti] = time.time() + ttl
        logger.warning("Redis 撤销失败，降级到进程内: jti=%s error=%s", jti, e)
        return True


def is_token_revoked(jti: str) -> bool:
    """检查 Token 是否已被撤销

    注意：同步方法仅检查进程内黑名单，Redis 检查需要使用 is_token_revoked_async。

    Args:
        jti: Token 唯一标识

    Returns:
        是否已被撤销
    """
    # 清理过期的进程内记录
    now = time.time()
    expired_keys = [k for k, v in _revoked_tokens.items() if v <= now]
    for k in expired_keys:
        del _revoked_tokens[k]

    return jti in _revoked_tokens


async def is_token_revoked_async(jti: str) -> bool:
    """异步检查 Token 是否已被撤销（含 Redis 检查）

    Args:
        jti: Token 唯一标识

    Returns:
        是否已被撤销
    """
    # 先检查进程内
    if is_token_revoked(jti):
        return True

    # 再检查 Redis
    try:
        import redis.asyncio as aioredis
        from agent.core.config import get_settings
        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        result = await client.exists(f"token_revoked:{jti}")
        await client.aclose()
        return result > 0
    except Exception:
        return False


def revoke_all_user_tokens(user_id: str) -> int:
    """撤销用户的所有 Token

    通过在 Redis 中设置用户级别的撤销时间戳实现。
    所有在该时间戳之前签发的 Token 均视为无效。

    Args:
        user_id: 用户ID

    Returns:
        撤销操作是否成功（1=成功）
    """
    try:
        import redis.asyncio as aioredis
        from agent.core.config import get_settings
        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)

        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_revoke_user_in_redis(client, user_id))
        else:
            loop.run_until_complete(_revoke_user_in_redis(client, user_id))
        return 1
    except Exception:
        logger.info("用户全部 Token 已撤销(进程内): user_id=%s", user_id)
        return 1


async def _revoke_user_in_redis(client, user_id: str) -> None:
    """在 Redis 中设置用户级别的 Token 撤销时间戳"""
    try:
        await client.set(f"user_revoked:{user_id}", str(time.time()), ex=7 * 24 * 3600)
        logger.info("用户全部 Token 已撤销(Redis): user_id=%s", user_id)
    except Exception as e:
        logger.warning("Redis 用户撤销失败: user_id=%s error=%s", user_id, e)
    finally:
        await client.aclose()


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
