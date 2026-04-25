"""分布式限流中间件

基于 Redis 的滑动窗口限流，替代进程内字典实现，
确保多 Worker / 多实例部署时限流策略一致生效。
降级策略：Redis 不可用时回退到进程内限流。
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# 免限流路径
SKIP_RATE_LIMIT_PATHS = {"/admin/health", "/docs", "/openapi.json", "/redoc", "/metrics"}


class _InMemoryRateLimiter:
    """进程内限流器（Redis 不可用时的降级方案）"""

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self._window_seconds
        if key in self._requests:
            self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        else:
            self._requests[key] = []

        if len(self._requests[key]) >= self._max_requests:
            return False
        self._requests[key].append(now)
        return True


class DistributedRateLimitMiddleware(BaseHTTPMiddleware):
    """基于 Redis 的分布式限流中间件

    限流维度：
    1. 全局限流：按 IP 地址限流
    2. 用户级限流：按用户 ID 限流（已认证用户）

    使用 Redis Sorted Set 实现滑动窗口算法：
    - key: rate_limit:{type}:{identifier}
    - score: 请求时间戳
    - member: 请求唯一标识

    降级策略：Redis 不可用时回退到进程内限流
    """

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._redis_client = None
        self._max_requests = _settings.rate_limit_per_minute
        self._window_seconds = 60
        self._fallback_limiter = _InMemoryRateLimiter(self._max_requests, self._window_seconds)
        self._use_redis = True

    async def _get_redis(self):
        """获取 Redis 客户端"""
        if self._redis_client is None:
            try:
                import redis.asyncio as aioredis
                self._redis_client = aioredis.from_url(
                    _settings.redis_url,
                    decode_responses=True,
                )
                # 测试连接
                await self._redis_client.ping()
                self._use_redis = True
            except Exception as e:
                logger.warning("Redis 连接失败，降级到进程内限流: %s", e)
                self._use_redis = False
                self._redis_client = None
        return self._redis_client

    async def _check_rate_limit_redis(self, key: str) -> bool:
        """使用 Redis Sorted Set 实现滑动窗口限流

        Args:
            key: 限流键，如 rate_limit:ip:127.0.0.1

        Returns:
            是否允许请求
        """
        redis = await self._get_redis()
        if redis is None:
            return self._fallback_limiter.is_allowed(key)

        now = time.time()
        window_start = now - self._window_seconds

        try:
            pipe = redis.pipeline()
            # 移除窗口外的记录
            pipe.zremrangebyscore(key, 0, window_start)
            # 获取当前窗口内的请求数
            pipe.zcard(key)
            # 添加当前请求
            pipe.zadd(key, {str(now): now})
            # 设置 key 过期时间
            pipe.expire(key, self._window_seconds + 1)
            results = await pipe.execute()

            current_count = results[1]
            if current_count >= self._max_requests:
                return False
            return True
        except Exception as e:
            logger.warning("Redis 限流异常，降级到进程内: %s", e)
            self._use_redis = False
            self._redis_client = None
            return self._fallback_limiter.is_allowed(key)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 去除 API 版本前缀后匹配
        import re
        stripped_path = re.sub(r"^/api/v\d+", "", path)
        if stripped_path in SKIP_RATE_LIMIT_PATHS or path in SKIP_RATE_LIMIT_PATHS:
            return await call_next(request)

        # 确定限流键
        client_ip = request.client.host if request.client else "unknown"
        user_id = getattr(request.state, "user_id", None)

        # 用户级限流（已认证用户）
        if user_id:
            rate_key = f"rate_limit:user:{user_id}"
        else:
            rate_key = f"rate_limit:ip:{client_ip}"

        # 检查限流
        if self._use_redis:
            allowed = await self._check_rate_limit_redis(rate_key)
        else:
            allowed = self._fallback_limiter.is_allowed(rate_key)

        if not allowed:
            logger.warning("限流触发: key=%s", rate_key)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit",
                    "message": "请求过于频繁，请稍后再试",
                    "retry_after": self._window_seconds,
                },
                headers={"Retry-After": str(self._window_seconds)},
            )

        return await call_next(request)
