"""核心评估执行器

加载 Fixture，驱动被测 Agent 执行，收集轨迹，调用 LLMJudge 评分，输出 EvalReport。
对应 spec 文档 3.3 节与 4.5/4.7 节。

数据模型：
  - TokenUsage: token 用量
  - SingleEvalResult: 单次评估结果
  - EvalReport: 评估报告（CI 门禁输入）

执行器：
  - HarnessRunner: 评估执行器核心

执行流程：
  Fixture -> 构造 Agent 上下文 -> 驱动 Agent -> 收集输出+轨迹
    -> 轨迹评估 + LLM-as-judge + token 统计
    -> SingleEvalResult
    -> (k>1) PassKEvaluator -> PassKResult
    -> EvalReport
"""

import argparse
import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field, ConfigDict

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.fixtures.dataset_loader import DatasetLoader
from agent.evaluation.rubrics.rubric_schema import JudgeResult
from agent.evaluation.rubrics.llm_judge import LLMJudge
from agent.evaluation.runners.trajectory_eval import TrajectoryEvaluator, TrajectoryEvalResult

logger = logging.getLogger(__name__)


class TokenUsage(BaseModel):
    """token 用量

    Attributes:
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        total_tokens: 总 token 数
        estimated_cost: 估算成本（元）
    """

    model_config = ConfigDict(frozen=False)

    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    estimated_cost: float = Field(default=0.0, description="估算成本（元）")


class SingleEvalResult(BaseModel):
    """单次评估结果

    Attributes:
        fixture_id: fixture ID
        success: 是否成功（judge 通过 + 轨迹通过）
        agent_response: Agent 最终响应
        agent_trajectory: Agent 执行轨迹
        judge_result: LLM-as-judge 评分结果
        trajectory_result: 轨迹评估结果
        usage: 资源用量
        duration_ms: 执行耗时（毫秒）
        error: 执行错误（失败时）
    """

    model_config = ConfigDict(frozen=False)

    fixture_id: str = Field(..., description="fixture ID")
    success: bool = Field(..., description="是否成功（judge 通过 + 轨迹通过）")
    agent_response: str = Field(default="", description="Agent 最终响应")
    agent_trajectory: list[dict] = Field(
        default_factory=list,
        description="Agent 执行轨迹",
    )
    judge_result: JudgeResult | None = Field(
        default=None,
        description="LLM-as-judge 评分结果",
    )
    trajectory_result: TrajectoryEvalResult | None = Field(
        default=None,
        description="轨迹评估结果",
    )
    usage: TokenUsage = Field(
        default_factory=TokenUsage,
        description="资源用量",
    )
    duration_ms: int = Field(default=0, description="执行耗时（毫秒）")
    error: str = Field(default="", description="执行错误（失败时）")


class EvalReport(BaseModel):
    """评估报告

    CI 门禁判断的输入。

    Attributes:
        suite_name: 套件名称（fast/slow/replay）
        total_fixtures: fixture 总数
        pass_count: 通过数
        fail_count: 失败数
        pass_at_k_rate: pass@k 通过率（0-1）
        pass_caret_5_rate: pass^5 通过率（0-1），CI 门禁核心指标
        critical_safety_violations: critical 级别安全违规数
        safety_violations: 安全违规总数
        avg_cost: 平均成本（元）
        cost_variance_ratio: 成本方差比（0-1）
        total_duration_ms: 总耗时（毫秒）
        coverage_rate: 评估覆盖率（0-1）
        pass_k_results: 各 fixture 的 pass@k 结果
        failed_fixture_ids: 失败 fixture ID 列表
    """

    model_config = ConfigDict(frozen=False)

    suite_name: str = Field(..., description="套件名称（fast/slow/replay）")
    total_fixtures: int = Field(..., description="fixture 总数")
    pass_count: int = Field(..., description="通过数")
    fail_count: int = Field(..., description="失败数")

    pass_at_k_rate: float = Field(..., description="pass@k 通过率（0-1）")
    pass_caret_5_rate: float = Field(
        ...,
        description="pass^5 通过率（0-1），CI 门禁核心指标",
    )

    critical_safety_violations: int = Field(
        default=0,
        description="critical 级别安全违规数",
    )
    safety_violations: int = Field(
        default=0,
        description="安全违规总数",
    )

    avg_cost: float = Field(default=0.0, description="平均成本（元）")
    cost_variance_ratio: float = Field(
        default=0.0,
        description="成本方差比（0-1）",
    )

    total_duration_ms: int = Field(default=0, description="总耗时（毫秒）")
    coverage_rate: float = Field(
        default=0.0,
        description="评估覆盖率（已覆盖核心场景比例，0-1）",
    )

    pass_k_results: list[Any] = Field(
        default_factory=list,
        description="各 fixture 的 pass@k 结果（PassKResult 列表，使用 Any 避免循环导入）",
    )
    failed_fixture_ids: list[str] = Field(
        default_factory=list,
        description="失败 fixture ID 列表",
    )


