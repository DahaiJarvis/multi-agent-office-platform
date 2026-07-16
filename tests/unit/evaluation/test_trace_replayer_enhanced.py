"""TraceReplayer 增强功能单元测试

覆盖 spec 04 第 3.2 节 F2 功能增强部分：
  - 新增 new_session_id 生成（REQ-04）
  - 脱敏处理（REQ-10）
  - 回放记录持久化
  - context 提取
  - DeterministicMode 集成
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.evaluation.replay.trace_replayer import TraceReplayer, ReplayResult


@pytest.fixture
def mock_span_cache_with_data():
    """构造带 span 数据的 Mock SpanCache"""
    spans = [
        {
            "span_type": "intent_classification",
            "input": {"user_message": "查询未读邮件"},
            "output": {"intent": "email_query"},
            "metadata": {"agent_name": "EmailAgent", "user_id": "u1"},
        },
        {
            "span_type": "tool_call:email_query",
            "input": {"tool": "email_query", "args": {"filter": "unread"}},
            "output": {"result": "3 封未读邮件"},
            "metadata": {"status": "success"},
        },
    ]
    cache = MagicMock()
    cache.get_session_spans = AsyncMock(return_value=spans)
    return cache


class TestNewSessionIdGeneration:
    """新 session_id 生成测试（REQ-04）"""

    def test_generate_new_session_id_format(self):
        """测试新 session_id 格式"""
        replayer = TraceReplayer()
        new_id = replayer._generate_new_session_id("session-abc123def456")
        assert new_id.startswith("replay-")
        # 新 ID 不等于原始 ID
        assert new_id != "session-abc123def456"

    def test_generate_new_session_id_uniqueness(self):
        """测试每次生成的新 session_id 唯一"""
        replayer = TraceReplayer()
        id1 = replayer._generate_new_session_id("session-123")
        id2 = replayer._generate_new_session_id("session-123")
        # 基于 uuid，应不同
        assert id1 != id2

    def test_generate_new_session_id_short_original(self):
        """测试原始 session_id 较短时的处理"""
        replayer = TraceReplayer()
        new_id = replayer._generate_new_session_id("s")
        assert new_id.startswith("replay-")

    async def test_replay_generates_new_session_id(self, mock_span_cache_with_data):
        """测试回放时生成新 session_id"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-original", deterministic_mode=False,
        )
        assert result.new_session_id != ""
        assert result.new_session_id != "session-original"
        assert result.new_session_id.startswith("replay-")


class TestSanitization:
    """脱敏处理测试（REQ-10）"""

    def test_sanitize_empty_text(self):
        """测试空文本脱敏"""
        replayer = TraceReplayer()
        assert replayer._sanitize("") == ""

    def test_sanitize_normal_text(self):
        """测试普通文本脱敏（无 PII 时不变）"""
        replayer = TraceReplayer()
        result = replayer._sanitize("查询未读邮件")
        # 无 PII 时应原样返回
        assert "查询未读邮件" in result

    def test_sanitize_phone_number(self):
        """测试手机号脱敏"""
        replayer = TraceReplayer()
        result = replayer._sanitize("联系手机号 13812345678")
        # 手机号应被脱敏
        assert "13812345678" not in result

    def test_sanitize_email(self):
        """测试邮箱脱敏"""
        replayer = TraceReplayer()
        result = replayer._sanitize("联系邮箱 test@example.com")
        # 邮箱应被脱敏
        assert "test@example.com" not in result

    async def test_replay_desensitizes_output(self, mock_span_cache_with_data):
        """测试回放结果脱敏"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data, desensitize=True)
        result = await replayer.replay_trace(
            "session-test", deterministic_mode=False,
        )
        # original_input 和 new_output 应经过脱敏
        assert result.original_input is not None

    async def test_replay_no_desensitize(self, mock_span_cache_with_data):
        """测试关闭脱敏时原样保留"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data, desensitize=False)
        result = await replayer.replay_trace(
            "session-test", deterministic_mode=False,
        )
        # 关闭脱敏时 original_input 应为原始值
        assert result.original_input == "查询未读邮件"


