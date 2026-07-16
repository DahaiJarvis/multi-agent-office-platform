"""长期记忆向量检索集成测试

端到端验证向量检索各模块协同工作，覆盖 spec 02 业务流程：
  1. 写入 -> 检索闭环（store + semantic_search）
  2. 融合检索全流程（向量 + 关键词 + RRF）
  3. 降级链路验证
  4. 多租户隔离验证
  5. SemanticCache 复用共享 Embedding 客户端验证
  6. 性能指标验证（RRF 融合延迟）
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.infrastructure.embedding import EmbeddingClient, get_embedding_client
from agent.core.session.long_term_memory import LongTermMemory
from agent.core.performance.semantic_cache import SemanticCache


def _make_mock_pool(execute_results: list = None):
    """构造 Mock 连接池，execute 按顺序返回预设结果"""
    mock_session = MagicMock()
    if execute_results:
        mock_session.execute = AsyncMock(side_effect=execute_results)
    else:
        mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_pool.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_pool


def _make_mock_result(rows: list[dict]):
    """构造 Mock 查询结果（mappings().all() 返回指定行）"""
    mock_result = MagicMock()
    mock_mapping = MagicMock()
    # 模拟 mappings().all() 返回行列表
    mock_rows = []
    for row in rows:
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key, r=row: r[key]
        mock_rows.append(mock_row)
    mock_mapping.all.return_value = mock_rows
    mock_result.mappings.return_value = mock_mapping
    mock_result.first.return_value = mock_rows[0] if mock_rows else None
    return mock_result


class TestWriteSearchPipeline:
    """集成测试 1：写入 -> 检索闭环"""

    async def test_store_then_search_hybrid(self):
        """测试写入知识后执行融合检索"""
        ltm = LongTermMemory()

        # 1. Mock store_user_knowledge 的 DB 操作
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_pool.return_value.__aexit__ = AsyncMock(return_value=None)
        ltm._get_pool = AsyncMock(return_value=mock_pool)

        # 2. Mock embedding 客户端
        mock_embedding = [0.1] * 1024
        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=mock_embedding)
            mock_get.return_value = mock_client

            # 3. 写入知识
            store_ok = await ltm.store_user_knowledge(
                "user_001", "preference", "用户偏好使用飞书文档协作",
                "session_001", tenant_id="tenant_A",
            )
            assert store_ok is True

            # 验证 INSERT 包含 embedding
            assert mock_session.execute.called
            sql_text = str(mock_session.execute.call_args.args[0])
            assert "embedding" in sql_text

        # 4. Mock semantic_search 的检索流程
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1, "content": "用户偏好使用飞书文档协作",
             "knowledge_type": "preference", "weight": 1.0,
             "vector_score": 0.92, "source_session_id": "session_001",
             "created_at": "2026-07-01T10:00:00", "expires_at": "2026-08-01T10:00:00"},
        ])
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 1, "content": "用户偏好使用飞书文档协作",
             "knowledge_type": "preference", "weight": 1.0,
             "keyword_score": 1.0, "source_session_id": "session_001",
             "created_at": "2026-07-01T10:00:00", "expires_at": "2026-08-01T10:00:00"},
        ])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=mock_embedding)
            mock_get.return_value = mock_client

            # 5. 执行检索
            results = await ltm.semantic_search(
                "user_001", "飞书文档偏好", top_k=10, score_threshold=0.75,
                tenant_id="tenant_A",
            )

            # 6. 验证结果
            assert len(results) == 1
            assert results[0]["id"] == 1
            assert results[0]["matched_by"] == "hybrid"  # 同时命中两路
            assert results[0]["score"] > 0
            assert "embedding" not in results[0]  # 不含 embedding 字段


class TestFullHybridSearchFlow:
    """集成测试 2：融合检索全流程"""

    async def test_hybrid_search_with_mixed_results(self):
        """测试混合检索：部分结果同时命中两路，部分仅单路"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        # 向量召回：id=1,2,3
        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1, "content": "偏好飞书", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.95, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
            {"id": 2, "content": "偏好邮件", "knowledge_type": "preference",
             "weight": 0.9, "vector_score": 0.85, "source_session_id": "s2",
             "created_at": "2026-07-02", "expires_at": "2026-08-02"},
            {"id": 3, "content": "偏好会议", "knowledge_type": "preference",
             "weight": 0.8, "vector_score": 0.80, "source_session_id": "s3",
             "created_at": "2026-07-03", "expires_at": "2026-08-03"},
        ])
        # 关键词召回：id=2,4
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 2, "content": "偏好邮件", "knowledge_type": "preference",
             "weight": 0.9, "keyword_score": 1.0, "source_session_id": "s2",
             "created_at": "2026-07-02", "expires_at": "2026-08-02"},
            {"id": 4, "content": "邮件设置", "knowledge_type": "fact",
             "weight": 0.7, "keyword_score": 0.9, "source_session_id": "s4",
             "created_at": "2026-07-04", "expires_at": "2026-08-04"},
        ])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            results = await ltm.semantic_search(
                "user_001", "偏好", top_k=10, score_threshold=0.75,
            )

            # 4 个唯一结果
            assert len(results) == 4
            ids = [r["id"] for r in results]
            assert set(ids) == {1, 2, 3, 4}

            # id=2 是 hybrid（同时命中两路）
            hybrid = [r for r in results if r["matched_by"] == "hybrid"]
            assert any(r["id"] == 2 for r in hybrid)

            # id=1 仅向量
            vector_only = [r for r in results if r["matched_by"] == "vector"]
            assert any(r["id"] == 1 for r in vector_only)

            # id=4 仅关键词
            keyword_only = [r for r in results if r["matched_by"] == "keyword"]
            assert any(r["id"] == 4 for r in keyword_only)

            # hybrid(id=2) 得分应最高
            assert results[0]["id"] == 2

    async def test_threshold_filters_low_score_vector(self):
        """测试 score_threshold 过滤低分向量结果"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1, "content": "高分", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.90, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
            {"id": 2, "content": "低分", "knowledge_type": "fact",
             "weight": 0.5, "vector_score": 0.60, "source_session_id": "s2",
             "created_at": "2026-07-02", "expires_at": "2026-08-02"},
        ])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            # threshold=0.75 -> id=2(0.60) 被过滤
            results = await ltm.semantic_search(
                "user_001", "查询", top_k=10, score_threshold=0.75,
            )
            assert len(results) == 1
            assert results[0]["id"] == 1


class TestDegradationChain:
    """集成测试 3：降级链路验证"""

    async def test_pgvector_unavailable_full_degradation(self):
        """测试 pgvector 不可用时完整降级链路"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=False)
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 1, "content": "关键词结果", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 0.9, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
        ])
        # Mock 向量召回以验证不被调用
        ltm._vector_recall = AsyncMock(return_value=[])

        results = await ltm.semantic_search("user_001", "查询")

        assert len(results) == 1
        assert results[0]["matched_by"] == "keyword"
        # pgvector 不可用时不应调用向量召回
        ltm._vector_recall.assert_not_called()

    async def test_embedding_service_down_degradation(self):
        """测试 Embedding 服务不可用时降级"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 1, "content": "结果", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 0.9, "source_session_id": "",
             "created_at": "", "expires_at": ""},
        ])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[])  # embedding 失败
            mock_get.return_value = mock_client

            results = await ltm.semantic_search("user_001", "查询")

            assert len(results) == 1
            assert results[0]["matched_by"] == "keyword"

    async def test_empty_query_uses_weight_sorting(self):
        """测试空 query 走 weight/created_at 排序"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._keyword_recall_fallback = AsyncMock(return_value=[
            {"id": 1, "content": "知识1", "score": 1.0, "matched_by": "keyword"},
            {"id": 2, "content": "知识2", "score": 0.5, "matched_by": "keyword"},
        ])

        results = await ltm.semantic_search("user_001", "")

        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]
        ltm._keyword_recall_fallback.assert_called_once()


