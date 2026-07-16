"""EvalScheduler 失败 Trace 识别与调度单元测试

覆盖 spec 04 第 3.1 节 F1 功能：
  - FailureFilter 筛选条件模型
  - FailedSession 失败 session 模型
  - EvalScheduler 扫描失败 session 与处理流程
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.evaluation.replay.eval_scheduler import (
    EvalScheduler,
    FailureFilter,
    FailedSession,
)


class TestFailureFilter:
    """FailureFilter 筛选条件模型测试"""

    def test_default_values(self):
        """测试默认值"""
        filt = FailureFilter()
        assert filt.status == "failed"
        assert filt.duration_p95_multiplier == 2.0
        assert filt.include_thumbs_down is True
        assert filt.since_hours == 24
        assert filt.agent_name is None
        assert filt.exclude_archived is True
        assert filt.max_batch_size == 50

    def test_custom_values(self):
        """测试自定义值"""
        filt = FailureFilter(
            status="error",
            duration_p95_multiplier=3.0,
            include_thumbs_down=False,
            since_hours=48,
            agent_name="EmailAgent",
            exclude_archived=False,
            max_batch_size=100,
        )
        assert filt.status == "error"
        assert filt.duration_p95_multiplier == 3.0
        assert filt.include_thumbs_down is False
        assert filt.since_hours == 48
        assert filt.agent_name == "EmailAgent"
        assert filt.exclude_archived is False
        assert filt.max_batch_size == 100

    def test_model_not_frozen(self):
        """测试模型可修改（frozen=False）"""
        filt = FailureFilter()
        filt.status = "timeout"
        assert filt.status == "timeout"


class TestFailedSession:
    """FailedSession 失败 session 模型测试"""

    def test_required_fields(self):
        """测试必填字段"""
        session = FailedSession(session_id="session-123")
        assert session.session_id == "session-123"
        assert session.agent_name == ""
        assert session.failure_reason == "failed"
        assert session.has_thumbs_down is False

    def test_all_fields(self):
        """测试所有字段"""
        session = FailedSession(
            session_id="session-456",
            agent_name="EmailAgent",
            failure_reason="thumbs_down",
            failure_detail="用户点踩",
            duration_ms=5000.0,
            has_thumbs_down=True,
        )
        assert session.session_id == "session-456"
        assert session.agent_name == "EmailAgent"
        assert session.failure_reason == "thumbs_down"
        assert session.failure_detail == "用户点踩"
        assert session.duration_ms == 5000.0
        assert session.has_thumbs_down is True
        # detected_at 应自动生成
        assert session.detected_at is not None


class TestEvalSchedulerScan:
    """EvalScheduler 扫描失败 session 测试"""

    @pytest.fixture
    def mock_span_cache(self):
        """构造 Mock SpanCache"""
        cache = MagicMock()
        cache.get_failed_sessions = AsyncMock(return_value=[])
        cache.get_session_spans = AsyncMock(return_value=[])
        return cache

    @pytest.fixture
    def mock_feedback_service(self):
        """构造 Mock FeedbackService"""
        service = MagicMock()
        service.get_thumbs_down_sessions = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def scheduler(self, mock_span_cache, mock_feedback_service):
        """构造带 Mock 的 EvalScheduler"""
        return EvalScheduler(
            span_cache=mock_span_cache,
            feedback_service=mock_feedback_service,
        )

    async def test_scan_no_data_sources(self):
        """测试无数据源时扫描返回空列表"""
        scheduler = EvalScheduler()
        result = await scheduler.scan_failed_sessions()
        assert result == []

    async def test_scan_from_span_cache(self, scheduler, mock_span_cache):
        """测试从 SpanCache 扫描失败 session"""
        mock_span_cache.get_failed_sessions.return_value = [
            {"session_id": "s1", "agent_name": "EmailAgent", "duration_ms": 3000},
            {"session_id": "s2", "agent_name": "CalendarAgent", "duration_ms": 5000},
        ]

        result = await scheduler.scan_failed_sessions()
        assert len(result) == 2
        assert result[0].session_id == "s1"
        assert result[0].failure_reason == "failed"
        assert result[0].agent_name == "EmailAgent"
        assert result[0].duration_ms == 3000

    async def test_scan_from_feedback(self, scheduler, mock_feedback_service):
        """测试从 FeedbackService 扫描点踩 session"""
        mock_feedback_service.get_thumbs_down_sessions.return_value = [
            {"session_id": "s3", "agent_name": "ApprovalAgent", "comment": "回答错误"},
        ]

        result = await scheduler.scan_failed_sessions()
        assert len(result) == 1
        assert result[0].session_id == "s3"
        assert result[0].failure_reason == "thumbs_down"
        assert result[0].has_thumbs_down is True

    async def test_scan_dedup_across_sources(self, scheduler, mock_span_cache, mock_feedback_service):
        """测试跨数据源去重"""
        mock_span_cache.get_failed_sessions.return_value = [
            {"session_id": "dup-1", "agent_name": "AgentA"},
        ]
        mock_feedback_service.get_thumbs_down_sessions.return_value = [
            {"session_id": "dup-1", "agent_name": "AgentA", "comment": "点踩"},
            {"session_id": "unique-1", "agent_name": "AgentB"},
        ]

        result = await scheduler.scan_failed_sessions()
        # dup-1 应只出现一次
        session_ids = [r.session_id for r in result]
        assert session_ids.count("dup-1") == 1
        assert "unique-1" in session_ids
        assert len(result) == 2

    async def test_scan_exclude_archived(self, scheduler, mock_span_cache):
        """测试排除已归档 session"""
        mock_span_cache.get_failed_sessions.return_value = [
            {"session_id": "archived-1", "agent_name": "AgentA"},
            {"session_id": "new-1", "agent_name": "AgentB"},
        ]

        # 标记 archived-1 为已处理
        scheduler._mark_processed("archived-1")

        result = await scheduler.scan_failed_sessions()
        session_ids = [r.session_id for r in result]
        assert "archived-1" not in session_ids
        assert "new-1" in session_ids

    async def test_scan_max_batch_size(self, scheduler, mock_span_cache):
        """测试批量大小限制"""
        mock_span_cache.get_failed_sessions.return_value = [
            {"session_id": f"s{i}", "agent_name": "Agent"} for i in range(10)
        ]

        filt = FailureFilter(max_batch_size=3)
        result = await scheduler.scan_failed_sessions(filt)
        assert len(result) <= 3

    async def test_scan_span_cache_exception(self, scheduler, mock_span_cache):
        """测试 SpanCache 异常时降级"""
        mock_span_cache.get_failed_sessions.side_effect = RuntimeError("连接失败")

        result = await scheduler.scan_failed_sessions()
        # 异常时应返回空列表，不抛出
        assert result == []

    async def test_scan_filter_agent_name(self, scheduler, mock_span_cache):
        """测试按 agent_name 过滤"""
        mock_span_cache.get_failed_sessions.return_value = [
            {"session_id": "s1", "agent_name": "EmailAgent"},
        ]

        filt = FailureFilter(agent_name="EmailAgent")
        result = await scheduler.scan_failed_sessions(filt)
        assert len(result) == 1
        # 验证 agent_name 参数被传递
        mock_span_cache.get_failed_sessions.assert_called_once()
        call_kwargs = mock_span_cache.get_failed_sessions.call_args
        assert call_kwargs.kwargs.get("agent_name") == "EmailAgent"


class TestEvalSchedulerProcess:
    """EvalScheduler 处理失败 session 测试"""

    @pytest.fixture
    def mock_converter(self):
        """构造 Mock TraceToFixtureConverter"""
        converter = MagicMock()
        converter.convert = AsyncMock(return_value=None)
        return converter

    @pytest.fixture
    def mock_replayer(self):
        """构造 Mock TraceReplayer"""
        replayer = MagicMock()
        replayer.replay_trace = AsyncMock(return_value=None)
        return replayer

    @pytest.fixture
    def mock_runner(self):
        """构造 Mock HarnessRunner"""
        runner = MagicMock()
        runner.run_single = AsyncMock(return_value=None)
        return runner

    @pytest.fixture
    def mock_archive(self):
        """构造 Mock FailureArchive"""
        archive = MagicMock()
        archive.archive = AsyncMock(return_value="archive-123")
        return archive

    @pytest.fixture
    def mock_span_cache(self):
        """构造 Mock SpanCache"""
        cache = MagicMock()
        cache.get_session_spans = AsyncMock(return_value=[])
        return cache

    @pytest.fixture
    def scheduler_with_mocks(
        self, mock_converter, mock_replayer, mock_runner, mock_archive, mock_span_cache,
    ):
        """构造带全部 Mock 的 EvalScheduler"""
        return EvalScheduler(
            span_cache=mock_span_cache,
            converter=mock_converter,
            replayer=mock_replayer,
            harness_runner=mock_runner,
            failure_archive=mock_archive,
        )

    async def test_process_convert_failure(self, scheduler_with_mocks, mock_converter):
        """测试 Fixture 生成失败时返回空字符串"""
        mock_converter.convert.return_value = None

        failed = FailedSession(session_id="s1")
        result = await scheduler_with_mocks.process_failed_session(failed)
        assert result == ""
        assert scheduler_with_mocks.is_processed("s1")

    async def test_process_eval_failed_triggers_archive(
        self, scheduler_with_mocks, mock_converter, mock_replayer,
        mock_runner, mock_archive,
    ):
        """测试评估失败时触发归档"""
        from agent.evaluation.fixtures.fixture_schema import Fixture
        from agent.evaluation.runners.harness_runner import SingleEvalResult

        fixture = Fixture(fixture_id="f1", input="test")
        mock_converter.convert.return_value = fixture

        # 评估结果为失败
        eval_result = SingleEvalResult(fixture_id="f1", success=False)
        mock_runner.run_single.return_value = eval_result

        failed = FailedSession(session_id="s1")
        result = await scheduler_with_mocks.process_failed_session(failed)

        # 应调用归档
        mock_archive.archive.assert_called_once()
        assert scheduler_with_mocks.is_processed("s1")

    async def test_process_eval_passed_no_archive(
        self, scheduler_with_mocks, mock_converter, mock_replayer,
        mock_runner, mock_archive,
    ):
        """测试评估通过时不归档"""
        from agent.evaluation.fixtures.fixture_schema import Fixture
        from agent.evaluation.runners.harness_runner import SingleEvalResult

        fixture = Fixture(fixture_id="f1", input="test")
        mock_converter.convert.return_value = fixture

        eval_result = SingleEvalResult(fixture_id="f1", success=True)
        mock_runner.run_single.return_value = eval_result

        failed = FailedSession(session_id="s1")
        await scheduler_with_mocks.process_failed_session(failed)

        # 不应调用归档
        mock_archive.archive.assert_not_called()

    async def test_process_exception_marks_processed(self, scheduler_with_mocks, mock_converter):
        """测试处理异常时仍标记已处理"""
        mock_converter.convert.side_effect = RuntimeError("转换异常")

        failed = FailedSession(session_id="s1")
        result = await scheduler_with_mocks.process_failed_session(failed)
        assert result == ""
        assert scheduler_with_mocks.is_processed("s1")

    async def test_run_scheduled_no_failed_sessions(self):
        """测试无失败 session 时定时任务正常完成"""
        scheduler = EvalScheduler()
        # 无数据源，扫描返回空
        await scheduler.run_scheduled()

    async def test_run_scheduled_with_failures(
        self, mock_converter,
    ):
        """测试有失败 session 时定时任务执行完整流程"""
        from unittest.mock import AsyncMock
        from agent.evaluation.fixtures.fixture_schema import Fixture

        mock_converter.convert.return_value = Fixture(fixture_id="f1", input="test")

        span_cache = MagicMock()
        span_cache.get_failed_sessions = AsyncMock(return_value=[
            {"session_id": "s1", "agent_name": "Agent"},
        ])

        scheduler = EvalScheduler(
            span_cache=span_cache,
            converter=mock_converter,
        )
        await scheduler.run_scheduled()
        assert scheduler.is_processed("s1")

    def test_extract_report_id_from_object(self):
        """测试从对象提取 report_id"""
        scheduler = EvalScheduler()

        class ObjWithReportId:
            report_id = "report-123"

        assert scheduler._extract_report_id(ObjWithReportId()) == "report-123"

    def test_extract_report_id_from_dict(self):
        """测试从字典提取 report_id"""
        scheduler = EvalScheduler()
        assert scheduler._extract_report_id({"report_id": "r1"}) == "r1"

    def test_extract_report_id_none(self):
        """测试从 None 提取 report_id"""
        scheduler = EvalScheduler()
        assert scheduler._extract_report_id(None) == ""

    def test_is_eval_failed_none_result(self):
        """测试 None 评估结果视为失败"""
        scheduler = EvalScheduler()
        assert scheduler._is_eval_failed(None) is True

    def test_is_eval_failed_success_false(self):
        """测试 success=False 视为失败"""
        scheduler = EvalScheduler()

        class Result:
            success = False

        assert scheduler._is_eval_failed(Result()) is True

    def test_is_eval_failed_success_true(self):
        """测试 success=True 视为通过"""
        scheduler = EvalScheduler()

        class Result:
            success = True

        assert scheduler._is_eval_failed(Result()) is False

    def test_is_eval_failed_status_fail(self):
        """测试 status=fail 视为失败"""
        scheduler = EvalScheduler()

        class Result:
            status = "fail"

        assert scheduler._is_eval_failed(Result()) is True
