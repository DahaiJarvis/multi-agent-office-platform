"""限流中间件"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于令牌桶算法的限流中间件"""

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._requests: dict[str, list[float]] = {}
        self._max_requests = _settings.rate_limit_per_minute
        self._window = 60  # 60秒窗口

    async def dispatch(self, request: Request, call_next):
        # 健康检查路径不限流
        if request.url.path in {"/admin/health", "/docs", "/openapi.json"}:
            return await call_next(request)

        client_id = request.client.host if request.client else "unknown"
        now = time.time()

        # 清理过期记录
        if client_id in self._requests:
            self._requests[client_id] = [
                t for t in self._requests[client_id] if now - t < self._window
            ]
        else:
            self._requests[client_id] = []

        # 检查限流
        if len(self._requests[client_id]) >= self._max_requests:
            logger.warning("限流触发: client=%s", client_id)
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limit", "message": "请求过于频繁，请稍后再试"},
            )

        self._requests[client_id].append(now)
        return await call_next(request)
