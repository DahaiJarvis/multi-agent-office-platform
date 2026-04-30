"""语义缓存（Semantic Cache）

基于语义相似度而非精确匹配的缓存层。
当用户查询与已缓存查询语义相近时，直接返回缓存结果，
避免重复调用 LLM，显著降低延迟和成本。

核心原理：
  1. 将查询文本通过 Embedding 模型转为向量
  2. 在向量空间中搜索与当前查询最相似的已缓存查询
  3. 相似度超过阈值时返回缓存结果

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
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
    max_entries: int = Field(default=500, description="最大缓存条目数")
    default_ttl: float = Field(default=600.0, description="默认生存时间(秒)")
    enable_embedding: bool = Field(default=True, description="是否启用 Embedding")
    embedding_model: str = Field(default="text-embedding-v3", description="Embedding 模型")


class SemanticCache:
    """语义缓存

    基于向量相似度的缓存实现。
    当 Embedding 服务不可用时，自动降级为精确匹配模式。
    """

    def __init__(self, config: SemanticCacheConfig | None = None) -> None:
        self._config = config or SemanticCacheConfig()
        self._entries: dict[str, SemanticCacheEntry] = {}
        self._embeddings: list[tuple[str, list[float]]] = []
        self._embedding_client: Any = None

    async def get(self, query: str, agent_name: str = "") -> Any | None:
        """从语义缓存获取结果

        1. 先尝试精确匹配（O(1)）
        2. 再尝试语义匹配（O(n) 向量搜索）
        3. 检查 TTL 是否过期

        Args:
            query: 用户查询文本
            agent_name: Agent 名称（可选，用于过滤）

        Returns:
            缓存的响应，未命中返回 None
        """
        # 精确匹配
        query_hash = self._hash_query(query)
        entry = self._entries.get(query_hash)
        if entry and not self._is_expired(entry):
            if not agent_name or entry.agent_name == agent_name:
                entry.access_count += 1
                logger.debug("语义缓存精确命中: %s", query[:30])
                return entry.response

        # 语义匹配
        if self._config.enable_embedding and self._embeddings:
            try:
                query_embedding = await self._get_embedding(query)
                if query_embedding:
                    best_key, best_sim = self._find_most_similar(
                        query_embedding, agent_name
                    )
                    if best_sim >= self._config.similarity_threshold and best_key:
                        entry = self._entries.get(best_key)
                        if entry and not self._is_expired(entry):
                            entry.access_count += 1
                            logger.debug(
                                "语义缓存相似命中: query=%s sim=%.3f",
                                query[:30], best_sim,
                            )
                            return entry.response
            except Exception as e:
                logger.debug("语义匹配失败，跳过: %s", e)

        return None

    async def set(
        self,
        query: str,
        response: Any,
        agent_name: str = "",
        ttl: float | None = None,
    ) -> None:
        """写入语义缓存

        Args:
            query: 用户查询文本
            response: LLM 响应
            agent_name: Agent 名称
            ttl: 生存时间(秒)
        """
        query_hash = self._hash_query(query)
        effective_ttl = ttl or self._config.default_ttl

        # 如果覆盖已有条目，先移除旧 embedding
        if query_hash in self._entries:
            self._embeddings = [
                (k, v) for k, v in self._embeddings if k != query_hash
            ]

        # 获取 Embedding
        embedding: list[float] = []
        if self._config.enable_embedding:
            try:
                embedding = await self._get_embedding(query) or []
            except Exception:
                pass

        entry = SemanticCacheEntry(
            query=query,
            query_hash=query_hash,
            response=response,
            embedding=embedding,
            agent_name=agent_name,
            ttl=effective_ttl,
        )

        # 清理过期条目和超限条目
        self._evict_if_needed()

        self._entries[query_hash] = entry
        if embedding:
            self._embeddings.append((query_hash, embedding))

        logger.debug("语义缓存写入: %s", query[:30])

    async def delete(self, query: str) -> bool:
        """删除缓存条目"""
        query_hash = self._hash_query(query)
        if query_hash in self._entries:
            del self._entries[query_hash]
            self._embeddings = [
                (k, v) for k, v in self._embeddings if k != query_hash
            ]
            return True
        return False

    def clear(self) -> None:
        """清空所有缓存"""
        self._entries.clear()
        self._embeddings.clear()

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
        }

    def _hash_query(self, query: str) -> str:
        """对查询文本生成哈希"""
        normalized = query.strip().lower()
        return hashlib.md5(normalized.encode()).hexdigest()

    def _is_expired(self, entry: SemanticCacheEntry) -> bool:
        """检查缓存条目是否过期"""
        return (time.time() - entry.created_at) > entry.ttl

    def _evict_if_needed(self) -> None:
        """清理过期和超限条目"""
        # 清理过期条目
        expired_keys = [
            k for k, v in self._entries.items() if self._is_expired(v)
        ]
        for key in expired_keys:
            del self._entries[key]
            self._embeddings = [
                (k, v) for k, v in self._embeddings if k != key
            ]

        # LRU 淘汰：按访问次数和创建时间排序
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
        """在缓存中查找最相似的条目

        使用余弦相似度计算向量距离。

        Returns:
            (最相似条目的 key, 相似度分数)
        """
        best_key = ""
        best_sim = 0.0

        for key, embedding in self._embeddings:
            # Agent 过滤
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
