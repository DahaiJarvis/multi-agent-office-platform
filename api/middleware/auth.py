"""认证与授权中间件

集成 JWT 认证和 RBAC 权限校验。
生产环境对接 OAuth2.0 / SSO，当前支持 JWT Bearer Token 和开发模式。
"""

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agent.core.infrastructure.config import get_settings
from security.auth import verify_token, extract_token_from_header
from security.audit import record_auth_audit

logger = logging.getLogger(__name__)

# 跳过认证的路径
SKIP_AUTH_PATHS = {
    "/admin/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/auth/login",
    "/auth/refresh",
    "/auth/sso/authorize",
    "/auth/sso/callback",
    "/auth/sso/providers",
    "/.well-known/openid-configuration",
    "/.well-known/jwks.json",
    "/metrics",
}

SKIP_AUTH_PREFIXES = {
    "/debug/",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """认证与授权中间件

    流程：
    1. 跳过免认证路径
    2. 从 Authorization 头提取 Token
    3. 验证 JWT Token（含黑名单检查）
    4. 将用户信息注入 request.state
    5. 开发模式下支持 X-User-ID 头降级
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 注入请求ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        try:
            response = await self._authenticate(request, call_next)
        finally:
            # 请求结束后清除租户上下文，防止上下文泄漏
            try:
                from security.tenant import clear_tenant_context
                clear_tenant_context()
            except Exception as e:
                logger.debug("操作失败，已忽略: %s", e)

        return response

    async def _authenticate(self, request: Request, call_next) -> Response:

        # 跳过免认证路径（支持任意 API 版本前缀）
        path = request.url.path
        if self._should_skip_auth(path):
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

                # 从 JWT Token 中提取 tenant_id 并注入租户上下文
                tenant_id = getattr(payload, "tenant_id", None)
                if tenant_id:
                    try:
                        from security.tenant import set_tenant_context, get_tenant_manager
                        manager = get_tenant_manager()
                        tenant = manager.get_tenant(tenant_id)
                        if tenant and tenant.status.value == "active":
                            set_tenant_context(tenant)
                    except Exception as e:
                        logger.debug("租户上下文注入失败（非致命）: %s", e)

                return await call_next(request)

            # Token 验证失败
            record_auth_audit(
                trace_id=request.state.request_id,
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
        # 需验证用户存在于用户存储中，防止伪造身份
        if settings.environment == "development":
            user_id = request.headers.get("X-User-ID", "dev-user")
            user_roles_str = request.headers.get("X-User-Roles", "employee")

            # 验证用户是否存在于用户存储中
            try:
                from security.user_store import get_user_store
                store = get_user_store()
                user_exists = await store.user_exists(user_id)
                if not user_exists:
                    record_auth_audit(
                        trace_id=request.state.request_id,
                        user_id=user_id,
                        channel=request.headers.get("X-Channel", "unknown"),
                        status="failed",
                        detail=f"开发模式降级: 用户 {user_id} 不存在于用户存储中",
                    )
                    return JSONResponse(
                        status_code=401,
                        content={"error": "unauthorized", "message": f"用户 {user_id} 不存在"},
                    )
            except Exception:
                # 用户存储不可用时，允许通过（避免阻塞开发调试）
                pass

            request.state.user_id = user_id
            request.state.user_roles = [r.strip() for r in user_roles_str.split(",")]
            request.state.user_departments = []
            request.state.auth_method = "dev"
            return await call_next(request)

        # 无 Token 且非开发模式
        record_auth_audit(
            trace_id=request.state.request_id,
            user_id="",
            channel=request.headers.get("X-Channel", "unknown"),
            status="failed",
            detail="缺少认证 Token",
        )
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "请提供有效的认证 Token"},
        )

    @staticmethod
    def _should_skip_auth(path: str) -> bool:
        """判断路径是否跳过认证

        支持任意 API 版本前缀，如 /api/v1/auth/login 和 /api/v2/auth/login 均跳过。

        Args:
            path: 请求路径

        Returns:
            是否跳过认证
        """
        # 精确匹配
        if path in SKIP_AUTH_PATHS:
            return True

        # 去除 API 版本前缀后匹配
        import re
        stripped = re.sub(r"^/api/v\d+", "", path)
        if stripped in SKIP_AUTH_PATHS:
            return True

        # 前缀匹配（如 /debug/trace/xxx）
        for prefix in SKIP_AUTH_PREFIXES:
            if path.startswith(prefix) or stripped.startswith(prefix):
                return True

        return False
