"""多级缓存策略

提供 L1 进程内缓存和 L2 Redis 分布式缓存的两级缓存架构。

缓存层级:
  - L1: 进程内 LRU 缓存，毫秒级访问，适合高频读取的配置数据
  - L2: Redis 分布式缓存，适合会话状态、热点查询结果等

淘汰策略:
  - TTL: 基于时间的自动过期
  - LRU: 最近最少使用淘汰
  - LFU: 最少频率使用淘汰（可选）

缓存一致性: Write-Through + TTL 过期 + 主动失效

使用方式:
    from agent.core.performance.cache import get_cache, generate_cache_key

    cache = get_cache()

    # L1 缓存
    cache.set_l1("key", value, ttl=300)
    result = cache.get_l1("key")

    # 多级缓存
    await cache.set("key", value, ttl=300)
    result = await cache.get("key")

    # 带命名空间的缓存
    await cache.set("key", value, namespace="knowledge")
    result = await cache.get("key", namespace="knowledge")
"""

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


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

    def keys(self) -> list[str]:
        """获取所有缓存 key"""
        with self._lock:
            return list(self._cache.keys())


class LFUCache:
    """进程内 LFU 缓存

    基于访问频率的淘汰策略，频率最低的条目优先淘汰。
    """

    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._cache: dict[str, tuple[Any, float, int]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                return None
            value, expire_at, freq = self._cache[key]
            if time.monotonic() > expire_at:
                del self._cache[key]
                return None
            self._cache[key] = (value, expire_at, freq + 1)
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """设置缓存值"""
        expire_at = time.monotonic() + (ttl or self._default_ttl)
        with self._lock:
            if key in self._cache:
                _, _, freq = self._cache[key]
                self._cache[key] = (value, expire_at, freq)
            else:
                self._cache[key] = (value, expire_at, 0)
            self._evict()

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

    def keys(self) -> list[str]:
        """获取所有缓存 key"""
        with self._lock:
            return list(self._cache.keys())

    def _evict(self) -> None:
        expired = [k for k, (_, expire_at, _) in self._cache.items() if time.monotonic() > expire_at]
        for k in expired:
            del self._cache[k]

        while len(self._cache) > self._max_size:
            lfu_key = min(self._cache, key=lambda k: (self._cache[k][2], self._cache[k][1]))
            del self._cache[lfu_key]


class RedisCache:
    """Redis 分布式缓存

    通过 Redis 实现跨进程的缓存共享。
    """

    def __init__(self, prefix: str = "cache:") -> None:
        self._prefix = prefix
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                from agent.core.redis_manager import get_redis_client
                self._client = await get_redis_client()
            except Exception:
                logger.debug("Redis 连接获取失败，L2 缓存不可用")
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
            logger.debug("Redis 缓存读取失败: key=%s error=%s", key, e)
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
            logger.debug("Redis 缓存写入失败: key=%s error=%s", key, e)

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
            logger.debug("Redis 缓存删除失败: key=%s error=%s", key, e)
            return False

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._client:
            await self._client.close()
            self._client = None


class MultiLevelCache:
    """多级缓存管理器

    L1 内存缓存 + L2 Redis 缓存的两级缓存架构。
    读取时先查 L1，再查 L2；写入时同时写入两级。
    支持 LRU/LFU 淘汰策略和命名空间隔离。
    """

    def __init__(
        self,
        l1_max_size: int = 1000,
        l1_ttl: float = 300.0,
        l2_ttl: float = 600.0,
        eviction_policy: str = "lru",
        namespace_separator: str = ":",
    ) -> None:
        if eviction_policy == "lfu":
            self._l1 = LFUCache(max_size=l1_max_size, default_ttl=l1_ttl)
        else:
            self._l1 = LRUCache(max_size=l1_max_size, default_ttl=l1_ttl)
        self._l2 = RedisCache()
        self._l2_ttl = l2_ttl
        self._namespace_separator = namespace_separator
        self._hit_count: int = 0
        self._miss_count: int = 0
        self._l1_hits: int = 0
        self._l2_hits: int = 0

    def get_l1(self, key: str) -> Any | None:
        """从 L1 缓存获取"""
        return self._l1.get(key)

    async def get(self, key: str, namespace: str = "") -> Any | None:
        """从多级缓存获取

        先查 L1，命中则直接返回；未命中则查 L2，
        L2 命中则回填 L1 并返回。
        """
        full_key = self._make_key(key, namespace)

        value = self._l1.get(full_key)
        if value is not None:
            self._hit_count += 1
            self._l1_hits += 1
            return value

        value = await self._l2.get(full_key)
        if value is not None:
            self._hit_count += 1
            self._l2_hits += 1
            self._l1.set(full_key, value)
            return value

        self._miss_count += 1
        return None

    def set_l1(self, key: str, value: Any, ttl: float | None = None) -> None:
        """仅设置 L1 缓存"""
        self._l1.set(key, value, ttl)

    async def set(self, key: str, value: Any, ttl: float | None = None, namespace: str = "") -> None:
        """同时设置 L1 和 L2 缓存"""
        full_key = self._make_key(key, namespace)
        l1_ttl = ttl or 300.0
        l2_ttl = ttl or self._l2_ttl

        self._l1.set(full_key, value, l1_ttl)
        await self._l2.set(full_key, value, l2_ttl)

    def delete_l1(self, key: str) -> bool:
        """仅删除 L1 缓存"""
        return self._l1.delete(key)

    async def delete(self, key: str, namespace: str = "") -> None:
        """同时删除 L1 和 L2 缓存"""
        full_key = self._make_key(key, namespace)
        self._l1.delete(full_key)
        await self._l2.delete(full_key)

    def clear_l1(self) -> None:
        """清空 L1 缓存"""
        self._l1.clear()

    async def clear_namespace(self, namespace: str) -> int:
        """清空指定命名空间的所有缓存（L1 + L2）"""
        prefix = f"cache{self._namespace_separator}{namespace}"
        count = 0

        # 清理 L1
        keys_to_delete = [k for k in self._l1.keys() if k.startswith(prefix)]
        for key in keys_to_delete:
            self._l1.delete(key)
            count += 1

        # 清理 L2 Redis
        try:
            client = await self._l2._get_client()
            if client:
                redis_keys = []
                async for key in client.scan_iter(match=f"{self._l2._prefix}{prefix}*"):
                    redis_keys.append(key)
                if redis_keys:
                    count += await client.delete(*redis_keys)
        except Exception as e:
            logger.warning("清理 L2 命名空间失败: %s", e)

        return count

    def stats(self) -> dict[str, Any]:
        """获取缓存统计"""
        total = self._hit_count + self._miss_count
        hit_rate = self._hit_count / total if total > 0 else 0.0
        l1_rate = self._l1_hits / self._hit_count if self._hit_count > 0 else 0.0

        return {
            "total_requests": total,
            "hits": self._hit_count,
            "misses": self._miss_count,
            "hit_rate": round(hit_rate, 4),
            "l1_hits": self._l1_hits,
            "l2_hits": self._l2_hits,
            "l1_hit_rate": round(l1_rate, 4),
            "l1_size": self._l1.size(),
        }

    def _make_key(self, key: str, namespace: str = "") -> str:
        """生成带命名空间的缓存 key"""
        if namespace:
            return f"cache{self._namespace_separator}{namespace}{self._namespace_separator}{key}"
        return key

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


_cache: MultiLevelCache | None = None


def get_cache() -> MultiLevelCache:
    """获取全局缓存实例"""
    global _cache
    if _cache is None:
        _cache = MultiLevelCache()
    return _cache
