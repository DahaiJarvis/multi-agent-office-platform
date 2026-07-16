"""pass@k / pass^k 一致性评估

pass@k: k 次尝试中至少 1 次成功（宽松）
pass^k: k 次尝试全部成功（严格，SRE 推荐）

行业标准：pass^5 >= 95% 才能进入生产

对应 spec 文档 3.4 节与 4.6 节。
"""

import asyncio
import logging
import math
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field, ConfigDict

from agent.evaluation.fixtures.fixture_schema import Fixture

if TYPE_CHECKING:
    from agent.evaluation.runners.harness_runner import HarnessRunner, SingleEvalResult

logger = logging.getLogger(__name__)


class PassKResult(BaseModel):
    """pass@k / pass^k 评估结果

    Attributes:
        fixture_id: fixture ID
        k: 重复执行次数
        pass_mode: 模式：pass@k | pass^k
        pass_at_k: pass@k 是否通过（至少 1 次成功）
        pass_caret_k: pass^k 是否通过（全部成功）
        success_count: 成功次数
        success_rate: 成功率（0-1）
        token_variance: token 用量方差（归一化标准差，0-1）
        duration_variance: 耗时方差（归一化标准差，0-1）
        cost_variance: 成本方差（归一化标准差，0-1）
        results: k 次执行的详细结果
    """

    model_config = ConfigDict(frozen=False)

    fixture_id: str = Field(..., description="fixture ID")
    k: int = Field(..., description="重复执行次数")
    pass_mode: str = Field(..., description="模式：pass@k | pass^k")

    pass_at_k: bool = Field(..., description="pass@k 是否通过（至少 1 次成功）")
    pass_caret_k: bool = Field(..., description="pass^k 是否通过（全部成功）")

    success_count: int = Field(..., description="成功次数")
    success_rate: float = Field(..., description="成功率（0-1）")

    token_variance: float = Field(
        default=0.0,
        description="token 用量方差（归一化标准差，0-1）",
    )
    duration_variance: float = Field(
        default=0.0,
        description="耗时方差（归一化标准差，0-1）",
    )
    cost_variance: float = Field(
        default=0.0,
        description="成本方差（归一化标准差，0-1）",
    )

    results: list[Any] = Field(
        default_factory=list,
        description="k 次执行的详细结果（SingleEvalResult 列表，使用 Any 避免循环导入）",
    )


class PassKEvaluator:
    """一致性评估器

    对同一 Fixture 重复执行 k 次，统计 pass@k 和 pass^k。

    使用示例：
        runner = HarnessRunner(agent_factory=...)
        evaluator = PassKEvaluator(runner)
        result = await evaluator.evaluate(fixture, k=5, pass_mode="pass^k")
        if not result.pass_caret_k:
            print(f"pass^5 未通过: 成功 {result.success_count}/{result.k}")
    """

    def __init__(self, runner: "HarnessRunner") -> None:
        """初始化

        Args:
            runner: 评估执行器实例
        """
        self._runner = runner

    async def evaluate(
        self,
        fixture: Fixture,
        k: int = 5,
        pass_mode: str = "pass^k",
        concurrency: int = 5,
    ) -> PassKResult:
        """对同一 Fixture 重复执行 k 次，统计一致性

        Args:
            fixture: 测试 Fixture
            k: 重复次数，默认 5
            pass_mode: "pass@k"（至少 1 次成功）或 "pass^k"（全部成功）
            concurrency: 并发度，默认 5（k 次执行并发控制）

        Returns:
            PassKResult，含 pass_at_k / pass_caret_k / success_rate / 方差
        """
        logger.info("pass@%d 评估: fixture=%s mode=%s", k, fixture.fixture_id, pass_mode)

        # 使用 semaphore 控制并发，避免 k 过大压垮模型
        semaphore = asyncio.Semaphore(concurrency)

        async def _run_once() -> "SingleEvalResult":
            async with semaphore:
                return await self._runner.run_single(fixture)

        # 并发执行 k 次
        tasks = [_run_once() for _ in range(k)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常结果
        single_results: list["SingleEvalResult"] = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("pass@k 第 %d 次执行异常: %s", idx + 1, result)
                # 构造失败结果
                from agent.evaluation.runners.harness_runner import SingleEvalResult
                single_results.append(SingleEvalResult(
                    fixture_id=fixture.fixture_id,
                    success=False,
                    error=str(result),
                ))
            else:
                single_results.append(result)

        # 统计
        success_count = sum(1 for r in single_results if r.success)
        success_rate = success_count / k if k > 0 else 0.0
        pass_at_k = success_count >= 1
        pass_caret_k = success_count == k

        # 计算方差
        token_values = [r.usage.total_tokens for r in single_results]
        duration_values = [r.duration_ms for r in single_results]
        cost_values = [r.usage.estimated_cost for r in single_results]

        token_variance = self.compute_variance(token_values)
        duration_variance = self.compute_variance(duration_values)
        cost_variance = self.compute_variance(cost_values)

        result = PassKResult(
            fixture_id=fixture.fixture_id,
            k=k,
            pass_mode=pass_mode,
            pass_at_k=pass_at_k,
            pass_caret_k=pass_caret_k,
            success_count=success_count,
            success_rate=success_rate,
            token_variance=token_variance,
            duration_variance=duration_variance,
            cost_variance=cost_variance,
            results=single_results,
        )

        logger.info(
            "pass@%d 完成: fixture=%s success=%d/%d pass_at_k=%s pass^k=%s",
            k, fixture.fixture_id, success_count, k, pass_at_k, pass_caret_k,
        )
        return result

    @staticmethod
    def compute_variance(values: list[float]) -> float:
        """计算方差（归一化标准差，0-1）

        归一化方式：标准差 / 均值。
        均值为 0 时返回 0。

        Args:
            values: 数值列表

        Returns:
            方差值（归一化标准差，0-1）
        """
        if not values or len(values) < 2:
            return 0.0

        mean = sum(values) / len(values)
        if mean == 0:
            return 0.0

        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std_dev = math.sqrt(variance)
        normalized = std_dev / mean

        # 限制在 0-1 范围
        return min(1.0, max(0.0, normalized))