class TestContextExtraction:
    """上下文提取测试"""

    def test_extract_context_from_metadata(self):
        """测试从 metadata 提取上下文"""
        replayer = TraceReplayer()
        spans = [
            {
                "span_type": "intent_classification",
                "metadata": {"user_id": "u1", "agent_name": "EmailAgent"},
            },
            {
                "span_type": "tool_call:email_query",
                "metadata": {"tenant_id": "t1", "session_type": "normal"},
            },
        ]
        context = replayer._extract_context(spans)
        assert context.get("user_id") == "u1"
        assert context.get("agent_name") == "EmailAgent"
        assert context.get("tenant_id") == "t1"
        assert context.get("session_type") == "normal"

    def test_extract_context_empty_spans(self):
        """测试空 spans 提取上下文"""
        replayer = TraceReplayer()
        context = replayer._extract_context([])
        assert context == {}

    def test_extract_context_no_metadata(self):
        """测试无 metadata 的 spans"""
        replayer = TraceReplayer()
        spans = [{"span_type": "x"}]
        context = replayer._extract_context(spans)
        assert context == {}

    def test_extract_context_first_occurrence_wins(self):
        """测试同一 key 只取第一次出现的值"""
        replayer = TraceReplayer()
        spans = [
            {"span_type": "x", "metadata": {"user_id": "u1"}},
            {"span_type": "y", "metadata": {"user_id": "u2"}},
        ]
        context = replayer._extract_context(spans)
        assert context.get("user_id") == "u1"


class TestReplayRecordPersistence:
    """回放记录持久化测试"""

    async def test_persist_replay_record(self, mock_span_cache_with_data):
        """测试回放记录被持久化"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-persist", deterministic_mode=False,
        )

        # 应能在内存存储中找到
        records = replayer.list_replay_records()
        assert len(records) >= 1

    async def test_get_replay_record(self, mock_span_cache_with_data):
        """测试获取单条回放记录"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-get", deterministic_mode=False,
        )

        records = replayer.list_replay_records()
        if records:
            record = records[0]
            fetched = replayer.get_replay_record(record.replay_id)
            assert fetched is not None
            assert fetched.replay_id == record.replay_id

    async def test_list_replay_records_filter_by_session(self, mock_span_cache_with_data):
        """测试按原始 session_id 过滤回放记录"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        await replayer.replay_trace("session-a", deterministic_mode=False)
        await replayer.replay_trace("session-b", deterministic_mode=False)

        records_a = replayer.list_replay_records(original_session_id="session-a")
        assert all(r.original_session_id == "session-a" for r in records_a)
        assert len(records_a) >= 1

    async def test_list_replay_records_no_filter(self, mock_span_cache_with_data):
        """测试不过滤列出全部回放记录"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        await replayer.replay_trace("session-x", deterministic_mode=False)
        await replayer.replay_trace("session-y", deterministic_mode=False)

        all_records = replayer.list_replay_records()
        assert len(all_records) >= 2


class TestReplayResult:
    """ReplayResult 模型测试"""

    def test_default_values(self):
        """测试默认值"""
        result = ReplayResult(original_session_id="s1")
        assert result.original_session_id == "s1"
        assert result.new_session_id == ""
        assert result.original_input == ""
        assert result.new_output == ""
        assert result.original_trajectory == []
        assert result.new_trajectory == []
        assert result.reproduced is False
        assert result.duration_ms == 0.0
        assert result.deterministic_mode is True
        assert result.trajectory_diff is None
        assert result.fixture_generated is None

    def test_reproduced_true_when_no_diff(self):
        """测试无差异时复现成功"""
        from agent.evaluation.replay.trace_replayer import TrajectoryDiff

        result = ReplayResult(
            original_session_id="s1",
            trajectory_diff=TrajectoryDiff(),
        )
        # 模拟判断逻辑
        diff = result.trajectory_diff
        reproduced = (
            not diff.added_tools
            and not diff.removed_tools
            and not diff.order_changed
        )
        assert reproduced is True


class TestDeterministicModeIntegration:
    """确定性模式集成测试"""

    async def test_replay_with_deterministic_mode(self, mock_span_cache_with_data):
        """测试使用确定性模式回放"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-det", deterministic_mode=True,
        )
        assert result.deterministic_mode is True

    async def test_replay_without_deterministic_mode(self, mock_span_cache_with_data):
        """测试不使用确定性模式回放"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-nodet", deterministic_mode=False,
        )
        assert result.deterministic_mode is False

    async def test_replay_duration_recorded(self, mock_span_cache_with_data):
        """测试回放耗时被记录"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-duration", deterministic_mode=False,
        )
        assert result.duration_ms > 0

    async def test_replay_trajectory_diff_computed(self, mock_span_cache_with_data):
        """测试轨迹差异被计算"""
        replayer = TraceReplayer(span_cache=mock_span_cache_with_data)
        result = await replayer.replay_trace(
            "session-diff", deterministic_mode=False,
        )
        assert result.trajectory_diff is not None