# Agent 执行器类型：接收 fixture，返回 (response, trajectory, usage)
AgentExecutor = Callable[[Fixture], Awaitable[tuple[str, list[dict], TokenUsage]]]


class HarnessRunner:
    """评估执行器核心

    加载 Fixture，驱动被测 Agent 执行，收集轨迹，调用 LLMJudge 评分，输出 EvalReport。

    使用示例：
        runner = HarnessRunner(
            agent_factory=my_agent_factory,
            judge=LLMJudge(judge_model_tier="max"),
            deterministic=False,
        )
        report = await runner.run_suite(fixtures, k=5, pass_mode="pass^k")
        gate = CIGate()
        blocked, reason = gate.should_block(report)
    """

    def __init__(
        self,
        agent_factory: Callable[[], Any] | None = None,
        agent_executor: AgentExecutor | None = None,
        judge: LLMJudge | None = None,
        deterministic: bool = False,
    ) -> None:
        """初始化执行器

        Args:
            agent_factory: 每次评估创建新的 Agent 实例（避免状态污染）
            agent_executor: 自定义 Agent 执行器（优先于 agent_factory）
                            接收 fixture，返回 (response, trajectory, usage)
            judge: LLM-as-judge 实例，None 时使用默认 max tier
            deterministic: True 时启用确定性模式（固定 seed/temperature/Mock 工具）
        """
        self._agent_factory = agent_factory
        self._agent_executor = agent_executor
        self._judge = judge or LLMJudge(judge_model_tier="max")
        self._deterministic = deterministic
        self._trajectory_evaluator = TrajectoryEvaluator()

    async def run_single(self, fixture: Fixture) -> SingleEvalResult:
        """执行单个 Fixture 评估

        流程：
            1. 根据 fixture 构造 Agent 执行上下文（注入 context）
            2. 驱动 Agent 处理 fixture.input
            3. 收集 Agent 输出与执行轨迹（工具调用链）
            4. 轨迹评估（expected_tools/forbidden_tools 校验）
            5. LLM-as-judge 评分
            6. 汇总 SingleEvalResult

        Returns:
            单次评估结果，含 success/judge_result/trajectory_result/usage
        """
        start_time = time.time()
        fixture_id = fixture.fixture_id

        logger.info("开始评估 fixture: %s", fixture_id)

        try:
            # 1. 驱动 Agent 执行
            agent_response, agent_trajectory, usage = await self._execute_agent(fixture)

            # 2. 轨迹评估
            trajectory_result = self._trajectory_evaluator.evaluate(fixture, agent_trajectory)

            # 3. LLM-as-judge 评分
            judge_result = await self._judge.judge(
                fixture=fixture,
                agent_response=agent_response,
                agent_trajectory=agent_trajectory,
            )

            # 4. 汇总
            duration_ms = int((time.time() - start_time) * 1000)
            success = judge_result.passed and trajectory_result.passed

            result = SingleEvalResult(
                fixture_id=fixture_id,
                success=success,
                agent_response=agent_response,
                agent_trajectory=agent_trajectory,
                judge_result=judge_result,
                trajectory_result=trajectory_result,
                usage=usage,
                duration_ms=duration_ms,
            )

            logger.info(
                "评估完成 fixture=%s success=%s score=%.2f duration=%dms",
                fixture_id, success, judge_result.overall_score, duration_ms,
            )
            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("评估异常 fixture=%s: %s", fixture_id, e)
            return SingleEvalResult(
                fixture_id=fixture_id,
                success=False,
                duration_ms=duration_ms,
                error=str(e),
            )

    async def run_suite(
        self,
        fixtures: list[Fixture],
        k: int = 1,
        pass_mode: str = "pass@k",
    ) -> EvalReport:
        """执行评估套件

        Args:
            fixtures: Fixture 列表
            k: 每个 fixture 重复执行次数（pass@k 评估）
            pass_mode: 一致性模式 "pass@k" | "pass^k"

        Returns:
            评估报告 EvalReport
        """
        from agent.evaluation.runners.pass_k import PassKEvaluator, PassKResult

        suite_start = time.time()
        total = len(fixtures)

        logger.info("开始评估套件: %d 个 fixture, k=%d, mode=%s", total, k, pass_mode)

        pass_k_evaluator = PassKEvaluator(self)
        pass_k_results: list[PassKResult] = []
        failed_fixture_ids: list[str] = []

        for idx, fixture in enumerate(fixtures, 1):
            logger.info("[%d/%d] 评估 %s", idx, total, fixture.fixture_id)

            if k > 1:
                pk_result = await pass_k_evaluator.evaluate(
                    fixture=fixture,
                    k=k,
                    pass_mode=pass_mode,
                )
            else:
                # k=1 时简化执行
                single_result = await self.run_single(fixture)
                pk_result = PassKResult(
                    fixture_id=fixture.fixture_id,
                    k=1,
                    pass_mode=pass_mode,
                    pass_at_k=single_result.success,
                    pass_caret_k=single_result.success,
                    success_count=1 if single_result.success else 0,
                    success_rate=1.0 if single_result.success else 0.0,
                    results=[single_result],
                )

            pass_k_results.append(pk_result)
            if not pk_result.pass_caret_k:
                failed_fixture_ids.append(fixture.fixture_id)

        # 汇总统计
        pass_count = sum(1 for r in pass_k_results if r.pass_caret_k)
        fail_count = total - pass_count
        pass_at_k_rate = sum(1 for r in pass_k_results if r.pass_at_k) / total if total else 0.0
        pass_caret_5_rate = pass_count / total if total else 0.0

        # 安全违规统计
        safety_violations = 0
        critical_safety_violations = 0
        for pk_result in pass_k_results:
            for single_result in pk_result.results:
                if single_result.judge_result and single_result.judge_result.safety_violations:
                    safety_violations += len(single_result.judge_result.safety_violations)
                    # adversarial 场景的安全违规视为 critical
                    if single_result.judge_result.fixture_id.startswith("adversarial"):
                        critical_safety_violations += len(single_result.judge_result.safety_violations)

        # 成本统计
        all_costs = []
        for pk_result in pass_k_results:
            for single_result in pk_result.results:
                all_costs.append(single_result.usage.estimated_cost)
        avg_cost = sum(all_costs) / len(all_costs) if all_costs else 0.0

        # 成本方差
        cost_variance_ratio = 0.0
        if len(all_costs) > 1 and avg_cost > 0:
            mean = avg_cost
            variance = sum((c - mean) ** 2 for c in all_costs) / len(all_costs)
            cost_variance_ratio = (variance ** 0.5) / mean if mean > 0 else 0.0

        total_duration_ms = int((time.time() - suite_start) * 1000)

        report = EvalReport(
            suite_name="custom",
            total_fixtures=total,
            pass_count=pass_count,
            fail_count=fail_count,
            pass_at_k_rate=pass_at_k_rate,
            pass_caret_5_rate=pass_caret_5_rate,
            critical_safety_violations=critical_safety_violations,
            safety_violations=safety_violations,
            avg_cost=avg_cost,
            cost_variance_ratio=cost_variance_ratio,
            total_duration_ms=total_duration_ms,
            coverage_rate=1.0,  # 套件内覆盖率默认 100%
            pass_k_results=pass_k_results,
            failed_fixture_ids=failed_fixture_ids,
        )

        logger.info(
            "评估套件完成: pass=%d fail=%d pass^5_rate=%.2f%% duration=%dms",
            pass_count, fail_count, pass_caret_5_rate, total_duration_ms,
        )
        return report

    async def _execute_agent(self, fixture: Fixture) -> tuple[str, list[dict], TokenUsage]:
        """驱动 Agent 执行

        优先使用 agent_executor（自定义执行器），
        其次使用 agent_factory 创建 Agent 并执行。

        Args:
            fixture: 测试 Fixture

        Returns:
            (agent_response, agent_trajectory, token_usage)
        """
        if self._agent_executor is not None:
            return await self._agent_executor(fixture)

        if self._agent_factory is not None:
            agent = self._agent_factory()
            # 调用 Agent 处理 fixture.input（具体调用方式取决于 Agent 接口）
            # 这里简化实现：直接调用 Agent 的 run 方法
            response = ""
            trajectory: list[dict] = []
            usage = TokenUsage()

            if hasattr(agent, "run"):
                result = await agent.run(fixture.input)
                if isinstance(result, str):
                    response = result
                elif isinstance(result, dict):
                    response = result.get("response", str(result))
                    trajectory = result.get("trajectory", [])
                elif isinstance(result, tuple):
                    response = result[0] if len(result) > 0 else ""
                    trajectory = result[1] if len(result) > 1 else []
            elif hasattr(agent, "handle"):
                response = await agent.handle(fixture.input)
            else:
                response = str(agent)

            return response, trajectory, usage

        # 无 Agent 工厂/执行器时，返回空结果（用于测试）
        logger.warning("未配置 agent_factory 或 agent_executor，返回空响应")
        return "", [], TokenUsage()


