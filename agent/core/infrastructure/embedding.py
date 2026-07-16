"""共享 Embedding 客户端

将 SemanticCache._get_embedding 的实现下沉为共享工具，
供 L3 长期记忆向量检索与语义缓存共同调用。

设计原则（遵循 spec 3.3 节）：
  - 保留 SemanticCache 既有逻辑不重复造轮子
  - 保持原有降级语义：调用失败返回空列表，由调用方决定是否降级
  - 延迟初始化，避免启动时强依赖 DashScope SDK

使用方式：
    from agent.core.infrastructure.embedding import get_embedding_client

    client = get_embedding_client()
    embedding = await client.get_embedding("查询文本")
    if embedding:
        # 向量检索
    else:
        # 降级到关键词检索
"""

import logging
from typing import Any

from agent.core.infrastructure.config import get_settings

logger = logging.getLogger(__name__)

# 默认 Embedding 模型（与 SemanticCache 配置一致）
_DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"

# Embedding 向量维度（text-embedding-v3 输出 1024 维）
EMBEDDING_DIMENSION = 1024


class EmbeddingClient:
    """共享 Embedding 客户端

    封装阿里云 DashScope TextEmbedding API 调用，
    支持单条与批量文本向量化。

    降级策略：
      - SDK 未安装或 API Key 缺失 -> 返回空列表
      - API 调用失败 -> 返回空列表
      - 调用方根据空列表自行决定降级逻辑
    """

    def __init__(self, model: str = _DEFAULT_EMBEDDING_MODEL) -> None:
        """初始化 Embedding 客户端

        Args:
            model: Embedding 模型名称，默认 text-embedding-v3
        """
        self._model = model
        self._client: Any = None
        self._initialized = False

    def _ensure_client(self) -> Any:
        """延迟初始化 DashScope 客户端

        保持与 SemanticCache._get_embedding 原有初始化逻辑一致。
        初始化失败时返回 None，调用方降级处理。
        """
        if self._initialized:
            return self._client

        self._initialized = True
        try:
            import dashscope

            settings = get_settings()
            dashscope.api_key = settings.dashscope_api_key
            self._client = dashscope
        except Exception as e:
            logger.debug("Embedding 客户端初始化失败: %s", e)
            self._client = None

        return self._client

    async def get_embedding(self, text: str) -> list[float]:
        """获取单条文本的 Embedding 向量

        使用阿里云 DashScope Embedding API。
        降级方案：返回空列表，由调用方决定是否降级。

        Args:
            text: 待向量化的文本

        Returns:
            1024 维浮点向量列表，失败时返回空列表
        """
        client = self._ensure_client()
        if client is None:
            return []

        try:
            resp = client.TextEmbedding.call(
                model=self._model,
                input=text,
            )
            if resp.status_code == 200:
                return resp.output["embeddings"][0]["embedding"]
        except Exception as e:
            logger.debug("Embedding 调用失败: %s", e)

        return []

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本的 Embedding 向量

        利用 DashScope TextEmbedding 的 batch input 能力，
        减少 API 调用次数，适用于存量数据回填场景。

        Args:
            texts: 待向量化的文本列表

        Returns:
            向量列表（与输入文本一一对应），失败时对应位置为空列表
        """
        if not texts:
            return []

        client = self._ensure_client()
        if client is None:
            return [[] for _ in texts]

        try:
            resp = client.TextEmbedding.call(
                model=self._model,
                input=texts,
            )
            if resp.status_code == 200:
                embeddings = resp.output["embeddings"]
                return [e["embedding"] for e in embeddings]
        except Exception as e:
            logger.debug("批量 Embedding 调用失败: %s", e)

        return [[] for _ in texts]

    @property
    def model(self) -> str:
        """当前使用的 Embedding 模型名称"""
        return self._model

    @property
    def available(self) -> bool:
        """客户端是否可用（已初始化且客户端非 None）"""
        return self._ensure_client() is not None


# 全局共享 Embedding 客户端单例
_embedding_client: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    """获取全局共享 Embedding 客户端实例

    供 SemanticCache 与 LongTermMemory 共同复用，
    避免重复初始化 DashScope 客户端。

    Returns:
        EmbeddingClient 单例
    """
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = EmbeddingClient()
    return _embedding_client