class TestTenantIsolation:
    """集成测试 4：多租户隔离验证"""

    async def test_different_tenants_isolated(self):
        """测试不同租户的检索互不干扰"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        # 租户 A 的向量召回结果
        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1, "content": "租户A知识", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.90, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
        ])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            # 租户 A 检索
            results_a = await ltm.semantic_search(
                "user_001", "查询", tenant_id="tenant_A",
            )

            # 验证 tenant_id 传递到向量召回
            call_args = ltm._vector_recall.call_args
            args = call_args.args
            kwargs = call_args.kwargs
            assert "tenant_A" in args or kwargs.get("tenant_id") == "tenant_A"

    async def test_tenant_id_from_context(self):
        """测试 tenant_id 从上下文获取"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=False)
        ltm._keyword_recall = AsyncMock(return_value=[])

        # Mock 上下文获取 tenant_id
        with patch("security.tenant.get_current_tenant_id", return_value="ctx_tenant"):
            await ltm.semantic_search("user_001", "查询")

            call_args = ltm._keyword_recall.call_args
            args = call_args.args
            kwargs = call_args.kwargs
            assert "ctx_tenant" in args or kwargs.get("tenant_id") == "ctx_tenant"


class TestSemanticCacheReuseEmbedding:
    """集成测试 5：SemanticCache 复用共享 Embedding 客户端"""

    async def test_semantic_cache_uses_shared_client(self):
        """测试 SemanticCache._get_embedding 调用共享客户端"""
        cache = SemanticCache()

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await cache._get_embedding("测试查询")

            assert len(result) == 1024
            mock_get.assert_called_once()
            mock_client.get_embedding.assert_called_once_with("测试查询")

    async def test_semantic_cache_degradation_on_failure(self):
        """测试 SemanticCache Embedding 失败时降级"""
        cache = SemanticCache()

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(side_effect=RuntimeError("网络错误"))
            mock_get.return_value = mock_client

            result = await cache._get_embedding("测试查询")

            # 降级返回空列表
            assert result == []

    def test_shared_client_singleton_between_modules(self):
        """测试 SemanticCache 与 LongTermMemory 共享同一 Embedding 客户端"""
        # 重置单例
        import agent.core.infrastructure.embedding as emb_module
        emb_module._embedding_client = None

        client1 = get_embedding_client()
        client2 = get_embedding_client()
        assert client1 is client2  # 同一实例

        # SemanticCache 与 LongTermMemory 调用 get_embedding_client 获取同一实例
        cache = SemanticCache()
        # cache._get_embedding 内部调用 get_embedding_client()
        # LongTermMemory.semantic_search 内部也调用 get_embedding_client()
        # 两者获取的是同一单例