def _run_suite_cli(
    suite: str = "fast",
    k: int = 5,
    pass_mode: str = "pass^k",
    output: str | None = None,
    fixture_id: str | None = None,
) -> None:
    """命令行入口：运行评估套件

    对应 spec 文档 8.3 节的运行命令。
    """
    from agent.evaluation.canaries.canary_manager import CanaryManager

    canary_manager = CanaryManager()

    if fixture_id:
        # 单 fixture 评估
        loader = DatasetLoader()
        all_fixtures = loader.load_all()
        fixtures = [f for f in all_fixtures if f.fixture_id == fixture_id]
        if not fixtures:
            logger.error("Fixture 未找到: %s", fixture_id)
            return
        k = 1
    elif suite == "fast":
        fixtures = canary_manager.get_fast_suite()
    elif suite == "slow":
        fixtures = canary_manager.get_slow_suite()
    else:
        logger.error("未知套件: %s（可选 fast/slow）", suite)
        return

    if not fixtures:
        logger.error("套件 %s 无 fixture", suite)
        return

    runner = HarnessRunner(deterministic=False)
    report = asyncio.run(runner.run_suite(fixtures, k=k, pass_mode=pass_mode))

    # 输出报告
    print(f"\n评估套件: {suite}")
    print(f"总数: {report.total_fixtures}")
    print(f"通过: {report.pass_count}")
    print(f"失败: {report.fail_count}")
    print(f"pass^5 通过率: {report.pass_caret_5_rate:.2%}")
    print(f"安全违规: {report.safety_violations}（critical: {report.critical_safety_violations}）")
    print(f"耗时: {report.total_duration_ms}ms")

    if report.failed_fixture_ids:
        print(f"失败 fixture: {', '.join(report.failed_fixture_ids)}")

    if output:
        report_data = report.model_dump()
        with open(output, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        print(f"报告已保存: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent 评估套件运行器")
    parser.add_argument("--suite", default="fast", choices=["fast", "slow"], help="评估套件")
    parser.add_argument("--k", type=int, default=5, help="pass@k 重复次数")
    parser.add_argument("--pass-mode", default="pass^k", choices=["pass@k", "pass^k"], help="一致性模式")
    parser.add_argument("--output", default=None, help="报告输出文件路径")
    parser.add_argument("--fixture-id", default=None, help="单个 fixture ID（调试用）")

    args = parser.parse_args()
    _run_suite_cli(
        suite=args.suite,
        k=args.k,
        pass_mode=args.pass_mode,
        output=args.output,
        fixture_id=args.fixture_id,
    )
