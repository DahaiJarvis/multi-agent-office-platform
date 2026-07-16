"""replay 子包 - Trace 回放与失败转 fixture

对应 spec 04 第 3.2/3.3/3.5 节。

模块组成：
  - deterministic_mode: 确定性模式上下文管理器
  - trace_replayer: Trace 回放执行器
  - trace_to_fixture: 失败 trace 转 Fixture 转换器
  - models: 回放与评估数据模型
  - regression_test_runner: 回归测试执行器
"""

from agent.evaluation.replay.deterministic_mode import DeterministicMode
from agent.evaluation.replay.trace_replayer import TraceReplayer, ReplayResult, TrajectoryDiff
from agent.evaluation.replay.trace_to_fixture import TraceToFixtureConverter, FailureAnalysis
from agent.evaluation.replay.models import (
    ReplayRecord,
    SessionEvalReport,
    RegressionReport,
)
from agent.evaluation.replay.regression_test_runner import RegressionTestRunner
from agent.evaluation.replay.eval_scheduler import (
    EvalScheduler,
    FailureFilter,
    FailedSession,
)

__all__ = [
    "DeterministicMode",
    "TraceReplayer",
    "ReplayResult",
    "TrajectoryDiff",
    "TraceToFixtureConverter",
    "FailureAnalysis",
    "ReplayRecord",
    "SessionEvalReport",
    "RegressionReport",
    "RegressionTestRunner",
    "EvalScheduler",
    "FailureFilter",
    "FailedSession",
]
