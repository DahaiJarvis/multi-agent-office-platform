"""归档与规则数据模型

对应 spec 04 第 4.4 节。

数据模型：
  - FailureArchiveRecord: 失败案例归档记录
  - GuardrailRuleCandidateRecord: 护栏规则候选记录（持久化）
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _gen_id(prefix: str) -> str:
    """生成带前缀的唯一 ID"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class FailureArchiveRecord(BaseModel):
    """失败案例归档记录

    记录一个失败案例的归档信息，用于追踪改进状态。

    Attributes:
        archive_id: 归档记录唯一标识
        session_id: 失败的原始会话 ID
        fixture_id: 转化生成的 Fixture ID
        report_id: 关联的评估报告 ID
        failure_pattern: 失败模式 injection/pii/tool_misuse/...
        failure_detail: 失败详情
        archived_at: 归档时间
        improvement_status: 改进状态 pending/improving/resolved
        resolved_at: 解决时间
    """

    model_config = ConfigDict(frozen=False)

    archive_id: str = Field(default_factory=lambda: _gen_id("archive"))
    session_id: str = Field(..., description="失败的原始会话 ID")
    fixture_id: str = Field(..., description="转化生成的 Fixture ID")
    report_id: str = Field(..., description="关联的评估报告 ID")
    failure_pattern: str = Field(
        default="",
        description="失败模式：injection/pii/tool_misuse/hallucination/policy_violation/other",
    )
    failure_detail: str = Field(default="")
    archived_at: datetime = Field(default_factory=datetime.now)
    improvement_status: str = Field(
        default="pending",
        description="改进状态：pending/improving/resolved",
    )
    resolved_at: datetime | None = Field(default=None)


class GuardrailRuleCandidateRecord(BaseModel):
    """护栏规则候选记录（持久化）

    记录一个护栏规则候选的完整生命周期信息。

    状态流转：candidate -> sandboxed -> approved -> online / rejected

    Attributes:
        rule_id: 规则唯一标识
        archive_id: 关联的归档记录 ID
        pattern: 失败模式
        rule_type: 规则类型 input/tool/output
        rule_definition: 规则定义（正则/关键词/LLM 判断条件）
        confidence: 置信度（0-1）
        sandbox_passed: 沙箱验证是否通过
        sandbox_result: 沙箱验证结果
        approved: 是否已审核通过
        approved_by: 审核人
        approved_at: 审核时间
        status: 规则状态 candidate/sandboxed/approved/rejected/online
        created_at: 创建时间
    """

    model_config = ConfigDict(frozen=False)

    rule_id: str = Field(default_factory=lambda: _gen_id("rule"))
    archive_id: str = Field(..., description="关联的归档记录 ID")
    pattern: str = Field(default="", description="失败模式")
    rule_type: str = Field(
        default="input",
        description="规则类型：input_guardrail/tool_guardrail/output_guardrail",
    )
    rule_definition: dict[str, Any] = Field(
        default_factory=dict,
        description="规则定义（正则/关键词/LLM 判断条件）",
    )
    confidence: float = Field(default=0.0, description="置信度（0-1）")
    sandbox_passed: bool = Field(default=False)
    sandbox_result: dict[str, Any] = Field(default_factory=dict)
    approved: bool = Field(default=False)
    approved_by: str = Field(default="")
    approved_at: datetime | None = Field(default=None)
    status: str = Field(
        default="candidate",
        description="状态：candidate/sandboxed/approved/rejected/online",
    )
    created_at: datetime = Field(default_factory=datetime.now)
