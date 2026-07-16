"""回归测试执行器

对应 spec 04 第 3.5 节 RegressionTestRunner 与 F6 功能。

改进上线后，重新执行原失败 Fixture，确认修复且未引入新问题。

核心流程：
  1. 加载原失败 Fixture（含 baseline 评分）
  2. 使用当前 Harness 执行 pass^k 评估
  3. 对比 baseline 与 current 评分
  4. 输出 pass/fail 回归报告

回归判定规则：
  - 改进后评分 > baseline 评分：该 fixture 回归通过
  - 改进后评分 <= baseline 评分：该 fixture 回归失败
  - 整体回归通过率 >= 阈值（默认 95%）：回归通过
"""

import logging
from typing import Any

from agent.evaluation.fixtures.dataset_loader import DatasetLoader
from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.replay.models import RegressionReport
from agent.evaluation.runners.harness_runner import HarnessRunner, SingleEvalResult

logger = logging.getLogger(__name__)

# 默认回归通过阈值
DEFAULT_REGRESSION_PASS_THRESHOLD = 0.95


class RegressionTestRunner:
    """改进效果回归测试

    改进上线后，重新执行原失败 Fixture，确认修复且未引入新问题。

    使用示例：
        runner = RegressionTestRunner(harness_runner=runner)
        report = await runner.run_regression(
            fixture_ids=["replay-abc123"],
            baseline_scores={"replay-abc123": 0.3},
        )
        if report.status == "pass":
            print("回归测试通过")
    """

    def __init__(
        self,
        harness_runner: HarnessRunner | None = None,
        dataset_loader: DatasetLoader | None = None,
        pass_threshold: float = DEFAULT_REGRESSION_PASS_THRESHOLD,
    ) -> None:
        """初始化回归测试执行器

        Args:
            harness_runner: HarnessRunner 实例，None 时使用默认实例
            dataset_loader: 数据集加载器，None 时使用默认实例
            pass_threshold: 回归通过率阈值（默认 95%）
        """
        self._harness_runner = harness_runner
        self._dataset_loader = dataset_loader
        self._pass_threshold = pass_threshold

    async def run_regression(
        self,
        fixture_ids: list[str],
        baseline_scores: dict[str, float] | None = None,
        k: int = 1,
    ) -> RegressionReport:
        """对指定 Fixture 列表执行回归测试

        步骤：
          1. 加载 Fixture（含原失败记录的 baseline 评分）
          2. 使用当前 Harness 执行评估
          3. 对比 baseline 与 current 评分
          4. 输出 pass/fail 报告

        Args:
            fixture_ids: 要回归测试的 Fixture ID 列表
            baseline_scores: 各 fixture 的 baseline 评分 {fixture_id: score}
            k: pass^k 的 k 值（默认 1，单次评估）

        Returns:
            RegressionReport 回归测试报告
        """
        baseline_scores = baseline_scores or {}

        logger.info(
            "回归测试开始: fixture_count=%d k=%d threshold=%.2f",
            len(fixture_ids),
            k,
            self._pass_threshold,
        )

        # 1. 加载 fixtures
        fixtures = self._load_fixtures(fixture_ids)
        if not fixtures:
            logger.warning("未加载到任何 fixture，回归测试终止")
            return RegressionReport(
                fixture_ids=fixture_ids,
                status="fail",
                details=[{"error": "未加载到任何 fixture"}],
            )

        # 2. 获取 HarnessRunner 实例
        runner = self._ensure_runner()
        if runner is None:
            logger.error("HarnessRunner 不可用，回归测试终止")
            return RegressionReport(
                fixture_ids=fixture_ids,
                status="fail",
                details=[{"error": "HarnessRunner 不可用"}],
            )

        # 3. 逐个 fixture 执行评估
        current_scores: dict[str, float] = {}
        details: list[dict[str, Any]] = []
        pass_count = 0
        fail_count = 0

        for fixture in fixtures:
            detail = await self._evaluate_single_fixture(
                fixture,
                runner,
                baseline_scores,
                current_scores,
            )
            details.append(detail)

            if detail["regression_passed"]:
                pass_count += 1
            else:
                fail_count += 1

        # 4. 判断整体回归结果
        total = pass_count + fail_count
        pass_rate = pass_count / total if total > 0 else 0.0
        overall_status = "pass" if pass_rate >= self._pass_threshold else "fail"

        report = RegressionReport(
            fixture_ids=fixture_ids,
            baseline_scores=baseline_scores,
            current_scores=current_scores,
            pass_count=pass_count,
            fail_count=fail_count,
            status=overall_status,
            details=details,
        )

        logger.info(
            "回归测试完成: status=%s pass=%d fail=%d pass_rate=%.2f",
            overall_status,
            pass_count,
            fail_count,
            pass_rate,
        )

        return report

    async def _evaluate_single_fixture(
        self,
        fixture: Fixture,
        runner: HarnessRunner,
        baseline_scores: dict[str, float],
        current_scores: dict[str, float],
    ) -> dict[str, Any]:
        """评估单个 fixture 并生成对比详情

        Args:
            fixture: 待评估的 fixture
            runner: HarnessRunner 实例
            baseline_scores: baseline 评分字典（会被读取）
            current_scores: current 评分字典（会被写入）

        Returns:
            评估详情字典
        """
        fixture_id = fixture.fixture_id
        baseline_score = baseline_scores.get(fixture_id, 0.0)

        try:
            # 执行评估
            result: SingleEvalResult = await runner.run_single(fixture)
            current_score = self._extract_score(result)
            current_scores[fixture_id] = current_score

            # 判断回归是否通过
            regression_passed = current_score > baseline_score

            return {
                "fixture_id": fixture_id,
                "baseline_score": round(baseline_score, 4),
                "current_score": round(current_score, 4),
                "score_improvement": round(current_score - baseline_score, 4),
                "regression_passed": regression_passed,
                "eval_success": result.success,
                "error": result.error,
                "judge_overall_score": (
                    result.judge_result.overall_score
                    if result.judge_result else 0.0
                ),
            }

        except Exception as e:
            logger.error("评估 fixture 异常 fixture_id=%s: %s", fixture_id, e)
            current_scores[fixture_id] = 0.0
            return {
                "fixture_id": fixture_id,
                "baseline_score": round(baseline_score, 4),
                "current_score": 0.0,
                "score_improvement": round(-baseline_score, 4),
                "regression_passed": False,
                "eval_success": False,
                "error": str(e),
            }

    def _load_fixtures(self, fixture_ids: list[str]) -> list[Fixture]:
        """加载指定 ID 的 fixtures

        Args:
            fixture_ids: fixture ID 列表

        Returns:
            Fixture 列表
        """
        loader = self._ensure_loader()
        if loader is None:
            logger.error("DatasetLoader 不可用")
            return []

        # 加载所有 fixtures，然后按 ID 过滤
        all_fixtures = loader.load_all()
        id_set = set(fixture_ids)
        fixtures = [f for f in all_fixtures if f.fixture_id in id_set]

        # 检查缺失的 fixture
        found_ids = {f.fixture_id for f in fixtures}
        missing_ids = id_set - found_ids
        if missing_ids:
            logger.warning("未找到以下 fixture: %s", missing_ids)

        return fixtures

    def _ensure_runner(self) -> HarnessRunner | None:
        """延迟初始化 HarnessRunner"""
        if self._harness_runner is None:
            try:
                self._harness_runner = HarnessRunner()
            except Exception as e:
                logger.warning("HarnessRunner 初始化失败: %s", e)
                return None
        return self._harness_runner

    def _ensure_loader(self) -> DatasetLoader | None:
        """延迟初始化 DatasetLoader"""
        if self._dataset_loader is None:
            try:
                self._dataset_loader = DatasetLoader()
            except Exception as e:
                logger.warning("DatasetLoader 初始化失败: %s", e)
                return None
        return self._dataset_loader

    def _extract_score(self, result: SingleEvalResult) -> float:
        """从评估结果中提取总分

        优先使用 judge_result.overall_score，其次使用 trajectory_result 的通过状态。

        Args:
            result: 单次评估结果

        Returns:
            评分（0-1）
        """
        if result.judge_result is not None:
            return float(result.judge_result.overall_score or 0.0)

        # 降级：根据 success 和 trajectory_result 计算
        if result.success:
            return 1.0

        if result.trajectory_result is not None:
            # 简化：轨迹通过给 0.6 分
            return 0.6 if result.trajectory_result.passed else 0.3

        return 0.0