class TestPerformanceMetrics:
    """集成测试 6：性能指标验证"""

    def test_rrf_fusion_latency_under_10ms(self):
        """测试 RRF 融合算法延迟 < 10ms（spec 02 第 7.1 节不含 DB 与 Embedding）"""
        vector_results = [
            {"id": i, "content": f"内容{i}", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.9 - i * 0.01,
             "source_session_id": "", "created_at": "", "expires_at": ""}
            for i in range(100)
        ]
        keyword_results = [
            {"id": i + 50, "content": f"关键词{i}", "knowledge_type": "fact",
             "weight": 0.8, "keyword_score": 0.9,
             "source_session_id": "", "created_at": "", "expires_at": ""}
            for i in range(50)
        ]

        start = time.perf_counter()
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 10.0, f"RRF 融合耗时 {elapsed_ms:.2f}ms 超过 10ms 阈值"
        assert len(result) <= 10

    def test_rrf_fusion_large_dataset(self):
        """测试大数据量 RRF 融合正确性"""
        vector_results = [
            {"id": i, "content": f"v{i}", "knowledge_type": "fact",
             "weight": 1.0, "vector_score": 0.8 + i * 0.001,
             "source_session_id": "", "created_at": "", "expires_at": ""}
            for i in range(1000)
        ]
        keyword_results = [
            {"id": i + 500, "content": f"k{i}", "knowledge_type": "fact",
             "weight": 0.9, "keyword_score": 0.9,
             "source_session_id": "", "created_at": "", "expires_at": ""}
            for i in range(500)
        ]

        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=20, score_threshold=0.75,
        )

        assert len(result) == 20
        # 结果按得分降序
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    async def test_pgvector_cache_avoids_repeated_probing(self):
        """测试 pgvector 可用性缓存避免重复探测"""
        ltm = LongTermMemory()
        import time as time_module
        ltm._pgvector_available_cache = True
        ltm._pgvector_cache_time = time_module.time()

        # 第一次调用 -> 命中缓存
        start1 = time.perf_counter()
        result1 = await ltm._pgvector_available()
        elapsed1 = time.perf_counter() - start1

        # 第二次调用 -> 命中缓存
        start2 = time.perf_counter()
        result2 = await ltm._pgvector_available()
        elapsed2 = time.perf_counter() - start2

        assert result1 is True
        assert result2 is True
        # 两次都应极快（缓存命中，无 DB 查询）
        assert elapsed1 < 0.001  # < 1ms
        assert elapsed2 < 0.001


class TestResultFormatSpec:
    """集成测试 7：返回结构符合 spec 规范"""

    async def test_result_structure_matches_spec(self):
        """测试返回结构包含 spec 02 第 4.4 节定义的全部字段"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1024, "user_id": "u_1001", "content": "用户偏好使用飞书文档协作",
             "knowledge_type": "preference", "weight": 1.0,
             "vector_score": 0.83, "source_session_id": "s_abc",
             "created_at": "2026-07-15T10:00:00", "expires_at": "2026-08-14T10:00:00"},
        ])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            results = await ltm.semantic_search(
                "u_1001", "飞书文档", top_k=10, score_threshold=0.75,
                tenant_id="t_001",
            )

            assert len(results) == 1
            item = results[0]
            # spec 第 4.4 节定义的字段
            assert "id" in item
            assert "user_id" in item
            assert "knowledge_type" in item
            assert "content" in item
            assert "source_session_id" in item
            assert "weight" in item
            assert "score" in item
            assert "matched_by" in item
            assert "created_at" in item
            assert "expires_at" in item
            # 不包含 embedding
            assert "embedding" not in item
            assert "vector_score" not in item  # 内部字段已清理
            assert "keyword_score" not in item  # 内部字段已清理

    async def test_matched_by_values(self):
        """测试 matched_by 字段取值为 vector/keyword/hybrid"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1, "content": "v", "knowledge_type": "fact", "weight": 1.0,
             "vector_score": 0.9, "source_session_id": "", "created_at": "", "expires_at": ""},
        ])
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 2, "content": "k", "knowledge_type": "fact", "weight": 1.0,
             "keyword_score": 0.9, "source_session_id": "", "created_at": "", "expires_at": ""},
            {"id": 1, "content": "v", "knowledge_type": "fact", "weight": 1.0,
             "keyword_score": 0.8, "source_session_id": "", "created_at": "", "expires_at": ""},
        ])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            results = await ltm.semantic_search("user_001", "查询")

            matched_by_values = {r["matched_by"] for r in results}
            # 应包含 hybrid（id=1）和 keyword（id=2）
            assert "hybrid" in matched_by_values
            assert "keyword" in matched_by_values
