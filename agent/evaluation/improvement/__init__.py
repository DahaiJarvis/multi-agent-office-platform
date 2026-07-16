"""improvement 子包 - 失败案例归档与改进

对应 spec 04 第 3.4 节 + spec 05 全文。

模块组成：
  - models: 数据模型（spec 04: FailureArchiveRecord / GuardrailRuleCandidateRecord
                     spec 05: RuleType / RuleStatus / GuardrailLayer / GuardrailRuleCandidate / SandboxReport / RuleVersion / RuleMetrics）
  - failure_pattern: 失败模式分类器（spec 04 classify + spec 05 classify_detailed）
  - rule_generator: 护栏规则候选生成器（spec 04 generate_rule + spec 05 generate_rule_v2）
  - rule_sandbox: 规则沙箱验证（spec 04 validate + spec 05 validate_v2）
  - failure_archive: 失败案例归档与改进（spec 04）
  - rule_store: 规则持久化存储（spec 05）
  - dynamic_loader: 动态规则加载器（spec 05）
  - rule_metrics: 规则效果监控器（spec 05）
  - rule_rollback: 规则回滚器（spec 05）
  - trace_consumer: 失败 Trace 消费器（spec 05）
"""

# spec 04 模型
from agent.evaluation.improvement.models import (
    FailureArchiveRecord,
    GuardrailRuleCandidateRecord,
)
# spec 05 模型
from agent.evaluation.improvement.models import (
    GuardrailLayer,
    GuardrailRuleCandidate,
    RuleMetrics,
    RuleStatus,
    RuleType,
    RuleVersion,
    SandboxReport,
)
# spec 05 分类器
from agent.evaluation.improvement.failure_pattern import (
    ClassificationResult,
    FailurePattern,
    FailurePatternClassifier,
)
# spec 04 生成器（GuardrailRuleCandidate from rule_generator 保持兼容）
from agent.evaluation.improvement.rule_generator import (
    GuardrailRuleGenerator,
)
# spec 04 沙箱
from agent.evaluation.improvement.rule_sandbox import (
    RuleSandbox,
    SandboxResult,
)
# spec 04 归档
from agent.evaluation.improvement.failure_archive import FailureArchive
# spec 05 新增模块
from agent.evaluation.improvement.rule_store import GuardrailRuleStore
from agent.evaluation.improvement.dynamic_loader import DynamicRuleLoader
from agent.evaluation.improvement.rule_metrics import RuleMetricsCollector
from agent.evaluation.improvement.rule_rollback import RuleRollback
from agent.evaluation.improvement.trace_consumer import FailureTraceConsumer

__all__ = [
    # spec 04 模型
    "FailureArchiveRecord",
    "GuardrailRuleCandidateRecord",
    # spec 05 模型
    "RuleType",
    "RuleStatus",
    "GuardrailLayer",
    "GuardrailRuleCandidate",
    "SandboxReport",
    "RuleVersion",
    "RuleMetrics",
    # 分类器
    "FailurePattern",
    "ClassificationResult",
    "FailurePatternClassifier",
    # 生成器
    "GuardrailRuleGenerator",
    # 沙箱
    "RuleSandbox",
    "SandboxResult",
    # 归档
    "FailureArchive",
    # spec 05 新增
    "GuardrailRuleStore",
    "DynamicRuleLoader",
    "RuleMetricsCollector",
    "RuleRollback",
    "FailureTraceConsumer",
]
