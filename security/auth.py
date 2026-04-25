"""JWT 认证

Token 签发、验证与刷新，与架构文档 7.2.1 节对齐。
支持 OAuth2.0 / SSO 对接，当前实现 JWT 基础认证。
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
    type: str = "access"  # access | refresh


class TokenPair(BaseModel):
    """Token 对（访问令牌 + 刷新令牌）"""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 0


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

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("Token 验证失败: %s", e)
        return None


def refresh_access_token(refresh_token: str) -> TokenPair | None:
    """使用刷新令牌获取新的 Token 对

    Args:
        refresh_token: 刷新令牌

    Returns:
        新的 TokenPair 或 None（刷新失败时）
    """
    payload = verify_token(refresh_token, expected_type="refresh")
    if payload is None:
        return None

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
