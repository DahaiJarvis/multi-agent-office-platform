"""语义缓存（Semantic Cache）

基于语义相似度而非精确匹配的缓存层。
当用户查询与已缓存查询语义相近时，直接返回缓存结果，
避免重复调用 LLM，显著降低延迟和成本。

核心原理：
  1. 将查询文本通过 Embedding 模型转为向量
  2. 在向量空间中搜索与当前查询最相似的已缓存查询
  3. 相似度超过阈值时返回缓存结果

双层缓存架构：
  - L1（进程内）：本地热点缓存，容量限制100条，TTL 60秒，仅精确匹配
  - L2（Redis）：分布式缓存，支持精确匹配和语义搜索，多实例共享

读写流程：
  - 读：L1 精确匹配 -> L2 Redis 精确匹配 -> L2 语义搜索
  - 写：同时写 L1 和 L2，L2 设置 TTL 自动过期

使用方式：
    from agent.core.performance.semantic_cache import get_semantic_cache

    cache = get_semantic_cache()
    result = await cache.get("北京明天天气怎么样")
    if result is not None:
        return result
    # 调用 LLM 获取结果后写入缓存
    await cache.set("北京明天天气怎么样", llm_result)
"""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# L1 缓存常量
L1_MAX_ENTRIES = 100
L1_TTL_SECONDS = 60

# L2 Redis Key 前缀
L2_KEY_PREFIX = "sem_cache:"
L2_EMBEDDINGS_KEY = "sem_cache:embeddings"


class SemanticCacheEntry(BaseModel):
    """语义缓存条目"""

    query: str = Field(..., description="原始查询文本")
    query_hash: str = Field(default="", description="查询文本哈希")
    response: Any = Field(..., description="缓存响应")
    embedding: list[float] = Field(default_factory=list, description="查询向量")
    created_at: float = Field(default_factory=time.time)
    access_count: int = Field(default=0, description="访问次数")
    ttl: float = Field(default=600.0, description="生存时间(秒)")
    agent_name: str = Field(default="", description="来源 Agent")


class SemanticCacheConfig(BaseModel):
    """语义缓存配置"""

    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0, description="相似度阈值")
    max_entries: int = Field(default=500, description="L2 最大缓存条目数")
    default_ttl: float = Field(default=600.0, description="默认生存时间(秒)")
    enable_embedding: bool = Field(default=True, description="是否启用 Embedding")
    embedding_model: str = Field(default="text-embedding-v3", description="Embedding 模型")


class _L1Cache:
    """L1 进程内热点缓存

    仅存储精确匹配结果，容量小、TTL 短，用于减少 Redis 查询。
    使用 OrderedDict 实现 LRU 淘汰。
    """

    def __init__(self, max_entries: int = L1_MAX_ENTRIES, ttl_seconds: float = L1_TTL_SECONDS) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, query_hash: str) -> Any | None:
        """获取 L1 缓存，命中时移到末尾（LRU）"""
        item = self._store.get(query_hash)
        if item is None:
            return None
        value, created_at = item
        if (time.time() - created_at) > self._ttl_seconds:
            del self._store[query_hash]
            return None
        self._store.move_to_end(query_hash)
        return value

    def set(self, query_hash: str, value: Any) -> None:
        """写入 L1 缓存，超限时淘汰最旧的条目"""
        if query_hash in self._store:
            self._store.move_to_end(query_hash)
        self._store[query_hash] = (value, time.time())
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def delete(self, query_hash: str) -> None:
        """删除 L1 缓存条目"""
        self._store.pop(query_hash, None)

    def clear(self) -> None:
        """清空 L1 缓存"""
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


