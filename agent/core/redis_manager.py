"""统一 Redis 连接管理器

提供全局单例 Redis 客户端，避免各模块重复创建连接。
所有需要 Redis 的模块应通过 get_redis_client() 获取共享实例。

使用方式:
    from agent.core.redis_manager import get_redis_client

    redis = await get_redis_client()
    await redis.set("key", "value")
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_redis_client: Any = None


async def get_redis_client() -> Any:
    """获取全局 Redis 客户端单例

    首次调用时根据配置创建连接，后续调用返回同一实例。

    Returns:
        redis.asyncio.Redis 客户端实例，连接失败时返回 None
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    try:
        import redis.asyncio as aioredis
        from agent.core.config import get_settings

        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
        logger.info("Redis 连接已建立: %s", settings.redis_url.split("@")[-1] if "@" in settings.redis_url else "localhost")
        return _redis_client
    except Exception as e:
        logger.error("Redis 连接创建失败: %s", e)
        return None


async def close_redis_client() -> None:
    """关闭全局 Redis 客户端连接"""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis 连接已关闭")
