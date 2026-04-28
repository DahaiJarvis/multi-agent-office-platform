"""嵌入式 Widget API

提供第三方应用嵌入 AI 助手的接口，支持：
  - 生成嵌入 Token（独立于用户 JWT 的有限权限 Token）
  - Widget 配置获取
  - 会话隔离
  - CORS 白名单管理

与 M365 Copilot 嵌入 Office 应用、Coze 嵌入飞书/钉钉对齐。
"""

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from security.auth import require_roles
from agent.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embed", tags=["嵌入式 Widget"])


class EmbedTokenRequest(BaseModel):
    """生成嵌入 Token 请求"""

    domain: str = Field(description="嵌入域名，如 'crm.example.com'")
    theme: str = Field(default="light", description="主题: light / dark / auto")
    position: str = Field(default="bottom-right", description="Widget 位置: bottom-right / bottom-left")
    agent_name: str = Field(default="", description="指定 Agent 名称，空则使用默认")
    locale: str = Field(default="zh-CN", description="语言设置")
    features: dict[str, bool] = Field(
        default_factory=lambda: {
            "chat": True,
            "streaming": True,
            "file_upload": False,
            "voice_input": False,
        },
        description="启用的功能",
    )


class EmbedTokenResponse(BaseModel):
    """嵌入 Token 响应"""

    token: str
    expires_in: int
    widget_url: str
    config: dict[str, Any]


class EmbedConfigResponse(BaseModel):
    """Widget 配置响应"""

    theme: str
    position: str
    agent_name: str
    locale: str
    features: dict[str, bool]
    api_base: str
    sse_enabled: bool


# 嵌入 Token 存储
_embed_tokens: dict[str, dict[str, Any]] = {}

# 域名白名单
_allowed_domains: set[str] = set()

EMBED_TOKEN_TTL = 3600 * 24  # 24 小时


def _init_allowed_domains() -> None:
    """从配置加载允许嵌入的域名"""
    settings = get_settings()
    origins = settings.cors_origins_list
    for origin in origins:
        if origin != "*":
            domain = origin.replace("https://", "").replace("http://", "").split(":")[0]
            _allowed_domains.add(domain)


_init_allowed_domains()


def _generate_embed_token(domain: str, config: dict[str, Any]) -> str:
    """生成嵌入 Token

    使用 HMAC-SHA256 签名，包含域名、配置哈希和过期时间。
    签名密钥优先使用 jwt_secret_key（HS256 模式），
    RS256 模式下使用 mcp_api_key 作为 HMAC 密钥。
    """
    settings = get_settings()
    secret = settings.jwt_secret_key or settings.mcp_api_key or "embed-default-secret"

    payload = f"{domain}:{config.get('agent_name', '')}:{int(time.time())}:{uuid.uuid4().hex[:8]}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"embed.{payload}.{signature[:16]}"
    return token


def verify_embed_token(token: str) -> dict[str, Any] | None:
    """验证嵌入 Token

    Returns:
        Token 对应的配置信息，或 None（无效/过期）
    """
    token_data = _embed_tokens.get(token)
    if not token_data:
        return None

    if time.time() > token_data.get("expires_at", 0):
        _embed_tokens.pop(token, None)
        return None

    return token_data


@router.post("/token", response_model=EmbedTokenResponse)
async def generate_embed_token(request: Request, body: EmbedTokenRequest) -> EmbedTokenResponse:
    """生成嵌入 Token

    第三方应用调用此接口获取嵌入 Token，用于在其页面中加载 AI 助手 Widget。
    """
    require_roles(request, ["admin"])

    if _allowed_domains and body.domain not in _allowed_domains:
        raise AppException(
            ErrorCode.PERMISSION_DENIED,
            message=f"域名 {body.domain} 未在白名单中，请联系管理员添加",
        )

    config = {
        "domain": body.domain,
        "theme": body.theme,
        "position": body.position,
        "agent_name": body.agent_name,
        "locale": body.locale,
        "features": body.features,
    }

    token = _generate_embed_token(body.domain, config)

    _embed_tokens[token] = {
        **config,
        "expires_at": time.time() + EMBED_TOKEN_TTL,
        "created_by": getattr(getattr(request.state, "auth_payload", None), "user_id", "unknown"),
    }

    return EmbedTokenResponse(
        token=token,
        expires_in=EMBED_TOKEN_TTL,
        widget_url=f"/embed/widget?token={token}",
        config=config,
    )


@router.get("/config", response_model=EmbedConfigResponse)
async def get_embed_config(token: str) -> EmbedConfigResponse:
    """获取 Widget 配置

    Widget 加载时调用此接口获取渲染配置。
    """
    token_data = verify_embed_token(token)
    if not token_data:
        raise AppException(ErrorCode.UNAUTHORIZED, message="无效或已过期的嵌入 Token")

    settings = get_settings()
    api_base = f"http://{settings.api_host}:{settings.api_port}" if settings.environment == "development" else ""

    return EmbedConfigResponse(
        theme=token_data.get("theme", "light"),
        position=token_data.get("position", "bottom-right"),
        agent_name=token_data.get("agent_name", ""),
        locale=token_data.get("locale", "zh-CN"),
        features=token_data.get("features", {}),
        api_base=api_base,
        sse_enabled=True,
    )


@router.get("/domains", response_model=list[str])
async def list_allowed_domains(request: Request) -> list[str]:
    """列出允许嵌入的域名白名单"""
    require_roles(request, ["admin"])
    return list(_allowed_domains)


@router.post("/domains", response_model=list[str])
async def add_allowed_domain(request: Request, domain: str) -> list[str]:
    """添加允许嵌入的域名"""
    require_roles(request, ["admin"])
    _allowed_domains.add(domain)
    logger.info("嵌入域名白名单已添加: %s", domain)
    return list(_allowed_domains)


@router.delete("/domains/{domain}", response_model=list[str])
async def remove_allowed_domain(request: Request, domain: str) -> list[str]:
    """移除允许嵌入的域名"""
    require_roles(request, ["admin"])
    _allowed_domains.discard(domain)
    logger.info("嵌入域名白名单已移除: %s", domain)
    return list(_allowed_domains)
