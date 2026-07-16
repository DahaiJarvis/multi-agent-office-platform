"""规则沙箱验证

对应 spec 04 第 3.4 节 RuleSandbox 与第 8.4 节规则上线安全流程。

护栏规则候选必须经过沙箱验证才能进入人工审核环节：
  1. 在评估套件上运行，验证规则不引入误报
  2. 验证规则命中率符合预期（命中失败案例、不命中正常案例）
  3. 输出 sandbox_result，通过后 status="sandboxed"

沙箱验证策略：
  - 正向验证：规则应命中已知失败案例（fixtures 标记为 adversarial/edge 且失败）
  - 负向验证：规则不应命中正常通过案例（fixtures 标记为 normal 且通过）
  - 误报率 = 负向命中数 / 负向总数
  - 召回率 = 正向命中数 / 正向总数
  - 通过条件：误报率 < 5% 且 召回率 >= 50%
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.improvement.rule_generator import GuardrailRuleCandidate

logger = logging.getLogger(__name__)

# 沙箱验证默认阈值
DEFAULT_MAX_FALSE_POSITIVE_RATE = 0.05  # 误报率上限 5%
DEFAULT_MIN_RECALL_RATE = 0.5           # 召回率下限 50%


class SandboxResult(BaseModel):
    """沙箱验证结果

    Attributes:
        passed: 是否通过验证
        total_fixtures: 验证的 fixture 总数
        positive_total: 正向样本数（应命中的失败案例）
        positive_hit: 正向命中数
        negative_total: 负向样本数（不应命中的正常案例）
        negative_hit: 负向命中数（误报）
        false_positive_rate: 误报率
        recall_rate: 召回率
        detail: 详细信息
    """

    model_config = ConfigDict(frozen=False)

    passed: bool = Field(default=False)
    total_fixtures: int = Field(default=0)
    positive_total: int = Field(default=0)
    positive_hit: int = Field(default=0)
    negative_total: int = Field(default=0)
    negative_hit: int = Field(default=0)
    false_positive_rate: float = Field(default=0.0)
    recall_rate: float = Field(default=0.0)
    detail: str = Field(default="")


class RuleSandbox:
    """规则沙箱验证

    在评估套件上验证规则候选不引入误报，确保规则质量。
    验证通过后规则候选状态从 candidate 流转为 sandboxed。

    使用示例：
        sandbox = RuleSandbox()
        result = await sandbox.validate(candidate, eval_suite)
        if result.passed:
            candidate.sandbox_passed = True
    """

    def __init__(
        self,
        max_false_positive_rate: float = DEFAULT_MAX_FALSE_POSITIVE_RATE,
        min_recall_rate: float = DEFAULT_MIN_RECALL_RATE,
    ) -> None:
        """初始化沙箱验证器

        Args:
            max_false_positive_rate: 允许的最大误报率（默认 5%）
            min_recall_rate: 要求的最小召回率（默认 50%）
        """
        self._max_fpr = max_false_positive_rate
        self._min_recall = min_recall_rate

    async def validate(
        self,
        candidate: GuardrailRuleCandidate,
        eval_suite: list[Fixture],
    ) -> SandboxResult:
        """验证规则候选

        在评估套件上运行规则，检查误报率和召回率是否符合要求。

        Args:
            candidate: 待验证的规则候选
            eval_suite: 评估套件（Fixture 列表）

        Returns:
            SandboxResult 验证结果
        """
        logger.info(
            "沙箱验证开始: rule_id=%s pattern=%s type=%s fixtures=%d",
            candidate.rule_id,
            candidate.pattern,
            candidate.rule_type,
            len(eval_suite),
        )

        # 划分正向样本（失败案例）和负向样本（正常案例）
        positive_fixtures, negative_fixtures = self._split_fixtures(eval_suite)

        # 对每个 fixture 应用规则
        positive_hit = 0
        negative_hit = 0

        for fixture in positive_fixtures:
            if self._apply_rule(candidate, fixture):
                positive_hit += 1

        for fixture in negative_fixtures:
            if self._apply_rule(candidate, fixture):
                negative_hit += 1

        positive_total = len(positive_fixtures)
        negative_total = len(negative_fixtures)
        total = positive_total + negative_total

        # 计算误报率和召回率
        false_positive_rate = (
            negative_hit / negative_total if negative_total > 0 else 0.0
        )
        recall_rate = (
            positive_hit / positive_total if positive_total > 0 else 0.0
        )

        # 判断是否通过
        passed = (
            false_positive_rate <= self._max_fpr
            and recall_rate >= self._min_recall
        )

        detail = (
            f"正向样本: {positive_hit}/{positive_total} 命中, "
            f"负向样本: {negative_hit}/{negative_total} 误报, "
            f"误报率={false_positive_rate:.2%}, 召回率={recall_rate:.2%}, "
            f"阈值: 误报率<={self._max_fpr:.2%}, 召回率>={self._min_recall:.2%}, "
            f"结果={'通过' if passed else '未通过'}"
        )

        result = SandboxResult(
            passed=passed,
            total_fixtures=total,
            positive_total=positive_total,
            positive_hit=positive_hit,
            negative_total=negative_total,
            negative_hit=negative_hit,
            false_positive_rate=round(false_positive_rate, 4),
            recall_rate=round(recall_rate, 4),
            detail=detail,
        )

        logger.info("沙箱验证完成: rule_id=%s passed=%s", candidate.rule_id, passed)
        logger.debug("沙箱验证详情: %s", detail)

        return result

    def _split_fixtures(
        self,
        eval_suite: list[Fixture],
    ) -> tuple[list[Fixture], list[Fixture]]:
        """划分正向和负向样本

        正向样本：severity 为 adversarial 或 edge 的 fixture（代表失败案例）
        负向样本：severity 为 normal 的 fixture（代表正常案例）

        Args:
            eval_suite: 评估套件

        Returns:
            (正向样本列表, 负向样本列表)
        """
        positive: list[Fixture] = []
        negative: list[Fixture] = []

        for fixture in eval_suite:
            severity = getattr(fixture, "severity", "normal").lower()
            if severity in ("adversarial", "edge"):
                positive.append(fixture)
            else:
                negative.append(fixture)

        return positive, negative

    def _apply_rule(
        self,
        candidate: GuardrailRuleCandidate,
        fixture: Fixture,
    ) -> bool:
        """对 fixture 应用规则，判断是否命中

        根据规则类型和定义检查 fixture 是否被规则命中。

        Args:
            candidate: 规则候选
            fixture: 待检查的 fixture

        Returns:
            是否命中规则
        """
        rule_def = candidate.rule_definition or {}
        check_type = str(rule_def.get("check_type", ""))

        try:
            if check_type == "regex":
                return self._check_regex(rule_def, fixture)
            if check_type == "pii_detection":
                return self._check_pii(rule_def, fixture)
            if check_type == "tool_whitelist":
                return self._check_tool_whitelist(rule_def, fixture)
            if check_type == "factuality_check":
                # 幻觉检测规则简化处理：检查输出是否为空或明显不合理
                return self._check_factuality(rule_def, fixture)
            if check_type == "permission_check":
                return self._check_permission(rule_def, fixture)
            # 未知规则类型默认不命中
            logger.debug("未知规则类型: %s，跳过", check_type)
            return False
        except Exception as e:
            logger.warning("应用规则异常 rule_id=%s: %s", candidate.rule_id, e)
            return False

    def _check_regex(self, rule_def: dict[str, Any], fixture: Fixture) -> bool:
        """正则规则检查

        检查 fixture.input 是否匹配规则定义的正则模式。

        Args:
            rule_def: 规则定义
            fixture: 待检查的 fixture

        Returns:
            是否命中
        """
        import re

        patterns = rule_def.get("patterns", [])
        text = fixture.input or ""

        for pattern in patterns:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            except re.error as e:
                logger.warning("正则表达式无效 pattern=%s: %s", pattern, e)
                continue
        return False

    def _check_pii(self, rule_def: dict[str, Any], fixture: Fixture) -> bool:
        """PII 检测规则检查

        检查 fixture.input 或 context 中是否包含指定类型的 PII。

        Args:
            rule_def: 规则定义
            fixture: 待检查的 fixture

        Returns:
            是否命中
        """
        try:
            from security.desensitize import has_pii
        except ImportError:
            logger.warning("security.desensitize 不可用，跳过 PII 检测")
            return False

        text = fixture.input or ""
        if has_pii(text):
            return True

        # 检查 context 中的文本字段
        context = getattr(fixture, "context", {}) or {}
        for value in context.values():
            if isinstance(value, str) and has_pii(value):
                return True

        return False

    def _check_tool_whitelist(
        self,
        rule_def: dict[str, Any],
        fixture: Fixture,
    ) -> bool:
        """工具白名单规则检查

        检查 fixture 的 expected_tools 中是否包含被禁止的工具。

        Args:
            rule_def: 规则定义
            fixture: 待检查的 fixture

        Returns:
            是否命中（即存在禁止工具）
        """
        forbidden_tools = rule_def.get("forbidden_tools", [])
        if not forbidden_tools:
            return False

        expected_tools = getattr(fixture, "expected_tools", []) or []
        forbidden_set = set(forbidden_tools)
        expected_set = set(expected_tools)

        # 如果 fixture 期望调用被禁止的工具，说明该规则会命中
        return bool(forbidden_set & expected_set)

    def _check_factuality(
        self,
        rule_def: dict[str, Any],
        fixture: Fixture,
    ) -> bool:
        """事实性检查规则（简化）

        幻觉检测规则简化处理，检查 success_criteria 中是否包含关键约束。

        Args:
            rule_def: 规则定义
            fixture: 待检查的 fixture

        Returns:
            是否命中
        """
        # 简化实现：如果 fixture 有 safety_constraints 则认为规则可能命中
        safety_constraints = getattr(fixture, "safety_constraints", []) or []
        return len(safety_constraints) > 0

    def _check_permission(
        self,
        rule_def: dict[str, Any],
        fixture: Fixture,
    ) -> bool:
        """权限检查规则

        检查 fixture.context 中是否包含权限相关信息。

        Args:
            rule_def: 规则定义
            fixture: 待检查的 fixture

        Returns:
            是否命中
        """
        context = getattr(fixture, "context", {}) or {}
        # 如果 context 中有 permissions 字段且为受限权限，则命中
        permissions = context.get("permissions") or context.get("user_role")
        if permissions and isinstance(permissions, str):
            return permissions.lower() in ("restricted", "guest", "limited")
        return False
