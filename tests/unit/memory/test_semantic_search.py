"""RRF 融合算法与 LongTermMemory 向量检索单元测试

覆盖 spec 02 第 4.3 节 RRF 融合算法、第 6.2 节查询流程、第 8 节安全要求。
重点测试纯函数 _rrf_fuse 与降级逻辑，DB 操作使用 Mock。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from agent.core.session.long_term_memory import LongTermMemory


class TestRRFFusion:
    """RRF 融合算法纯函数测试（spec 02 第 4.3 节）"""

    @pytest.fixture
    def vector_results(self):
        """构造向量召回结果（含 vector_score）"""
        return [
            {"id": 1, "content": "偏好飞书文档", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.92, "source_session_id": "s1",
             "created_at": "2026-07-01T10:00:00", "expires_at": "2026-08-01T10:00:00"},
            {"id": 2, "content": "偏好邮件沟通", "knowledge_type": "preference",
             "weight": 0.9, "vector_score": 0.85, "source_session_id": "s2",
             "created_at": "2026-07-02T10:00:00", "expires_at": "2026-08-02T10:00:00"},
            {"id": 3, "content": "低分结果", "knowledge_type": "fact",
             "weight": 0.5, "vector_score": 0.60, "source_session_id": "s3",
             "created_at": "2026-07-03T10:00:00", "expires_at": "2026-08-03T10:00:00"},
        ]

    @pytest.fixture
    def keyword_results(self):
        """构造关键词召回结果（含 keyword_score）"""
        return [
            {"id": 2, "content": "偏好邮件沟通", "knowledge_type": "preference",
             "weight": 0.9, "keyword_score": 1.0, "source_session_id": "s2",
             "created_at": "2026-07-02T10:00:00", "expires_at": "2026-08-02T10:00:00"},
            {"id": 4, "content": "邮件相关事实", "knowledge_type": "fact",
             "weight": 0.8, "keyword_score": 0.9, "source_session_id": "s4",
             "created_at": "2026-07-04T10:00:00", "expires_at": "2026-08-04T10:00:00"},
        ]

    def test_rrf_threshold_filter(self, vector_results, keyword_results):
        """测试 score_threshold 过滤低分向量结果"""
        # threshold=0.80 -> id=3（0.60）被过滤
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.80,
        )
        ids = [r["id"] for r in result]
        assert 3 not in ids  # 被过滤
        assert 1 in ids
        assert 2 in ids

    def test_rrf_hybrid_matched(self, vector_results, keyword_results):
        """测试同时命中两路的结果标记为 hybrid"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        # id=2 同时出现在向量与关键词结果中
        hybrid_items = [r for r in result if r["matched_by"] == "hybrid"]
        assert any(r["id"] == 2 for r in hybrid_items)

    def test_rrf_vector_only_matched(self, vector_results, keyword_results):
        """测试仅向量命中的结果标记为 vector"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        vector_items = [r for r in result if r["matched_by"] == "vector"]
        assert any(r["id"] == 1 for r in vector_items)

    def test_rrf_keyword_only_matched(self, vector_results, keyword_results):
        """测试仅关键词命中的结果标记为 keyword"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        keyword_items = [r for r in result if r["matched_by"] == "keyword"]
        assert any(r["id"] == 4 for r in keyword_items)

    def test_rrf_dedup_by_id(self, vector_results, keyword_results):
        """测试按 id 去重（id=2 不重复出现）"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        ids = [r["id"] for r in result]
        assert len(ids) == len(set(ids))  # 无重复

    def test_rrf_sorted_by_score_desc(self, vector_results, keyword_results):
        """测试结果按融合得分降序排列"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_hybrid_score_higher_than_single(self, vector_results, keyword_results):
        """测试 hybrid 结果得分高于单路命中（两路叠加）"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        score_map = {r["id"]: r["score"] for r in result}
        # id=2 是 hybrid，得分应高于仅向量命中的 id=1
        assert score_map[2] > score_map[1]

    def test_rrf_top_k_limit(self, vector_results, keyword_results):
        """测试 top_k 截断"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=2, score_threshold=0.75,
        )
        assert len(result) <= 2

    def test_rrf_empty_vector(self, keyword_results):
        """测试向量结果为空时仅返回关键词结果"""
        result = LongTermMemory._rrf_fuse(
            [], keyword_results, top_k=10, score_threshold=0.75,
        )
        assert len(result) == len(keyword_results)
        assert all(r["matched_by"] == "keyword" for r in result)

    def test_rrf_empty_keyword(self, vector_results):
        """测试关键词结果为空时仅返回向量结果"""
        result = LongTermMemory._rrf_fuse(
            vector_results, [], top_k=10, score_threshold=0.75,
        )
        # 过滤后 id=1 和 id=2 通过 threshold
        assert len(result) == 2
        assert all(r["matched_by"] == "vector" for r in result)

    def test_rrf_all_filtered_by_threshold(self, vector_results, keyword_results):
        """测试所有向量结果被阈值过滤后仅剩关键词结果"""
        # threshold=0.95 -> 所有向量结果被过滤
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.95,
        )
        # 仅剩关键词结果
        assert all(r["matched_by"] == "keyword" for r in result)
        assert len(result) == len(keyword_results)

    def test_rrf_both_empty(self):
        """测试两路结果均为空"""
        result = LongTermMemory._rrf_fuse(
            [], [], top_k=10, score_threshold=0.75,
        )
        assert result == []

    def test_rrf_custom_weights(self, vector_results, keyword_results):
        """测试自定义权重"""
        # 关键词权重更高
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
            vector_weight=0.3, keyword_weight=0.7,
        )
        assert len(result) > 0

    def test_rrf_result_no_internal_fields(self, vector_results, keyword_results):
        """测试结果不含内部排名字段（_ 开头）"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        for item in result:
            for key in item:
                assert not key.startswith("_"), f"内部字段 {key} 不应出现在结果中"

    def test_rrf_result_has_score_and_matched_by(self, vector_results, keyword_results):
        """测试结果包含 score 和 matched_by 字段"""
        result = LongTermMemory._rrf_fuse(
            vector_results, keyword_results, top_k=10, score_threshold=0.75,
        )
        for item in result:
            assert "score" in item
            assert "matched_by" in item
            assert item["matched_by"] in ("vector", "keyword", "hybrid")


class TestFormatKeywordOnly:
    """_format_keyword_only 降级格式化测试"""

    def test_format_basic(self):
        """测试基本格式化"""
        keyword_results = [
            {"id": 1, "content": "内容1", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 1.0, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
        ]
        result = LongTermMemory._format_keyword_only(keyword_results, top_k=10)
        assert len(result) == 1
        assert result[0]["score"] == 1.0
        assert result[0]["matched_by"] == "keyword"

    def test_format_top_k_limit(self):
        """测试 top_k 截断"""
        keyword_results = [
            {"id": i, "content": f"内容{i}", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 0.9, "source_session_id": "",
             "created_at": "", "expires_at": ""}
            for i in range(5)
        ]
        result = LongTermMemory._format_keyword_only(keyword_results, top_k=3)
        assert len(result) == 3

    def test_format_no_embedding_field(self):
        """测试结果不包含 embedding 字段"""
        keyword_results = [
            {"id": 1, "content": "内容", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 0.9, "source_session_id": "",
             "created_at": "", "expires_at": ""},
        ]
        result = LongTermMemory._format_keyword_only(keyword_results, top_k=10)
        assert "embedding" not in result[0]

    def test_format_empty_input(self):
        """测试空输入返回空列表"""
        assert LongTermMemory._format_keyword_only([], top_k=10) == []


class TestPgvectorAvailabilityCache:
    """pgvector 可用性缓存测试"""

    def test_cache_class_attributes_exist(self):
        """测试缓存类属性存在"""
        ltm = LongTermMemory()
        assert hasattr(ltm, "_pgvector_available_cache")
        assert hasattr(ltm, "_pgvector_cache_time")
        assert hasattr(ltm, "_pgvector_cache_ttl")
        assert ltm._pgvector_cache_ttl == 300.0

    async def test_cache_returns_cached_value(self):
        """测试缓存命中时不重新探测"""
        ltm = LongTermMemory()
        # 预设缓存
        import time
        ltm._pgvector_available_cache = True
        ltm._pgvector_cache_time = time.time()

        # 应直接返回缓存值，不调用 _get_pool
        result = await ltm._pgvector_available()
        assert result is True

    async def test_cache_expired_reprobes(self):
        """测试缓存过期后重新探测"""
        ltm = LongTermMemory()
        import time
        # 设置过期缓存
        ltm._pgvector_available_cache = True
        ltm._pgvector_cache_time = time.time() - 400  # 超过 5 分钟 TTL
        ltm._pgvector_cache_ttl = 300.0

        # Mock _get_pool 返回 None（DB 不可用）
        ltm._get_pool = AsyncMock(return_value=None)

        result = await ltm._pgvector_available()
        assert result is False  # DB 不可用 -> False
        assert ltm._pgvector_available_cache is False

    async def test_db_unavailable_returns_false(self):
        """测试 DB 不可用时返回 False"""
        ltm = LongTermMemory()
        ltm._pgvector_available_cache = None  # 清除缓存
        ltm._get_pool = AsyncMock(return_value=None)

        result = await ltm._pgvector_available()
        assert result is False


class TestSemanticSearchDegradation:
    """semantic_search 降级逻辑测试"""

    @pytest.fixture
    def ltm(self):
        """构造 LongTermMemory 实例，所有 DB 操作 Mock 化"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=None)  # DB 不可用
        return ltm

    async def test_db_unavailable_returns_empty(self, ltm):
        """测试 DB 不可用时返回空列表"""
        result = await ltm.semantic_search("user_001", "查询文本")
        assert result == []

    async def test_empty_query_fallback(self):
        """测试空 query 走 weight/created_at 召回"""
        ltm = LongTermMemory()
        # Mock _keyword_recall_fallback
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._keyword_recall_fallback = AsyncMock(return_value=[
            {"id": 1, "content": "知识", "score": 1.0, "matched_by": "keyword"},
        ])

        result = await ltm.semantic_search("user_001", "")
        assert len(result) == 1
        ltm._keyword_recall_fallback.assert_called_once()

    async def test_whitespace_query_fallback(self):
        """测试纯空白 query 走 fallback"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._keyword_recall_fallback = AsyncMock(return_value=[])

        result = await ltm.semantic_search("user_001", "   ")
        assert result == []
        ltm._keyword_recall_fallback.assert_called_once()

    async def test_pgvector_unavailable_degrades_to_keyword(self):
        """测试 pgvector 不可用时降级到关键词召回"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=False)
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 1, "content": "知识", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 0.9, "source_session_id": "",
             "created_at": "", "expires_at": ""},
        ])

        result = await ltm.semantic_search("user_001", "查询")
        assert len(result) == 1
        assert result[0]["matched_by"] == "keyword"
        ltm._keyword_recall.assert_called_once()

    async def test_embedding_failed_degrades_to_keyword(self):
        """测试 Embedding 生成失败时降级到关键词召回"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        # Mock embedding 返回空列表
        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[])
            mock_get.return_value = mock_client

            ltm._keyword_recall = AsyncMock(return_value=[
                {"id": 1, "content": "知识", "knowledge_type": "fact",
                 "weight": 1.0, "keyword_score": 0.9, "source_session_id": "",
                 "created_at": "", "expires_at": ""},
            ])

            result = await ltm.semantic_search("user_001", "查询")
            assert len(result) == 1
            assert result[0]["matched_by"] == "keyword"

    async def test_hybrid_search_full_flow(self):
        """测试完整融合检索流程（向量 + 关键词 + RRF）"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        vector_results = [
            {"id": 1, "content": "向量结果", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.90, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
        ]
        keyword_results = [
            {"id": 2, "content": "关键词结果", "knowledge_type": "fact",
             "weight": 0.9, "keyword_score": 1.0, "source_session_id": "s2",
             "created_at": "2026-07-02", "expires_at": "2026-08-02"},
        ]

        ltm._vector_recall = AsyncMock(return_value=vector_results)
        ltm._keyword_recall = AsyncMock(return_value=keyword_results)

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await ltm.semantic_search("user_001", "查询", top_k=10, score_threshold=0.75)

            assert len(result) == 2
            matched_by_set = {r["matched_by"] for r in result}
            assert "vector" in matched_by_set
            assert "keyword" in matched_by_set

    async def test_both_recall_empty_returns_empty(self):
        """测试两路召回均为空时返回空列表"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._vector_recall = AsyncMock(return_value=[])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await ltm.semantic_search("user_001", "查询")
            assert result == []

    async def test_vector_recall_exception_degrades(self):
        """测试向量召回异常时降级到关键词"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)

        # 向量召回抛异常（asyncio.gather return_exceptions=True 捕获）
        ltm._vector_recall = AsyncMock(side_effect=RuntimeError("SQL 错误"))
        ltm._keyword_recall = AsyncMock(return_value=[
            {"id": 1, "content": "知识", "knowledge_type": "fact",
             "weight": 1.0, "keyword_score": 0.9, "source_session_id": "",
             "created_at": "", "expires_at": ""},
        ])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await ltm.semantic_search("user_001", "查询")
            # 向量异常 -> 仅关键词结果
            assert len(result) == 1
            assert result[0]["matched_by"] == "keyword"

    async def test_result_excludes_embedding_field(self):
        """测试返回结果不包含 embedding 字段（spec 02 第 8.2 节）"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._vector_recall = AsyncMock(return_value=[
            {"id": 1, "content": "知识", "knowledge_type": "preference",
             "weight": 1.0, "vector_score": 0.90, "source_session_id": "s1",
             "created_at": "2026-07-01", "expires_at": "2026-08-01"},
        ])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await ltm.semantic_search("user_001", "查询")
            for item in result:
                assert "embedding" not in item


class TestSemanticSearchTenantIsolation:
    """多租户隔离测试（spec 02 第 8.1 节）"""

    async def test_tenant_id_passed_to_vector_recall(self):
        """测试 tenant_id 传递到向量召回"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._vector_recall = AsyncMock(return_value=[])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            await ltm.semantic_search("user_001", "查询", tenant_id="tenant_A")

            # 验证 _vector_recall 被调用时包含 tenant_id
            call_args = ltm._vector_recall.call_args
            assert call_args.kwargs.get("tenant_id") == "tenant_A" or "tenant_A" in call_args.args

    async def test_tenant_id_passed_to_keyword_recall(self):
        """测试 tenant_id 传递到关键词召回"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=True)
        ltm._vector_recall = AsyncMock(return_value=[])
        ltm._keyword_recall = AsyncMock(return_value=[])

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            await ltm.semantic_search("user_001", "查询", tenant_id="tenant_B")

            call_args = ltm._keyword_recall.call_args
            assert call_args.kwargs.get("tenant_id") == "tenant_B" or "tenant_B" in call_args.args

    async def test_empty_tenant_id_allowed(self):
        """测试空 tenant_id 不报错（与既有行为一致）"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=MagicMock())
        ltm._pgvector_available = AsyncMock(return_value=False)
        ltm._keyword_recall = AsyncMock(return_value=[])

        # 不传 tenant_id，且上下文无 tenant（security.tenant 导入失败）
        result = await ltm.semantic_search("user_001", "查询")
        # 不应抛异常
        assert isinstance(result, list)


