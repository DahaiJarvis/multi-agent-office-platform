"""归档与规则数据模型

对应 spec 04 第 4.4 节 + spec 05 第五章数据模型。

数据模型：
  - FailureArchiveRecord: 失败案例归档记录（spec 04）
  - GuardrailRuleCandidateRecord: 护栏规则候选记录（spec 04，持久化）
  - RuleType / RuleStatus / GuardrailLayer: 枚举（spec 05）
  - GuardrailRuleCandidate: 候选护栏规则（spec 05，含完整生命周期）
  - SandboxReport: 沙箱验证报告（spec 05）
  - RuleVersion: 规则版本链节点（spec 05）
  - RuleMetrics: 规则效果指标（spec 05）
"""

import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _gen_id(prefix: str) -> str:
    """生成带前缀的唯一 ID"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ==================== spec 05 枚举 ====================


class RuleType(str, Enum):
    """规则类型（spec 05 第 5.1 节）"""

    REGEX = "regex"            # 正则匹配规则
    KEYWORD = "keyword"        # 关键词匹配规则
    FUNCTION = "function"      # Python 函数规则（复杂逻辑）
    SCHEMA = "schema"          # JSON Schema 校验规则


class RuleStatus(str, Enum):
    """规则状态（spec 05 第 2.3 节状态机）"""

    CANDIDATE = "candidate"
    SANDBOX_RUNNING = "sandbox_running"
    SANDBOX_PASSED = "sandbox_passed"
    SANDBOX_FAILED = "sandbox_failed"
    APPROVED = "approved"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class GuardrailLayer(str, Enum):
    """护栏层（规则作用位置）"""

    INPUT = "input"            # 输入护栏
    TOOL = "tool"              # 工具调用护栏
    OUTPUT = "output"          # 输出护栏


# ==================== spec 05 数据模型 ====================


class GuardrailRuleCandidate(BaseModel):
    """候选护栏规则（spec 05 第 5.1 节）

    从失败案例生成的候选规则，经历完整生命周期：
    candidate -> sandbox_running -> sandbox_passed -> approved -> active

    Attributes:
        rule_id: 规则唯一标识
        pattern: 对应的失败模式
        rule_type: 规则类型（regex/keyword/function/schema）
        rule_spec: 规则定义（正则/关键词/函数体/Schema）
        layer: 规则作用的护栏层
        action: 命中后动作 block/redact/warn
        description: 规则说明
        source_trace_id: 来源 Trace ID
        tenant_id: 租户ID，空表示平台级
        status: 规则状态
        created_at: 创建时间
        created_by: 创建者
    """

    model_config = ConfigDict(frozen=False)

    rule_id: str = Field(default_factory=lambda: _gen_id("rule"))
    pattern: str = Field(..., description="对应的失败模式")
    rule_type: RuleType = Field(..., description="规则类型")
    rule_spec: dict[str, Any] = Field(
        default_factory=dict,
        description="规则定义（正则/关键词/函数体/Schema）",
    )
    layer: GuardrailLayer = Field(
        default=GuardrailLayer.INPUT,
        description="规则作用的护栏层",
    )
    action: str = Field(default="block", description="命中后动作: block/redact/warn")
    description: str = Field(default="", description="规则说明")
    source_trace_id: str = Field(default="", description="来源 Trace ID")
    tenant_id: str = Field(default="", description="租户ID，空表示平台级")
    status: RuleStatus = Field(
        default=RuleStatus.CANDIDATE,
        description="规则状态",
    )
    created_at: float = Field(default_factory=time.time, description="创建时间")
    created_by: str = Field(default="system", description="创建者")


class SandboxReport(BaseModel):
    """沙箱验证报告（spec 05 第 5.1 节）

    Attributes:
        candidate_rule_id: 候选规则 ID
        recall: 召回率
        false_positive_rate: 误报率
        compatibility: 兼容性
        positive_hits: 正样本命中详情
        negative_hits: 负样本误报详情
        passed: 是否通过沙箱门禁
        executed_at: 执行时间
        duration_ms: 执行耗时(ms)
    """

    model_config = ConfigDict(frozen=False)

    candidate_rule_id: str = Field(..., description="候选规则 ID")
    recall: float = Field(..., ge=0.0, le=1.0, description="召回率")
    false_positive_rate: float = Field(..., ge=0.0, le=1.0, description="误报率")
    compatibility: float = Field(..., ge=0.0, le=1.0, description="兼容性")
    positive_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="正样本命中详情"
    )
    negative_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="负样本误报详情"
    )
    passed: bool = Field(..., description="是否通过沙箱门禁")
    executed_at: float = Field(default_factory=time.time, description="执行时间")
    duration_ms: int = Field(default=0, description="执行耗时(ms)")


class RuleVersion(BaseModel):
    """规则版本（版本链节点，spec 05 第 5.1 节）

    Attributes:
        rule_id: 规则 ID
        version: 版本号，递增
        rule_spec: 规则定义
        status: 规则状态
        changed_by: 变更人
        changed_at: 变更时间
        change_reason: 变更原因
        change_type: 变更类型 create/status_change/rollback
    """

    model_config = ConfigDict(frozen=False)

    rule_id: str = Field(...)
    version: int = Field(..., description="版本号，递增")
    rule_spec: dict[str, Any] = Field(default_factory=dict)
    status: RuleStatus = Field(default=RuleStatus.CANDIDATE)
    changed_by: str = Field(default="", description="变更人")
    changed_at: float = Field(default_factory=time.time)
    change_reason: str = Field(default="", description="变更原因")
    change_type: str = Field(..., description="变更类型: create/status_change/rollback")


class RuleMetrics(BaseModel):
    """规则效果指标（spec 05 第 4.6 节）

    Attributes:
        rule_id: 规则 ID
        hit_count: 命中次数
        false_positive_count: 误报次数
        false_positive_rate: 误报率
        last_hit_at: 最近命中时间
    """

    model_config = ConfigDict(frozen=False)

    rule_id: str = Field(...)
    hit_count: int = Field(default=0, description="命中次数")
    false_positive_count: int = Field(default=0, description="误报次数")
    false_positive_rate: float = Field(default=0.0, description="误报率")
    last_hit_at: float = Field(default=0.0, description="最近命中时间")


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
