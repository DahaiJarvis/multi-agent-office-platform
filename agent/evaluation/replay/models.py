"""回放与评估数据模型

对应 spec 04 第 4.3 节。

数据模型：
  - ReplayRecord: 回放记录（持久化）
  - SessionEvalReport: 单 session 评估报告
  - RegressionReport: 回归测试报告

与 P0 HarnessRunner.EvalReport（套件级）的区别：
  本模块的 SessionEvalReport 是单 session 级别的评估报告，
  用于 Trace-Eval-Improve 闭环中对单个失败 session 的评估。
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _gen_id(prefix: str) -> str:
    """生成带前缀的唯一 ID"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ReplayRecord(BaseModel):
    """回放记录（持久化）

    记录一次 Trace 回放的完整信息，用于审计和回归对比。

    Attributes:
        replay_id: 回放记录唯一标识
        original_session_id: 原始会话 ID
        new_session_id: 回放生成的新会话 ID
        fixture_id: 关联的 Fixture ID
        replayed_at: 回放时间
        deterministic_mode: 是否使用确定性模式
        original_input: 原始用户输入（已脱敏）
        new_output: 回放输出（已脱敏）
        trajectory_diff: 轨迹差异
        duration_ms: 回放耗时（毫秒）
        status: 回放状态 success/failed/error
    """

    model_config = ConfigDict(frozen=False)

    replay_id: str = Field(default_factory=lambda: _gen_id("replay"))
    original_session_id: str = Field(..., description="原始会话 ID")
    new_session_id: str = Field(default="", description="回放生成的新会话 ID")
    fixture_id: str = Field(default="", description="关联的 Fixture ID")
    replayed_at: datetime = Field(default_factory=datetime.now)
    deterministic_mode: bool = Field(default=True)
    original_input: str = Field(default="", description="原始用户输入（已脱敏）")
    new_output: str = Field(default="", description="回放输出（已脱敏）")
    trajectory_diff: dict[str, Any] = Field(
        default_factory=dict,
        description="轨迹差异：tools_added/tools_removed/tools_reordered/output_changed",
    )
    duration_ms: float = Field(default=0.0, description="回放耗时（毫秒）")
    status: str = Field(default="", description="回放状态：success/failed/error")


class SessionEvalReport(BaseModel):
    """单 session 评估报告

    对单个失败 session 回放后的评估结果，含多维度评分和 pass@k 信息。

    与 P0 HarnessRunner.EvalReport（套件级）的区别：
    本报告针对单个 session，用于闭环中对失败案例的精细化评估。

    Attributes:
        report_id: 报告唯一标识
        replay_id: 关联的回放记录 ID
        fixture_id: 关联的 Fixture ID
        agent_name: 被评估的 Agent 名称
        evaluated_at: 评估时间
        correctness_score: 正确性评分（0-1）
        completeness_score: 完整性评分（0-1）
        safety_score: 安全性评分（0-1）
        trajectory_score: 轨迹合理性评分（0-1）
        overall_score: 加权总分（0-1）
        pass_at_k: pass@k 是否通过
        pass_caret_k: pass^k 是否通过
        k: k 值
        success_rate: 成功率
        safety_violations: 安全违规列表
        critical_safety_violations: critical 级别安全违规数
        judge_reasoning: judge 评分理由
        status: 评估结果 pass/fail
    """

    model_config = ConfigDict(frozen=False)

    report_id: str = Field(default_factory=lambda: _gen_id("eval"))
    replay_id: str = Field(default="", description="关联的回放记录 ID")
    fixture_id: str = Field(..., description="关联的 Fixture ID")
    agent_name: str = Field(default="", description="被评估的 Agent 名称")
    evaluated_at: datetime = Field(default_factory=datetime.now)

    # 评分维度
    correctness_score: float = Field(default=0.0, description="正确性评分（0-1）")
    completeness_score: float = Field(default=0.0, description="完整性评分（0-1）")
    safety_score: float = Field(default=0.0, description="安全性评分（0-1）")
    trajectory_score: float = Field(default=0.0, description="轨迹合理性评分（0-1）")
    overall_score: float = Field(default=0.0, description="加权总分（0-1）")

    # pass@k
    pass_at_k: bool = Field(default=False)
    pass_caret_k: bool = Field(default=False)
    k: int = Field(default=1)
    success_rate: float = Field(default=0.0)

    # 安全
    safety_violations: list[str] = Field(default_factory=list)
    critical_safety_violations: int = Field(default=0)

    judge_reasoning: str = Field(default="")
    status: str = Field(default="", description="评估结果：pass/fail")


class RegressionReport(BaseModel):
    """回归测试报告

    改进上线后对原失败 Fixture 重新执行评估的对比报告。

    Attributes:
        report_id: 报告唯一标识
        fixture_ids: 参与回归的 Fixture ID 列表
        run_at: 回归执行时间
        baseline_scores: 改进前各 fixture 的评分 {fixture_id: score}
        current_scores: 改进后各 fixture 的评分 {fixture_id: score}
        pass_count: 通过数
        fail_count: 失败数
        status: 回归结果 pass/fail
        details: 详细对比信息
    """

    model_config = ConfigDict(frozen=False)

    report_id: str = Field(default_factory=lambda: _gen_id("regression"))
    fixture_ids: list[str] = Field(default_factory=list)
    run_at: datetime = Field(default_factory=datetime.now)
    baseline_scores: dict[str, float] = Field(
        default_factory=dict,
        description="改进前各 fixture 的评分 {fixture_id: score}",
    )
    current_scores: dict[str, float] = Field(
        default_factory=dict,
        description="改进后各 fixture 的评分 {fixture_id: score}",
    )
    pass_count: int = Field(default=0)
    fail_count: int = Field(default=0)
    status: str = Field(default="", description="回归结果：pass/fail")
    details: list[dict[str, Any]] = Field(default_factory=list)