class SemanticCache:
    """语义缓存

    双层缓存架构：L1 进程内 + L2 Redis。
    当 Redis 不可用时，自动降级为纯内存模式。
    当 Embedding 服务不可用时，自动降级为精确匹配模式。
    """

    def __init__(self, config: SemanticCacheConfig | None = None) -> None:
        self._config = config or SemanticCacheConfig()
        self._l1 = _L1Cache()
        self._entries: dict[str, SemanticCacheEntry] = {}
        self._embeddings: list[tuple[str, list[float]]] = []
        self._embedding_client: Any = None
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端，不可用时返回 None"""
        if self._redis is not None:
            return self._redis
        try:
            from agent.core.redis_manager import get_redis_client
            self._redis = await get_redis_client()
            return self._redis
        except Exception as e:
            logger.debug("Redis 获取失败: %s", e)
            return None

    async def get(self, query: str, agent_name: str = "") -> Any | None:
        """从语义缓存获取结果

        查询流程：
          1. L1 精确匹配（进程内，最快）
          2. L2 Redis 精确匹配
          3. L2 Redis 语义搜索（需要 Embedding）
          4. 内存语义搜索（Redis 不可用时的降级）

        Args:
            query: 用户查询文本
            agent_name: Agent 名称（可选，用于过滤）

        Returns:
            缓存的响应，未命中返回 None
        """
        query_hash = self._hash_query(query)

        # 步骤1: L1 精确匹配
        l1_result = self._l1.get(query_hash)
        if l1_result is not None:
            logger.debug("L1 缓存精确命中: query=%s", query[:30])
            return l1_result

        # 步骤2: L2 Redis 精确匹配
        redis = await self._get_redis()
        if redis is not None:
            l2_result = await self._l2_get(redis, query_hash)
            if l2_result is not None:
                # 回填 L1
                self._l1.set(query_hash, l2_result)
                logger.debug("L2 Redis 精确命中: query=%s", query[:30])
                return l2_result

            # 步骤3: L2 Redis 语义搜索
            if self._config.enable_embedding:
                try:
                    query_embedding = await self._get_embedding(query)
                    if query_embedding:
                        best_hash, best_sim = await self._l2_semantic_search(
                            redis, query_embedding, agent_name,
                        )
                        if best_sim >= self._config.similarity_threshold and best_hash:
                            sem_result = await self._l2_get(redis, best_hash)
                            if sem_result is not None:
                                self._l1.set(query_hash, sem_result)
                                logger.debug(
                                    "L2 Redis 语义命中: query=%s sim=%.3f",
                                    query[:30], best_sim,
                                )
                                return sem_result
                except Exception as e:
                    logger.debug("L2 语义搜索失败: %s", e)

        # 步骤4: 内存语义搜索（Redis 不可用时的降级）
        if self._config.enable_embedding and self._embeddings:
            try:
                query_embedding = await self._get_embedding(query)
                if query_embedding:
                    best_key, best_sim = self._find_most_similar(
                        query_embedding, agent_name,
                    )
                    if best_sim >= self._config.similarity_threshold and best_key:
                        entry = self._entries.get(best_key)
                        if entry and not self._is_expired(entry):
                            entry.access_count += 1
                            logger.debug(
                                "内存语义命中: query=%s sim=%.3f",
                                query[:30], best_sim,
                            )
                            return entry.response
            except Exception as e:
                logger.debug("内存语义匹配失败: %s", e)

        return None

    async def set(
        self,
        query: str,
        response: Any,
        agent_name: str = "",
        ttl: float | None = None,
    ) -> None:
        """写入语义缓存

        同时写入 L1 和 L2，L2 设置 TTL 自动过期。

        Args:
            query: 用户查询文本
            response: LLM 响应
            agent_name: Agent 名称
            ttl: 生存时间(秒)
        """
        query_hash = self._hash_query(query)
        effective_ttl = ttl or self._config.default_ttl

        # 获取 Embedding
        embedding: list[float] = []
        if self._config.enable_embedding:
            try:
                embedding = await self._get_embedding(query) or []
            except Exception:
                pass

        # 写入 L1
        self._l1.set(query_hash, response)

        # 写入 L2 Redis
        redis = await self._get_redis()
        if redis is not None:
            await self._l2_set(redis, query_hash, query, response, agent_name, effective_ttl, embedding)

        # 写入内存（降级用）
        if query_hash in self._entries:
            self._embeddings = [
                (k, v) for k, v in self._embeddings if k != query_hash
            ]

        entry = SemanticCacheEntry(
            query=query,
            query_hash=query_hash,
            response=response,
            embedding=embedding,
            agent_name=agent_name,
            ttl=effective_ttl,
        )

        self._evict_if_needed()

        self._entries[query_hash] = entry
        if embedding:
            self._embeddings.append((query_hash, embedding))

        logger.debug("语义缓存写入: %s", query[:30])

    async def delete(self, query: str) -> bool:
        """删除缓存条目（同时删除 L1 和 L2）"""
        query_hash = self._hash_query(query)

        # 删除 L1
        self._l1.delete(query_hash)

        # 删除 L2
        redis = await self._get_redis()
        if redis is not None:
            await self._l2_delete(redis, query_hash)

        # 删除内存
        if query_hash in self._entries:
            del self._entries[query_hash]
            self._embeddings = [
                (k, v) for k, v in self._embeddings if k != query_hash
            ]
            return True
        return False

    def clear(self) -> None:
        """清空所有缓存"""
        self._l1.clear()
        self._entries.clear()
        self._embeddings.clear()

    async def clear_l2(self) -> None:
        """清空 L2 Redis 缓存"""
        redis = await self._get_redis()
        if redis is None:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match=f"{L2_KEY_PREFIX}*", count=100,
                )
                if keys:
                    await redis.delete(*keys)
                if cursor == 0:
                    break
            await redis.delete(L2_EMBEDDINGS_KEY)
            logger.info("L2 Redis 缓存已清空")
        except Exception as e:
            logger.warning("清空 L2 缓存失败: %s", e)

    def stats(self) -> dict[str, Any]:
        """获取缓存统计"""
        total = len(self._entries)
        expired = sum(1 for e in self._entries.values() if self._is_expired(e))
        return {
            "total_entries": total,
            "active_entries": total - expired,
            "expired_entries": expired,
            "embedding_count": len(self._embeddings),
            "max_entries": self._config.max_entries,
            "similarity_threshold": self._config.similarity_threshold,
            "l1_size": self._l1.size,
            "l1_max_entries": L1_MAX_ENTRIES,
            "l1_ttl_seconds": L1_TTL_SECONDS,
        }

    # ==================== L2 Redis 操作 ====================

    async def _l2_get(self, redis: Any, query_hash: str) -> Any | None:
        """从 L2 Redis 获取缓存"""
        try:
            key = f"{L2_KEY_PREFIX}{query_hash}"
            raw = await redis.get(key)
            if raw is None:
                return None
            data = json.loads(raw)
            # 检查 TTL（Redis TTL 自动过期，此处为双重校验）
            if (time.time() - data.get("created_at", 0)) > data.get("ttl", 600):
                await redis.delete(key)
                return None
            return data.get("response")
        except Exception as e:
            logger.debug("L2 Redis 读取失败: %s", e)
            return None

    async def _l2_set(
        self,
        redis: Any,
        query_hash: str,
        query: str,
        response: Any,
        agent_name: str,
        ttl: float,
        embedding: list[float],
    ) -> None:
        """写入 L2 Redis"""
        try:
            key = f"{L2_KEY_PREFIX}{query_hash}"
            data = {
                "query": query,
                "query_hash": query_hash,
                "response": response,
                "agent_name": agent_name,
                "created_at": time.time(),
                "ttl": ttl,
            }
            ttl_ms = int(ttl * 1000)
            await redis.set(key, json.dumps(data, ensure_ascii=False), px=ttl_ms)

            # 存储 Embedding 索引（用于语义搜索）
            if embedding:
                embedding_data = json.dumps({
                    "query_hash": query_hash,
                    "agent_name": agent_name,
                    "created_at": time.time(),
                    "embedding": embedding,
                }, ensure_ascii=False)
                await redis.zadd(
                    L2_EMBEDDINGS_KEY,
                    {embedding_data: time.time()},
                )
                # 清理过期的 embedding 索引
                cutoff = time.time() - self._config.default_ttl
                await redis.zremrangebyscore(L2_EMBEDDINGS_KEY, "-inf", cutoff)

        except Exception as e:
            logger.debug("L2 Redis 写入失败: %s", e)

    async def _l2_delete(self, redis: Any, query_hash: str) -> None:
        """从 L2 Redis 删除缓存"""
        try:
            key = f"{L2_KEY_PREFIX}{query_hash}"
            await redis.delete(key)
        except Exception as e:
            logger.debug("L2 Redis 删除失败: %s", e)

    async def _l2_semantic_search(
        self,
        redis: Any,
        query_embedding: list[float],
        agent_name: str = "",
    ) -> tuple[str, float]:
        """在 L2 Redis 中执行语义搜索

        从 Redis Sorted Set 中获取所有 Embedding 索引，
        在应用层计算余弦相似度，返回最相似的条目。

        Returns:
            (最相似条目的 query_hash, 相似度分数)
        """
        best_hash = ""
        best_sim = 0.0

        try:
            # 获取最近 default_ttl 时间内的 embedding 索引
            cutoff = time.time() - self._config.default_ttl
            raw_entries = await redis.zrangebyscore(
                L2_EMBEDDINGS_KEY, cutoff, "+inf",
            )

            for raw_entry in raw_entries:
                try:
                    entry = json.loads(raw_entry)
                    # Agent 过滤
                    if agent_name and entry.get("agent_name") != agent_name:
                        continue

                    embedding = entry.get("embedding", [])
                    if not embedding:
                        continue

                    sim = self._cosine_similarity(query_embedding, embedding)
                    if sim > best_sim:
                        best_sim = sim
                        best_hash = entry.get("query_hash", "")
                except (json.JSONDecodeError, KeyError):
                    continue

        except Exception as e:
            logger.debug("L2 语义搜索失败: %s", e)

        return best_hash, best_sim

    # ==================== 内存操作（降级用） ====================

    def _hash_query(self, query: str) -> str:
        """对查询文本生成哈希"""
        normalized = query.strip().lower()
        return hashlib.md5(normalized.encode()).hexdigest()

    def _is_expired(self, entry: SemanticCacheEntry) -> bool:
        """检查缓存条目是否过期"""
        return (time.time() - entry.created_at) > entry.ttl

    def _evict_if_needed(self) -> None:
        """清理过期和超限条目"""
        expired_keys = [
            k for k, v in self._entries.items() if self._is_expired(v)
        ]
        for key in expired_keys:
            del self._entries[key]
            self._embeddings = [
                (k, v) for k, v in self._embeddings if k != key
            ]

        if len(self._entries) > self._config.max_entries:
            sorted_entries = sorted(
                self._entries.items(),
                key=lambda x: (x[1].access_count, x[1].created_at),
            )
            to_remove = len(self._entries) - self._config.max_entries
            for key, _ in sorted_entries[:to_remove]:
                del self._entries[key]
                self._embeddings = [
                    (k, v) for k, v in self._embeddings if k != key
                ]

    def _find_most_similar(
        self,
        query_embedding: list[float],
        agent_name: str = "",
    ) -> tuple[str, float]:
        """在内存缓存中查找最相似的条目"""
        best_key = ""
        best_sim = 0.0

        for key, embedding in self._embeddings:
            if agent_name:
                entry = self._entries.get(key)
                if entry and entry.agent_name != agent_name:
                    continue

            sim = self._cosine_similarity(query_embedding, embedding)
            if sim > best_sim:
                best_sim = sim
                best_key = key

        return best_key, best_sim

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算余弦相似度"""
        if len(a) != len(b) or not a:
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    async def _get_embedding(self, text: str) -> list[float]:
        """获取文本的 Embedding 向量

        使用阿里云 DashScope Embedding API。
        降级方案：返回空列表，语义匹配不生效。
        """
        if self._embedding_client is None:
            try:
                import dashscope
                from agent.core.config import get_settings
                settings = get_settings()
                dashscope.api_key = settings.dashscope_api_key
                self._embedding_client = dashscope
            except Exception as e:
                logger.debug("Embedding 客户端初始化失败: %s", e)
                return []

        try:
            resp = self._embedding_client.TextEmbedding.call(
                model=self._config.embedding_model,
                input=text,
            )
            if resp.status_code == 200:
                return resp.output["embeddings"][0]["embedding"]
        except Exception as e:
            logger.debug("Embedding 调用失败: %s", e)

        return []


# 全局语义缓存实例
_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    """获取全局语义缓存实例"""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