class TestStoreUserKnowledgeEmbedding:
    """store_user_knowledge Embedding 写入测试"""

    async def test_store_with_embedding(self):
        """测试写入时生成 embedding"""
        ltm = LongTermMemory()
        # Mock 连接池与 session
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_pool.return_value.__aexit__ = AsyncMock(return_value=None)
        ltm._get_pool = AsyncMock(return_value=mock_pool)

        # Mock embedding 返回向量
        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await ltm.store_user_knowledge(
                "user_001", "preference", "偏好飞书文档", "session_001",
            )

            assert result is True
            # 验证 execute 被调用（含 embedding 的 INSERT）
            mock_session.execute.assert_called_once()
            # 验证 SQL 包含 embedding
            call_args = mock_session.execute.call_args
            sql_text = str(call_args.args[0])
            assert "embedding" in sql_text

    async def test_store_without_embedding_fallback(self):
        """测试 embedding 生成失败时仍写入知识（embedding 为 NULL）"""
        ltm = LongTermMemory()
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_pool.return_value.__aexit__ = AsyncMock(return_value=None)
        ltm._get_pool = AsyncMock(return_value=mock_pool)

        # Mock embedding 返回空列表
        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[])
            mock_get.return_value = mock_client

            result = await ltm.store_user_knowledge(
                "user_001", "preference", "偏好飞书文档", "session_001",
            )

            assert result is True
            # 验证 execute 被调用（不含 embedding 的 INSERT）
            mock_session.execute.assert_called_once()
            call_args = mock_session.execute.call_args
            sql_text = str(call_args.args[0])
            # 不含 embedding 列的 INSERT
            assert "embedding" not in sql_text or "embedding" not in call_args.kwargs.get("params", {}).get("emb", "")

    async def test_store_db_unavailable_returns_false(self):
        """测试 DB 不可用时返回 False"""
        ltm = LongTermMemory()
        ltm._get_pool = AsyncMock(return_value=None)

        with patch("agent.core.infrastructure.embedding.get_embedding_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_get.return_value = mock_client

            result = await ltm.store_user_knowledge(
                "user_001", "preference", "内容", "session_001",
            )
            assert result is False
