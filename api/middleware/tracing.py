"""追踪中间件"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class TracingMiddleware(BaseHTTPMiddleware):
    """请求追踪中间件，记录请求耗时和基本信息

    为每个请求生成唯一 request_id 并注入到 request.state，
    使后续中间件和异常处理器可以引用。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 优先使用上游传入的 request_id，否则生成新的
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start_time = time.time()

        response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            "request_id=%s method=%s path=%s status=%d duration=%.1fms",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        try:
            from observability.metrics import record_request
            record_request(request.method, request.url.path, response.status_code, duration_ms / 1000)
        except Exception as e:
            logger.debug("操作失败，已忽略: %s", e)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Duration-Ms"] = f"{duration_ms:.1f}"
        return response
