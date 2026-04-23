"""认证中间件"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# 开发模式下跳过认证的路径
SKIP_AUTH_PATHS = {"/admin/health", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件

    生产环境需对接 OAuth2.0 / SSO，当前为开发模式基础实现。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 健康检查和文档路径跳过认证
        if request.url.path in SKIP_AUTH_PATHS:
            return await call_next(request)

        # 注入请求ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        # TODO: Phase 4 实现 JWT / OAuth2.0 认证
        # 当前开发模式：从请求头提取 user_id
        user_id = request.headers.get("X-User-ID", "dev-user")
        request.state.user_id = user_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
