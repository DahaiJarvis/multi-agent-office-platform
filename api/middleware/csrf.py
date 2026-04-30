"""CSRF 防护中间件

对状态变更请求（POST/PUT/DELETE/PATCH）校验自定义请求头，
利用同源策略阻止跨站请求伪造攻击。

策略：
  - 安全方法（GET/HEAD/OPTIONS）直接放行
  - 状态变更方法要求携带 X-CSRF-Token 或 Authorization 头
  - Content-Type 为 application/json 时视为 AJAX 请求，天然免疫简单 CSRF
  - 前端通过自定义头传递 CSRF Token，浏览器跨站请求无法自动附加
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_HEADER = "x-csrf-token"
_AUTH_HEADER = "authorization"
_CONTENT_TYPE_HEADER = "content-type"


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF 防护中间件

    对状态变更请求校验自定义头，利用浏览器同源策略阻止 CSRF。
    合法请求需满足以下条件之一：
      1. 使用安全方法（GET/HEAD/OPTIONS）
      2. 携带 X-CSRF-Token 自定义头
      3. 携带 Authorization 头（Bearer Token 认证请求）
      4. Content-Type 为 application/json（AJAX 请求）
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        if request.url.path.startswith("/docs") or request.url.path.startswith("/openapi") or request.url.path.startswith("/redoc"):
            return await call_next(request)

        if _CSRF_HEADER in request.headers:
            return await call_next(request)

        if _AUTH_HEADER in request.headers:
            return await call_next(request)

        content_type = request.headers.get(_CONTENT_TYPE_HEADER, "")
        if "application/json" in content_type:
            return await call_next(request)

        logger.warning("CSRF 防护拦截: method=%s path=%s", request.method, request.url.path)
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF token missing. 请在请求头中携带 X-CSRF-Token 或 Authorization。"},
        )
