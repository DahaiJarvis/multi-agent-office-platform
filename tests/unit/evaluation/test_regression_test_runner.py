"""RegressionTestRunner 回归测试执行器单元测试

覆盖 spec 04 第 3.5 节 F6 功能：
  - RegressionReport 模型
  - RegressionTestRunner 回归测试执行
  - baseline/current 评分对比
  - 通过率阈值判定
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.replay.models import RegressionReport
from agent.evaluation.replay.regression_test_runner import (
    RegressionTestRunner,
    DEFAULT_REGRESSION_PASS_THRESHOLD,
)
from agent.evaluation.runners.harness_runner import SingleEvalResult


@pytest.fixture
def sample_fixtures() -> list[Fixture]:
    """构造测试 Fixture 列表"""
    return [
        Fixture(
            fixture_id="reg-001",
            category="email",
            severity="normal",
            input="查询邮件",
        ),
        Fixture(
            fixture_id="reg-002",
            category="approval",
            severity="normal",
            input="查询审批",
        ),
    ]


class TestRegressionReport:
    """RegressionReport 模型测试"""

    def test_default_values(self):
        """测试默认值"""
        report = RegressionReport()
        assert report.report_id.startswith("regression-")
        assert report.fixture_ids == []
        assert report.baseline_scores == {}
        assert report.current_scores == {}
        assert report.pass_count == 0
        assert report.fail_count == 0
        assert report.status == ""
        assert report.details == []

    def test_custom_values(self):
        """测试自定义值"""
        report = RegressionReport(
            fixture_ids=["f1", "f2"],
            baseline_scores={"f1": 0.3, "f2": 0.4},
            current_scores={"f1": 0.8, "f2": 0.9},
            pass_count=2,
            fail_count=0,
            status="pass",
        )
        assert report.fixture_ids == ["f1", "f2"]
        assert report.pass_count == 2
        assert report.status == "pass"


class TestRegressionTestRunnerInit:
    """RegressionTestRunner 初始化测试"""

    def test_default_threshold(self):
        """测试默认通过阈值"""
        runner = RegressionTestRunner()
        assert runner._pass_threshold == DEFAULT_REGRESSION_PASS_THRESHOLD
        assert DEFAULT_REGRESSION_PASS_THRESHOLD == 0.95

    def test_custom_threshold(self):
        """测试自定义通过阈值"""
        runner = RegressionTestRunner(pass_threshold=0.8)
        assert runner._pass_threshold == 0.8


class TestRegressionTestRunnerRun:
    """RegressionTestRunner 执行测试"""

    async def test_run_regression_no_fixtures_loaded(self):
        """测试未加载到 fixture 时返回 fail"""
        runner = RegressionTestRunner()
        # Mock loader 返回空
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = []

        report = await runner.run_regression(["nonexistent-001"])
        assert report.status == "fail"
        assert len(report.details) == 1
        assert "error" in report.details[0]

    async def test_run_regression_runner_unavailable(self, sample_fixtures):
        """测试 HarnessRunner 不可用时返回 fail"""
        runner = RegressionTestRunner()
        # Mock loader 返回 fixture
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = sample_fixtures
        # Mock runner 初始化失败
        runner._harness_runner = None

        with patch.object(
            RegressionTestRunner, "_ensure_runner", return_value=None,
        ):
            report = await runner.run_regression(["reg-001"])
            assert report.status == "fail"

    async def test_run_regression_all_pass(self, sample_fixtures):
        """测试全部通过的回归测试"""
        # 构造 Mock runner，返回高分评估结果
        mock_runner = MagicMock()
        mock_runner.run_single = AsyncMock()

        # 为每个 fixture 构造成功的评估结果
        def make_result(fixture_id, score):
            result = MagicMock()
            result.success = True
            result.error = ""
            result.judge_result = MagicMock()
            result.judge_result.overall_score = score
            result.trajectory_result = None
            return result

        mock_runner.run_single.side_effect = [
            make_result("reg-001", 0.9),
            make_result("reg-002", 0.85),
        ]

        runner = RegressionTestRunner(harness_runner=mock_runner)
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = sample_fixtures

        report = await runner.run_regression(
            fixture_ids=["reg-001", "reg-002"],
            baseline_scores={"reg-001": 0.3, "reg-002": 0.4},
        )

        assert report.status == "pass"
        assert report.pass_count == 2
        assert report.fail_count == 0
        assert report.current_scores["reg-001"] == 0.9
        assert report.current_scores["reg-002"] == 0.85

    async def test_run_regression_some_fail(self, sample_fixtures):
        """测试部分失败的回归测试"""
        mock_runner = MagicMock()
        mock_runner.run_single = AsyncMock()

        def make_result(fixture_id, score, success=True):
            result = MagicMock()
            result.success = success
            result.error = ""
            result.judge_result = MagicMock()
            result.judge_result.overall_score = score
            result.trajectory_result = None
            return result

        # reg-001 改进（0.3 -> 0.8），reg-002 未改进（0.4 -> 0.3）
        mock_runner.run_single.side_effect = [
            make_result("reg-001", 0.8),
            make_result("reg-002", 0.3),
        ]

        runner = RegressionTestRunner(harness_runner=mock_runner)
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = sample_fixtures

        report = await runner.run_regression(
            fixture_ids=["reg-001", "reg-002"],
            baseline_scores={"reg-001": 0.3, "reg-002": 0.4},
        )

        # 通过率 50% < 95%，整体 fail
        assert report.status == "fail"
        assert report.pass_count == 1
        assert report.fail_count == 1

    async def test_run_regression_eval_exception(self, sample_fixtures):
        """测试评估异常时的处理"""
        mock_runner = MagicMock()
        mock_runner.run_single = AsyncMock(side_effect=RuntimeError("评估崩溃"))

        runner = RegressionTestRunner(harness_runner=mock_runner)
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = sample_fixtures

        report = await runner.run_regression(
            fixture_ids=["reg-001"],
            baseline_scores={"reg-001": 0.5},
        )

        # 异常 fixture 记为失败
        assert report.fail_count == 1
        assert report.current_scores["reg-001"] == 0.0
        detail = report.details[0]
        assert detail["regression_passed"] is False
        assert "评估崩溃" in detail["error"]

    async def test_run_regression_empty_fixture_ids(self):
        """测试空 fixture_ids 列表"""
        runner = RegressionTestRunner()
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = []

        report = await runner.run_regression([])
        assert report.status == "fail"

    async def test_run_regression_partial_fixtures_found(self, sample_fixtures):
        """测试部分 fixture 找到"""
        mock_runner = MagicMock()
        mock_runner.run_single = AsyncMock()

        def make_result(score):
            result = MagicMock()
            result.success = True
            result.error = ""
            result.judge_result = MagicMock()
            result.judge_result.overall_score = score
            result.trajectory_result = None
            return result

        mock_runner.run_single.return_value = make_result(0.9)

        runner = RegressionTestRunner(harness_runner=mock_runner)
        runner._dataset_loader = MagicMock()
        runner._dataset_loader.load_all.return_value = [sample_fixtures[0]]

        # 请求 reg-001（存在）和 nonexistent（不存在）
        report = await runner.run_regression(
            fixture_ids=["reg-001", "nonexistent"],
            baseline_scores={"reg-001": 0.3},
        )

        # 只评估了 reg-001，通过率 100%
        assert report.pass_count == 1
        assert report.fail_count == 0

    def test_extract_score_with_judge_result(self):
        """测试从 judge_result 提取评分"""
        runner = RegressionTestRunner()

        result = MagicMock()
        result.judge_result = MagicMock()
        result.judge_result.overall_score = 0.85
        result.success = True

        assert runner._extract_score(result) == 0.85

    def test_extract_score_no_judge_success(self):
        """测试无 judge_result 但 success 时返回 1.0"""
        runner = RegressionTestRunner()

        result = MagicMock()
        result.judge_result = None
        result.success = True
        result.trajectory_result = None

        assert runner._extract_score(result) == 1.0

    def test_extract_score_no_judge_trajectory_passed(self):
        """测试无 judge 但轨迹通过时返回 0.6"""
        runner = RegressionTestRunner()

        result = MagicMock()
        result.judge_result = None
        result.success = False
        result.trajectory_result = MagicMock()
        result.trajectory_result.passed = True

        assert runner._extract_score(result) == 0.6

    def test_extract_score_no_judge_trajectory_failed(self):
        """测试无 judge 且轨迹未通过时返回 0.3"""
        runner = RegressionTestRunner()

        result = MagicMock()
        result.judge_result = None
        result.success = False
        result.trajectory_result = MagicMock()
        result.trajectory_result.passed = False

        assert runner._extract_score(result) == 0.3

    def test_extract_score_all_none(self):
        """测试全 None 时返回 0.0"""
        runner = RegressionTestRunner()

        result = MagicMock()
        result.judge_result = None
        result.success = False
        result.trajectory_result = None

        assert runner._extract_score(result) == 0.0
