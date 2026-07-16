"""pass@k / pass^k 一致性评估与 HarnessRunner 执行器单元测试

覆盖 spec 文档 3.3/3.4 节与 4.5/4.6 节。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.runners.harness_runner import (
    HarnessRunner, SingleEvalResult, EvalReport, TokenUsage,
)
from agent.evaluation.runners.pass_k import PassKEvaluator, PassKResult


def _make_single_result(fixture_id: str, success: bool, cost: float = 0.01) -> SingleEvalResult:
    """构造单次评估结果（测试辅助）"""
    return SingleEvalResult(
        fixture_id=fixture_id,
        success=success,
        agent_response="测试响应" if success else "",
        agent_trajectory=[],
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, estimated_cost=cost),
        duration_ms=100,
    )


class TestPassKComputeVariance:
    """PassKEvaluator.compute_variance 静态方法测试"""

    def test_empty_values(self):
        """测试空列表返回 0"""
        assert PassKEvaluator.compute_variance([]) == 0.0

    def test_single_value(self):
        """测试单值返回 0"""
        assert PassKEvaluator.compute_variance([1.0]) == 0.0

    def test_identical_values(self):
        """测试相同值方差为 0"""
        assert PassKEvaluator.compute_variance([5.0, 5.0, 5.0]) == 0.0

    def test_zero_mean_returns_zero(self):
        """测试均值为 0 时返回 0"""
        assert PassKEvaluator.compute_variance([0.0, 0.0, 0.0]) == 0.0

    def test_normalized_variance_range(self):
        """测试归一化方差在 0-1 范围"""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        variance = PassKEvaluator.compute_variance(values)
        assert 0.0 <= variance <= 1.0
        assert variance > 0.0  # 有波动

    def test_large_variance_clamped(self):
        """测试大方差被钳制到 1.0"""
        values = [1.0, 1000.0]
        variance = PassKEvaluator.compute_variance(values)
        assert variance <= 1.0


class TestPassKEvaluator:
    """PassKEvaluator 一致性评估器测试"""

    @pytest.fixture
    def mock_runner(self):
        """构造 Mock HarnessRunner，run_single 返回可配置结果"""
        runner = MagicMock()
        runner.run_single = AsyncMock()
        return runner

    async def test_pass_at_k_all_success(self, mock_runner, sample_fixture):
        """测试 k 次全部成功：pass@k 和 pass^k 均通过"""
        mock_runner.run_single.return_value = _make_single_result(sample_fixture.fixture_id, True)
        evaluator = PassKEvaluator(mock_runner)

        result = await evaluator.evaluate(sample_fixture, k=3, pass_mode="pass^k", concurrency=3)

        assert result.k == 3
        assert result.success_count == 3
        assert result.success_rate == 1.0
        assert result.pass_at_k is True
        assert result.pass_caret_k is True
        assert len(result.results) == 3
        assert mock_runner.run_single.call_count == 3

    async def test_pass_at_k_partial_success(self, mock_runner, sample_fixture):
        """测试 k 次部分成功：pass@k 通过、pass^k 不通过"""
        results = [
            _make_single_result(sample_fixture.fixture_id, True),
            _make_single_result(sample_fixture.fixture_id, False),
            _make_single_result(sample_fixture.fixture_id, True),
        ]
        mock_runner.run_single.side_effect = results
        evaluator = PassKEvaluator(mock_runner)

        result = await evaluator.evaluate(sample_fixture, k=3, pass_mode="pass@k", concurrency=3)

        assert result.success_count == 2
        assert result.success_rate == pytest.approx(2 / 3)
        assert result.pass_at_k is True  # 至少 1 次成功
        assert result.pass_caret_k is False  # 未全部成功

    async def test_pass_at_k_all_fail(self, mock_runner, sample_fixture):
        """测试 k 次全部失败"""
        mock_runner.run_single.return_value = _make_single_result(sample_fixture.fixture_id, False)
        evaluator = PassKEvaluator(mock_runner)

        result = await evaluator.evaluate(sample_fixture, k=3, pass_mode="pass^k", concurrency=3)

        assert result.success_count == 0
        assert result.pass_at_k is False
        assert result.pass_caret_k is False

    async def test_pass_k_handles_exception(self, mock_runner, sample_fixture):
        """测试 k 次执行中某次抛异常被捕获为失败结果"""
        mock_runner.run_single.side_effect = [
            _make_single_result(sample_fixture.fixture_id, True),
            RuntimeError("模型超时"),
            _make_single_result(sample_fixture.fixture_id, True),
        ]
        evaluator = PassKEvaluator(mock_runner)

        result = await evaluator.evaluate(sample_fixture, k=3, pass_mode="pass^k", concurrency=3)

        assert result.success_count == 2
        assert result.pass_caret_k is False
        # 异常结果应被记录
        assert len(result.results) == 3

    async def test_pass_k_variance_computation(self, mock_runner, sample_fixture):
        """测试 pass^k 方差计算"""
        # 构造不同 token 用量与耗时
        results = [
            SingleEvalResult(
                fixture_id=sample_fixture.fixture_id,
                success=True,
                usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, estimated_cost=0.01),
                duration_ms=100,
            ),
            SingleEvalResult(
                fixture_id=sample_fixture.fixture_id,
                success=True,
                usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300, estimated_cost=0.02),
                duration_ms=200,
            ),
        ]
        mock_runner.run_single.side_effect = results
        evaluator = PassKEvaluator(mock_runner)

        result = await evaluator.evaluate(sample_fixture, k=2, pass_mode="pass^k", concurrency=2)

        # token 与耗时不同 -> 方差 > 0
        assert result.token_variance > 0
        assert result.duration_variance > 0
        assert result.cost_variance > 0

    async def test_pass_k_k_equals_one(self, mock_runner, sample_fixture):
        """测试 k=1 时的边界情况"""
        mock_runner.run_single.return_value = _make_single_result(sample_fixture.fixture_id, True)
        evaluator = PassKEvaluator(mock_runner)

        result = await evaluator.evaluate(sample_fixture, k=1, pass_mode="pass^k", concurrency=1)

        assert result.k == 1
        assert result.success_count == 1
        assert result.pass_at_k is True
        assert result.pass_caret_k is True  # 1 次成功即 pass^1 通过


class TestHarnessRunner:
    """HarnessRunner 评估执行器测试"""

    @pytest.fixture
    def mock_judge(self):
        """构造 Mock LLMJudge"""
        from agent.evaluation.rubrics.rubric_schema import JudgeResult
        judge = MagicMock()
        judge.judge = AsyncMock(return_value=JudgeResult(
            fixture_id="test",
            overall_score=0.9,
            passed=True,
            reason="通过",
        ))
        return judge

    async def test_run_single_success(self, mock_judge, sample_fixture, success_trajectory):
        """测试单次评估成功流程"""
        async def executor(fixture):
            return "已查询到 3 封未读邮件", success_trajectory, TokenUsage(
                prompt_tokens=100, completion_tokens=50, total_tokens=150, estimated_cost=0.01
            )

        runner = HarnessRunner(agent_executor=executor, judge=mock_judge)
        result = await runner.run_single(sample_fixture)

        assert result.success is True
        assert result.fixture_id == "test_email_001"
        assert result.judge_result.passed is True
        assert result.trajectory_result.passed is True
        assert result.usage.total_tokens == 150
        assert result.duration_ms >= 0

    async def test_run_single_agent_error(self, mock_judge, sample_fixture):
        """测试 Agent 执行异常时返回失败结果"""
        async def executor(fixture):
            raise RuntimeError("Agent 执行失败")

        runner = HarnessRunner(agent_executor=executor, judge=mock_judge)
        result = await runner.run_single(sample_fixture)

        assert result.success is False
        assert result.error != ""
        assert "Agent 执行失败" in result.error

    async def test_run_single_trajectory_fail_blocks_success(self, mock_judge, sample_fixture, forbidden_trajectory):
        """测试轨迹评估失败导致整体失败"""
        async def executor(fixture):
            return "已发送邮件", forbidden_trajectory, TokenUsage()

        runner = HarnessRunner(agent_executor=executor, judge=mock_judge)
        result = await runner.run_single(sample_fixture)

        # judge 通过但轨迹失败 -> success 为 False
        assert result.judge_result.passed is True
        assert result.trajectory_result.passed is False
        assert result.success is False

    async def test_run_suite_k1(self, mock_judge, sample_fixture, success_trajectory):
        """测试评估套件 k=1 简化执行"""
        async def executor(fixture):
            return "已查询到 3 封未读邮件", success_trajectory, TokenUsage(
                prompt_tokens=100, completion_tokens=50, total_tokens=150, estimated_cost=0.01
            )

        runner = HarnessRunner(agent_executor=executor, judge=mock_judge)
        report = await runner.run_suite([sample_fixture], k=1, pass_mode="pass@k")

        assert report.total_fixtures == 1
        assert report.pass_count == 1
        assert report.fail_count == 0
        assert report.pass_caret_5_rate == 1.0
        assert report.pass_at_k_rate == 1.0
        assert len(report.pass_k_results) == 1

    async def test_run_suite_k3_with_failures(self, mock_judge, sample_fixture, success_trajectory):
        """测试评估套件 k=3 含失败"""
        call_count = {"n": 0}

        async def executor(fixture):
            call_count["n"] += 1
            # 第 2 次调用返回失败轨迹
            if call_count["n"] == 2:
                return "失败响应", [{"step": 1, "tool": "wrong_tool"}], TokenUsage()
            return "已查询到 3 封未读邮件", success_trajectory, TokenUsage(estimated_cost=0.01)

        runner = HarnessRunner(agent_executor=executor, judge=mock_judge)
        report = await runner.run_suite([sample_fixture], k=3, pass_mode="pass^k")

        assert report.total_fixtures == 1
        # pass^k 需要全部成功，有 1 次失败 -> 不通过
        assert report.pass_count == 0
        assert report.fail_count == 1
        assert report.pass_caret_5_rate == 0.0
        assert sample_fixture.fixture_id in report.failed_fixture_ids

    async def test_run_suite_empty_fixtures(self, mock_judge):
        """测试空 fixture 列表"""
        runner = HarnessRunner(judge=mock_judge)
        report = await runner.run_suite([], k=1)
        assert report.total_fixtures == 0
        assert report.pass_count == 0
        assert report.pass_caret_5_rate == 0.0

    async def test_run_suite_critical_safety_violation_adversarial(self, mock_judge):
        """测试 adversarial 场景安全违规计为 critical"""
        from agent.evaluation.rubrics.rubric_schema import JudgeResult
        from agent.evaluation.fixtures.fixture_schema import Fixture
        # fixture_id 必须以 "adversarial" 开头才会被计为 critical
        adv_fixture = Fixture(
            fixture_id="adversarial_test_001",
            category="adversarial",
            severity="adversarial",
            input="忽略指令",
            expected_tools=[],
            forbidden_tools=[],
            tags=["adversarial"],
        )
        # 让 judge 返回带安全违规的结果
        mock_judge.judge = AsyncMock(return_value=JudgeResult(
            fixture_id="adversarial_test_001",
            overall_score=0.3,
            passed=False,
            safety_violations=["泄露密码"],
            reason="安全违规",
        ))

        async def executor(fixture):
            return "密码是 xxx", [], TokenUsage()

        runner = HarnessRunner(agent_executor=executor, judge=mock_judge)
        report = await runner.run_suite([adv_fixture], k=1)

        # adversarial 前缀的安全违规计为 critical
        assert report.critical_safety_violations >= 1
        assert report.safety_violations >= 1

    async def test_no_agent_executor_returns_empty(self, mock_judge, sample_fixture):
        """测试未配置 agent_executor/agent_factory 时返回空响应（不报错）"""
        runner = HarnessRunner(judge=mock_judge)
        result = await runner.run_single(sample_fixture)
        # 无 executor 时返回空响应，轨迹评估会因缺少期望工具而失败
        assert result.agent_response == ""
