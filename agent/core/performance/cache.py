"""多级缓存策略

提供 L1 进程内缓存和 L2 Redis 分布式缓存的两级缓存架构。

缓存层级:
  - L1: 进程内 LRU 缓存，毫秒级访问，适合高频读取的配置数据
  - L2: Redis 分布式缓存，适合会话状态、热点查询结果等

缓存一致性: Write-Through + TTL 过期 + 主动失效
"""

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


# ==================== L1 进程内缓存 ====================

class LRUCache:
    """进程内 LRU 缓存

    线程安全的最近最少使用缓存，支持 TTL 过期。
    """

    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                return None
            value, expire_at = self._cache[key]
            if time.monotonic() > expire_at:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """设置缓存值"""
        expire_at = time.monotonic() + (ttl or self._default_ttl)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expire_at)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        """删除缓存值"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """获取缓存大小"""
        with self._lock:
            return len(self._cache)


# ==================== L2 Redis 缓存 ====================

class RedisCache:
    """Redis 分布式缓存

    通过 Redis 实现跨进程的缓存共享。
    """

    def __init__(self, redis_url: str = "", prefix: str = "cache:") -> None:
        self._prefix = prefix
        self._redis_url = redis_url
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                from agent.core.redis_manager import get_redis_client
                self._client = await get_redis_client()
            except Exception:
                logger.warning("Redis 连接获取失败，L2 缓存不可用")
                return None
        return self._client

    async def get(self, key: str) -> Any | None:
        """获取缓存值"""
        client = await self._get_client()
        if client is None:
            return None
        try:
            full_key = f"{self._prefix}{key}"
            data = await client.get(full_key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.error("Redis 缓存读取失败: key=%s error=%s", key, e)
            return None

    async def set(self, key: str, value: Any, ttl: float = 300.0) -> None:
        """设置缓存值"""
        client = await self._get_client()
        if client is None:
            return
        try:
            full_key = f"{self._prefix}{key}"
            await client.set(full_key, json.dumps(value, ensure_ascii=False, default=str), ex=int(ttl))
        except Exception as e:
            logger.error("Redis 缓存写入失败: key=%s error=%s", key, e)

    async def delete(self, key: str) -> bool:
        """删除缓存值"""
        client = await self._get_client()
        if client is None:
            return False
        try:
            full_key = f"{self._prefix}{key}"
            result = await client.delete(full_key)
            return result > 0
        except Exception as e:
            logger.error("Redis 缓存删除失败: key=%s error=%s", key, e)
            return False

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._client:
            await self._client.close()
            self._client = None


# ==================== 多级缓存管理器 ====================

class MultiLevelCache:
    """多级缓存管理器

    实现 L1 + L2 两级缓存，读取时先查 L1 再查 L2，
    写入时同时写入两级缓存。
    """

    def __init__(
        self,
        l1_max_size: int = 1000,
        l1_ttl: float = 300.0,
        l2_ttl: float = 600.0,
        redis_url: str = "",
    ) -> None:
        self._l1 = LRUCache(max_size=l1_max_size, default_ttl=l1_ttl)
        self._l2 = RedisCache(redis_url=redis_url)
        self._l2_ttl = l2_ttl

    def get_l1(self, key: str) -> Any | None:
        """从 L1 缓存获取"""
        return self._l1.get(key)

    async def get(self, key: str) -> Any | None:
        """从多级缓存获取

        先查 L1，命中则直接返回；未命中则查 L2，
        L2 命中则回填 L1 并返回。
        """
        value = self._l1.get(key)
        if value is not None:
            return value

        value = await self._l2.get(key)
        if value is not None:
            self._l1.set(key, value)
            return value

        return None

    def set_l1(self, key: str, value: Any, ttl: float | None = None) -> None:
        """仅设置 L1 缓存"""
        self._l1.set(key, value, ttl)

    async def set(self, key: str, value: Any, l1_ttl: float | None = None) -> None:
        """同时设置 L1 和 L2 缓存"""
        self._l1.set(key, value, l1_ttl)
        await self._l2.set(key, value, self._l2_ttl)

    def delete_l1(self, key: str) -> bool:
        """仅删除 L1 缓存"""
        return self._l1.delete(key)

    async def delete(self, key: str) -> None:
        """同时删除 L1 和 L2 缓存"""
        self._l1.delete(key)
        await self._l2.delete(key)

    def clear_l1(self) -> None:
        """清空 L1 缓存"""
        self._l1.clear()

    async def close(self) -> None:
        """关闭缓存连接"""
        self._l1.clear()
        await self._l2.close()


def generate_cache_key(prefix: str, *args: Any) -> str:
    """生成缓存 key

    Args:
        prefix: key 前缀
        args: 用于生成 key 的参数

    Returns:
        缓存 key 字符串
    """
    content = json.dumps(args, ensure_ascii=False, default=str, sort_keys=True)
    hash_val = hashlib.md5(content.encode()).hexdigest()
    return f"{prefix}:{hash_val}"


# 全局缓存实例
_cache: MultiLevelCache | None = None


def get_cache() -> MultiLevelCache:
    """获取全局缓存实例"""
    global _cache
    if _cache is None:
        _cache = MultiLevelCache()
    return _cache
