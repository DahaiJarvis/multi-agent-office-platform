"""improvement 子包 - 失败案例归档与改进

对应 spec 04 第 3.4 节。

模块组成：
  - models: 数据模型（FailureArchiveRecord / GuardrailRuleCandidateRecord）
  - failure_pattern: 失败模式分类器
  - rule_generator: 护栏规则候选生成器
  - rule_sandbox: 规则沙箱验证
  - failure_archive: 失败案例归档与改进
"""

from agent.evaluation.improvement.models import (
    FailureArchiveRecord,
    GuardrailRuleCandidateRecord,
)
from agent.evaluation.improvement.failure_pattern import FailurePatternClassifier
from agent.evaluation.improvement.rule_generator import (
    GuardrailRuleCandidate,
    GuardrailRuleGenerator,
)
from agent.evaluation.improvement.rule_sandbox import RuleSandbox
from agent.evaluation.improvement.failure_archive import FailureArchive

__all__ = [
    "FailureArchiveRecord",
    "GuardrailRuleCandidateRecord",
    "FailurePatternClassifier",
    "GuardrailRuleCandidate",
    "GuardrailRuleGenerator",
    "RuleSandbox",
    "FailureArchive",
]
