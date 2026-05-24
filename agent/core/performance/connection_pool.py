"""连接池管理

管理 HTTP 客户端和数据库连接池，复用连接减少开销。

连接池类型:
  - HTTP 连接池: 复用 httpx.AsyncClient，避免频繁建连
  - Redis 连接池: 复用 Redis 连接，支持高并发访问
  - 数据库连接池: 复用 PostgreSQL 连接（由 SQLAlchemy 管理）
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ==================== HTTP 连接池 ====================

class HTTPConnectionPool:
    """HTTP 连接池管理器

    统一管理 httpx.AsyncClient 实例，按服务分组复用。
    """

    def __init__(self, max_connections: int = 100, timeout: float = 30.0) -> None:
        self._max_connections = max_connections
        self._timeout = timeout
        self._clients: dict[str, Any] = {}

    async def get_client(self, service_name: str, base_url: str = "", headers: dict[str, str] | None = None) -> Any:
        """获取或创建 HTTP 客户端

        Args:
            service_name: 服务名称，用于分组
            base_url: 基础 URL
            headers: 默认请求头

        Returns:
            httpx.AsyncClient 实例
        """
        if service_name not in self._clients:
            import httpx

            self._clients[service_name] = httpx.AsyncClient(
                base_url=base_url,
                headers=headers or {},
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=self._max_connections,
                    max_keepalive_connections=min(self._max_connections // 2, 50),
                ),
            )
            logger.info("创建 HTTP 连接池: service=%s", service_name)

        return self._clients[service_name]

    async def close_all(self) -> None:
        """关闭所有 HTTP 客户端连接"""
        for name, client in self._clients.items():
            try:
                await client.aclose()
                logger.info("关闭 HTTP 连接池: service=%s", name)
            except Exception as e:
                logger.error("关闭 HTTP 连接池失败: service=%s error=%s", name, e)
        self._clients.clear()


# ==================== Redis 连接池 ====================

class RedisConnectionPool:
    """Redis 连接池管理器

    提供统一的 Redis 连接管理，支持连接复用。
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", max_connections: int = 50) -> None:
        self._redis_url = redis_url
        self._max_connections = max_connections
        self._pool: Any = None
        self._client: Any = None

    async def get_client(self) -> Any:
        """获取 Redis 客户端"""
        if self._client is None:
            try:
                import redis.asyncio as aioredis

                self._pool = aioredis.ConnectionPool.from_url(
                    self._redis_url,
                    max_connections=self._max_connections,
                    decode_responses=True,
                )
                self._client = aioredis.Redis(connection_pool=self._pool)
                logger.info("创建 Redis 连接池: url=%s", self._redis_url)
            except ImportError:
                logger.warning("redis 异步库未安装，Redis 连接池不可用")
                return None
        return self._client

    async def close(self) -> None:
        """关闭 Redis 连接池"""
        if self._client:
            await self._client.close()
        if self._pool:
            await self._pool.disconnect()
        self._client = None
        self._pool = None
        logger.info("关闭 Redis 连接池")


# ==================== 连接池管理器 ====================

class ConnectionPoolManager:
    """连接池统一管理器

    集中管理所有类型的连接池，提供统一的获取和关闭接口。
    """

    def __init__(self, redis_url: str = "") -> None:
        self._http_pool = HTTPConnectionPool()
        self._redis_pool = RedisConnectionPool(redis_url=redis_url)
        self._initialized = False

    async def initialize(self) -> None:
        """初始化连接池（预创建 Redis 连接）"""
        if self._initialized:
            return

        redis_client = await self._redis_pool.get_client()
        if redis_client:
            try:
                await redis_client.ping()
                logger.info("Redis 连接池预创建成功")
            except Exception as e:
                logger.warning("Redis 连接池预创建失败: %s", e)

        self._initialized = True

    async def get_http_client(self, service_name: str, base_url: str = "", headers: dict[str, str] | None = None) -> Any:
        """获取 HTTP 客户端"""
        return await self._http_pool.get_client(service_name, base_url, headers)

    async def get_redis_client(self) -> Any:
        """获取 Redis 客户端"""
        return await self._redis_pool.get_client()

    async def close_all(self) -> None:
        """关闭所有连接池"""
        await self._http_pool.close_all()
        await self._redis_pool.close()
        self._initialized = False
        logger.info("所有连接池已关闭")

    async def shutdown(self) -> None:
        """关闭连接池管理器（等同于 close_all）"""
        await self.close_all()


# 全局连接池管理器
_pool_manager: ConnectionPoolManager | None = None


def get_pool_manager() -> ConnectionPoolManager:
    """获取全局连接池管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _pool_manager
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_pool_manager() is not None:
            return ctx.get_pool_manager()
    except Exception:
        pass
    if _pool_manager is None:
        from agent.core.infrastructure.config import get_settings
        settings = get_settings()
        _pool_manager = ConnectionPoolManager(redis_url=settings.redis_url)
    return _pool_manager
