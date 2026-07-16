"""Agent 评估体系模块

提供 2026 年 Agent Evaluation Harness 标准四件套：
  - Fixtures: 结构化测试数据集
  - Rubrics: LLM-as-judge 评分标准
  - Canaries: 金丝雀回归测试 + CI 门禁
  - Replay: Trace 回放与失败转 fixture

核心导出：
  - Fixture: 评估测试数据模型
  - Rubric / JudgeResult: 评分标准与结果
  - HarnessRunner: 评估执行器
  - PassKEvaluator: pass@k / pass^k 一致性评估
  - CIGate: CI 门禁判断
  - TraceReplayer: Trace 回放执行器
"""

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.rubrics.rubric_schema import Rubric, RubricDimension, JudgeResult, DimensionScore
from agent.evaluation.runners.trajectory_eval import TrajectoryEvalResult, CheckResult
from agent.evaluation.runners.harness_runner import (
    SingleEvalResult,
    TokenUsage,
    EvalReport,
    HarnessRunner,
)
from agent.evaluation.runners.pass_k import PassKResult, PassKEvaluator
from agent.evaluation.canaries.ci_gate import CIGate
from agent.evaluation.canaries.canary_manager import CanaryManager
from agent.evaluation.replay.deterministic_mode import DeterministicMode
from agent.evaluation.replay.trace_replayer import TraceReplayer, ReplayResult, TrajectoryDiff
from agent.evaluation.replay.trace_to_fixture import TraceToFixtureConverter, FailureAnalysis

__all__ = [
    # 数据模型
    "Fixture",
    "Rubric",
    "RubricDimension",
    "JudgeResult",
    "DimensionScore",
    "CheckResult",
    "TrajectoryEvalResult",
    "TokenUsage",
    "SingleEvalResult",
    "EvalReport",
    "PassKResult",
    "ReplayResult",
    "TrajectoryDiff",
    "FailureAnalysis",
    # 执行器
    "HarnessRunner",
    "PassKEvaluator",
    "CIGate",
    "CanaryManager",
    "DeterministicMode",
    "TraceReplayer",
    "TraceToFixtureConverter",
]
