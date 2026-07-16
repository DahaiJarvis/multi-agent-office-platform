"""共享 Embedding 客户端单元测试

覆盖 spec 02 第 3.3 节 Embedding 复用方案：
  - 延迟初始化
  - 单条/批量向量化
  - 降级语义（返回空列表）
  - 单例获取
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.core.infrastructure.embedding import (
    EmbeddingClient,
    get_embedding_client,
    EMBEDDING_DIMENSION,
    _DEFAULT_EMBEDDING_MODEL,
)


class TestEmbeddingClientInit:
    """EmbeddingClient 初始化测试"""

    def test_default_model(self):
        """测试默认模型名称"""
        client = EmbeddingClient()
        assert client.model == _DEFAULT_EMBEDDING_MODEL
        assert client.model == "text-embedding-v3"

    def test_custom_model(self):
        """测试自定义模型名称"""
        client = EmbeddingClient(model="text-embedding-v2")
        assert client.model == "text-embedding-v2"

    def test_dimension_constant(self):
        """测试向量维度常量"""
        assert EMBEDDING_DIMENSION == 1024

    def test_lazy_initialization(self):
        """测试延迟初始化：构造时不初始化客户端"""
        client = EmbeddingClient()
        assert client._initialized is False
        assert client._client is None

    def test_available_false_when_init_fails(self):
        """测试初始化失败时 available 为 False"""
        client = EmbeddingClient()
        # 模拟 dashscope 导入失败
        client._initialized = True
        client._client = None
        assert client.available is False


class TestEmbeddingClientGetEmbedding:
    """get_embedding 方法测试"""

    async def test_get_embedding_success(self):
        """测试成功获取向量"""
        client = EmbeddingClient()

        # Mock dashscope 客户端
        mock_dashscope = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.output = {"embeddings": [{"embedding": [0.1] * 1024}]}
        mock_dashscope.TextEmbedding.call = MagicMock(return_value=mock_resp)
        client._client = mock_dashscope
        client._initialized = True

        result = await client.get_embedding("测试文本")
        assert len(result) == 1024
        assert result[0] == 0.1

    async def test_get_embedding_api_error(self):
        """测试 API 返回非 200 时返回空列表"""
        client = EmbeddingClient()
        mock_dashscope = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_dashscope.TextEmbedding.call = MagicMock(return_value=mock_resp)
        client._client = mock_dashscope
        client._initialized = True

        result = await client.get_embedding("测试文本")
        assert result == []

    async def test_get_embedding_exception(self):
        """测试 API 调用抛异常时返回空列表"""
        client = EmbeddingClient()
        mock_dashscope = MagicMock()
        mock_dashscope.TextEmbedding.call = MagicMock(side_effect=RuntimeError("网络错误"))
        client._client = mock_dashscope
        client._initialized = True

        result = await client.get_embedding("测试文本")
        assert result == []

    async def test_get_embedding_no_client(self):
        """测试客户端未初始化时返回空列表"""
        client = EmbeddingClient()
        client._client = None
        client._initialized = True

        result = await client.get_embedding("测试文本")
        assert result == []


class TestEmbeddingClientBatch:
    """get_embeddings_batch 方法测试"""

    async def test_batch_success(self):
        """测试批量获取向量成功"""
        client = EmbeddingClient()
        mock_dashscope = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.output = {
            "embeddings": [
                {"embedding": [0.1] * 1024},
                {"embedding": [0.2] * 1024},
            ]
        }
        mock_dashscope.TextEmbedding.call = MagicMock(return_value=mock_resp)
        client._client = mock_dashscope
        client._initialized = True

        result = await client.get_embeddings_batch(["文本1", "文本2"])
        assert len(result) == 2
        assert len(result[0]) == 1024
        assert result[0][0] == 0.1
        assert result[1][0] == 0.2

    async def test_batch_empty_input(self):
        """测试空输入返回空列表"""
        client = EmbeddingClient()
        result = await client.get_embeddings_batch([])
        assert result == []

    async def test_batch_api_error(self):
        """测试批量 API 失败返回空向量列表（与输入一一对应）"""
        client = EmbeddingClient()
        mock_dashscope = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_dashscope.TextEmbedding.call = MagicMock(return_value=mock_resp)
        client._client = mock_dashscope
        client._initialized = True

        result = await client.get_embeddings_batch(["文本1", "文本2", "文本3"])
        assert len(result) == 3
        assert all(r == [] for r in result)


class TestEmbeddingClientSingleton:
    """单例模式测试"""

    def test_get_embedding_client_singleton(self):
        """测试 get_embedding_client 返回单例"""
        # 重置全局单例
        import agent.core.infrastructure.embedding as emb_module
        emb_module._embedding_client = None

        client1 = get_embedding_client()
        client2 = get_embedding_client()
        assert client1 is client2

    def test_get_embedding_client_type(self):
        """测试返回类型为 EmbeddingClient"""
        client = get_embedding_client()
        assert isinstance(client, EmbeddingClient)
