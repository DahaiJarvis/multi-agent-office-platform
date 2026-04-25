"""认证与授权中间件

集成 JWT 认证和 RBAC 权限校验。
生产环境对接 OAuth2.0 / SSO，当前支持 JWT Bearer Token 和开发模式。
"""

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agent.core.config import get_settings
from security.auth import verify_token, extract_token_from_header
from security.audit import record_auth_audit

logger = logging.getLogger(__name__)

# 跳过认证的路径
SKIP_AUTH_PATHS = {"/admin/health", "/docs", "/openapi.json", "/redoc", "/auth/login", "/auth/refresh"}


class AuthMiddleware(BaseHTTPMiddleware):
    """认证与授权中间件

    流程：
    1. 跳过免认证路径
    2. 从 Authorization 头提取 Token
    3. 验证 JWT Token
    4. 将用户信息注入 request.state
    5. 开发模式下支持 X-User-ID 头降级
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 注入请求ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        # 跳过免认证路径
        if request.url.path in SKIP_AUTH_PATHS:
            return await call_next(request)

        settings = get_settings()

        # 尝试 JWT 认证
        auth_header = request.headers.get("Authorization")
        token = extract_token_from_header(auth_header)

        if token:
            payload = verify_token(token, expected_type="access")
            if payload is not None:
                request.state.user_id = payload.user_id
                request.state.user_roles = payload.roles
                request.state.user_departments = payload.departments
                request.state.auth_method = "jwt"
                return await call_next(request)

            # Token 验证失败
            record_auth_audit(
                trace_id=request_id,
                user_id="",
                channel=request.headers.get("X-Channel", "unknown"),
                status="failed",
                detail="JWT Token 验证失败",
            )
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "Token 无效或已过期"},
            )

        # 开发模式降级：从 X-User-ID 头提取用户信息
        if settings.environment == "development":
            user_id = request.headers.get("X-User-ID", "dev-user")
            user_roles_str = request.headers.get("X-User-Roles", "employee")
            request.state.user_id = user_id
            request.state.user_roles = [r.strip() for r in user_roles_str.split(",")]
            request.state.user_departments = []
            request.state.auth_method = "dev"
            return await call_next(request)

        # 无 Token 且非开发模式
        record_auth_audit(
            trace_id=request_id,
            user_id="",
            channel=request.headers.get("X-Channel", "unknown"),
            status="failed",
            detail="缺少认证 Token",
        )
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "请提供有效的认证 Token"},
        )
