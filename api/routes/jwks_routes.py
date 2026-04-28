"""JWKS 与 OIDC 发现端点

提供标准 OIDC 发现端点，使子系统能够自动获取
主平台的公钥信息，实现 Token 的跨系统验证。

端点说明：
- GET /.well-known/openid-configuration: OIDC 发现文档
- GET /.well-known/jwks.json: JWKS 公钥集

使用示例：
  # 获取 OIDC 发现文档
  curl http://localhost:8000/.well-known/openid-configuration

  # 获取 JWKS 公钥
  curl http://localhost:8000/.well-known/jwks.json

  # 子系统验证 Token 流程：
  # 1. 请求 /.well-known/openid-configuration 获取 jwks_uri
  # 2. 请求 jwks_uri 获取公钥
  # 3. 使用公钥验证主平台签发的 JWT Token
"""

import base64
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OIDC"])


def _rsa_public_key_to_jwk(public_key_pem: str, kid: str = "key-1") -> dict[str, Any]:
    """将 RSA PEM 公钥转换为 JWKS 格式

    从 PEM 格式的 RSA 公钥中提取模数(n)和指数(e)，
    转换为 Base64url 编码的 JWKS 格式。
    支持密钥轮换，通过 kid 标识不同密钥版本。

    Args:
        public_key_pem: PEM 格式的 RSA 公钥字符串
        kid: 密钥 ID，用于密钥轮换场景，默认 "key-1"

    Returns:
        JWKS 格式的密钥字典，包含 kty/kid/n/e/alg/use 字段

    Raises:
        ValueError: 公钥格式无效或解析失败
    """
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        public_key = load_pem_public_key(public_key_pem.encode("utf-8"))

        # 提取 RSA 公钥的模数和指数
        public_numbers = public_key.public_numbers()
        n = public_numbers.n
        e = public_numbers.e

        # 转换为 Base64url 编码（无填充）
        def _int_to_base64url(value: int) -> str:
            byte_length = (value.bit_length() + 7) // 8
            value_bytes = value.to_bytes(byte_length, byteorder="big")
            return base64.urlsafe_b64encode(value_bytes).rstrip(b"=").decode("ascii")

        return {
            "kty": "RSA",
            "kid": kid,
            "n": _int_to_base64url(n),
            "e": _int_to_base64url(e),
            "alg": "RS256",
            "use": "sig",
        }
    except ImportError:
        logger.error("cryptography 库未安装，无法生成 JWKS")
        raise ValueError("cryptography 库未安装，JWKS 端点不可用")
    except Exception as e:
        logger.error("RSA 公钥解析失败: %s", str(e))
        raise ValueError(f"RSA 公钥格式无效: {str(e)}")


@router.get("/.well-known/openid-configuration")
async def openid_configuration() -> dict[str, Any]:
    """OIDC 发现端点

    返回 OIDC 标准发现文档，包含签发者标识、JWKS 端点地址、
    支持的签名算法等信息。子系统通过此端点自动发现
    主平台的 OIDC 配置，无需硬编码。

    返回示例:
        {
            "issuer": "multi-agent-platform",
            "jwks_uri": "http://localhost:8000/.well-known/jwks.json",
            "id_token_signing_alg_values_supported": ["RS256"],
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "claims_supported": ["sub", "iss", "aud", "exp", "iat"]
        }
    """
    settings = get_settings()
    issuer = getattr(settings, "jwt_issuer", "") or "multi-agent-platform"
    base_url = f"http://{settings.api_host}:{settings.api_port}"
    if settings.api_host == "0.0.0.0":
        base_url = f"http://localhost:{settings.api_port}"

    return {
        "issuer": issuer,
        "jwks_uri": f"{base_url}/.well-known/jwks.json",
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "jti", "roles", "departments"],
        "scopes_supported": ["openid", "profile"],
    }


@router.get("/.well-known/jwks.json")
async def jwks_json() -> JSONResponse:
    """JWKS 公钥端点

    返回主平台的 RSA 公钥集（JSON Web Key Set），
    子系统使用此公钥验证主平台签发的 JWT Token。

    支持密钥轮换：通过 kid 字段标识不同版本的密钥，
    签发 Token 时携带 kid，验证时根据 kid 选择对应公钥。

    返回示例:
        {
            "keys": [{
                "kty": "RSA",
                "kid": "key-1",
                "n": "...",
                "e": "AQAB",
                "alg": "RS256",
                "use": "sig"
            }]
        }

    异常处理：
    - RS256 模式且公钥有效：返回 JWKS
    - HS256 模式或公钥缺失：返回空 keys 数组（子系统需使用其他验证方式）
    - 公钥解析失败：记录错误日志，返回空 keys 数组
    """
    settings = get_settings()

    keys = []

    if settings.jwt_algorithm == "RS256" and settings.jwt_public_key:
        try:
            jwk = _rsa_public_key_to_jwk(settings.jwt_public_key, kid="key-1")
            keys.append(jwk)
            logger.debug("JWKS 公钥生成成功: kid=key-1")
        except ValueError as e:
            logger.error("JWKS 公钥生成失败: %s", str(e))
    else:
        logger.warning(
            "当前非 RS256 模式或公钥未配置，JWKS 端点返回空密钥集。"
            "子系统将无法通过 JWKS 验证 Token。"
        )

    return JSONResponse(
        content={"keys": keys},
        headers={"Cache-Control": "public, max-age=3600"},
    )
