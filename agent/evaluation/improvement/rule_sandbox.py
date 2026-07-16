"""规则沙箱验证

对应 spec 04 第 3.4 节 + spec 05 第 4.3 节 RuleSandbox。

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

spec 05 增强：
  - validate_v2: 返回 SandboxReport（含兼容性检查）
  - _prepare_suite: 准备验证套件（正样本+负样本）
  - _run_with_rule: 注入规则重跑评估套件
  - _check_compatibility: 兼容性检查（不引入回归）
"""

import logging
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.improvement.models import (
    GuardrailRuleCandidate as Spec05Candidate,
    SandboxReport,
)
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

    # ==================== spec 05 增强：详细验证接口 ====================

    # 门禁阈值（spec 05 第 4.3 节）
    FALSE_POSITIVE_THRESHOLD = 0.05  # 误报率上限 5%
    RECALL_THRESHOLD = 0.80          # 召回率下限 80%
    COMPATIBILITY_THRESHOLD = 0.95   # 兼容性下限 95%

    async def validate_v2(
        self,
        candidate: Spec05Candidate,
        target_fixtures: list[dict[str, Any]] | None = None,
        eval_suite: list[Fixture] | None = None,
    ) -> SandboxReport:
        """在沙箱中验证候选规则（spec 05 第 4.3 节）

        验证维度：
          - 召回率：规则能命中目标失败案例
          - 误报率：规则不误伤正常用例（核心门禁指标）
          - 兼容性：规则不破坏现有评估用例的通过状态

        Args:
            candidate: 候选规则（spec 05 GuardrailRuleCandidate）
            target_fixtures: 目标失败案例（验证召回率），为空时从评估套件中提取
            eval_suite: 评估套件（验证兼容性和误报率）

        Returns:
            沙箱验证报告 SandboxReport
        """
        start_time = time.time()

        # 1. 准备验证套件
        positives, negatives = await self._prepare_suite(
            candidate, target_fixtures, eval_suite
        )

        # 2. 注入候选规则后重跑评估套件
        positive_hits = await self._run_with_rule(candidate, positives)
        negative_hits = await self._run_with_rule(candidate, negatives)

        # 3. 计算指标
        recall = len(positive_hits) / len(positives) if positives else 0.0
        false_positive_rate = (
            len(negative_hits) / len(negatives) if negatives else 0.0
        )

        # 4. 兼容性检查
        compatibility = await self._check_compatibility(candidate, eval_suite or [])

        # 5. 门禁判定
        passed = (
            false_positive_rate < self.FALSE_POSITIVE_THRESHOLD
            and recall >= self.RECALL_THRESHOLD
            and compatibility >= self.COMPATIBILITY_THRESHOLD
        )

        duration_ms = int((time.time() - start_time) * 1000)

        report = SandboxReport(
            candidate_rule_id=candidate.rule_id,
            recall=round(recall, 4),
            false_positive_rate=round(false_positive_rate, 4),
            compatibility=round(compatibility, 4),
            positive_hits=positive_hits,
            negative_hits=negative_hits,
            passed=passed,
            duration_ms=duration_ms,
        )

        logger.info(
            "沙箱验证完成(spec05): rule_id=%s recall=%.2f fpr=%.2f compat=%.2f passed=%s",
            candidate.rule_id, recall, false_positive_rate, compatibility, passed,
        )

        return report

    async def _prepare_suite(
        self,
        candidate: Spec05Candidate,
        target_fixtures: list[dict[str, Any]] | None,
        eval_suite: list[Fixture] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """准备验证套件：正样本（应拦截）+ 负样本（不应拦截）

        Args:
            candidate: 候选规则
            target_fixtures: 目标失败案例
            eval_suite: 评估套件

        Returns:
            (正样本列表, 负样本列表)
        """
        positives: list[dict[str, Any]] = []
        negatives: list[dict[str, Any]] = []

        # 正样本：目标失败案例
        if target_fixtures:
            positives = list(target_fixtures)
        elif eval_suite:
            # 从评估套件中提取 adversarial/edge 作为正样本
            for fixture in eval_suite:
                fixture_dict = self._fixture_to_dict(fixture)
                severity = fixture_dict.get("severity", "normal").lower()
                if severity in ("adversarial", "edge"):
                    positives.append(fixture_dict)

        # 负样本：正常用例
        if eval_suite:
            for fixture in eval_suite:
                fixture_dict = self._fixture_to_dict(fixture)
                severity = fixture_dict.get("severity", "normal").lower()
                if severity not in ("adversarial", "edge"):
                    negatives.append(fixture_dict)

        # 确保至少有 1 个负样本（避免除零）
        if not negatives:
            negatives = [{"fixture_id": "default_negative", "input": "正常请求", "expected_tools": [], "context": {}}]

        return positives, negatives

    @staticmethod
    def _fixture_to_dict(fixture: Any) -> dict[str, Any]:
        """将 Fixture 对象或字典统一转换为字典格式

        Args:
            fixture: Fixture 对象或字典

        Returns:
            统一的字典格式
        """
        if isinstance(fixture, dict):
            return dict(fixture)
        return {
            "fixture_id": getattr(fixture, "fixture_id", ""),
            "input": getattr(fixture, "input", ""),
            "expected_tools": getattr(fixture, "expected_tools", []),
            "context": getattr(fixture, "context", {}),
            "severity": getattr(fixture, "severity", "normal"),
        }

    async def _run_with_rule(
        self,
        candidate: Spec05Candidate,
        samples: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """注入候选规则后重跑评估套件，返回命中列表

        Args:
            candidate: 候选规则
            samples: 样本列表

        Returns:
            命中样本列表
        """
        hits: list[dict[str, Any]] = []

        rule_spec = candidate.rule_spec or {}
        rule_type = candidate.rule_type.value if hasattr(candidate.rule_type, "value") else str(candidate.rule_type)

        for sample in samples:
            if self._apply_rule_v2(rule_type, rule_spec, sample):
                hits.append(sample)

        return hits

    def _apply_rule_v2(
        self,
        rule_type: str,
        rule_spec: dict[str, Any],
        sample: dict[str, Any],
    ) -> bool:
        """对样本应用 spec 05 规则，判断是否命中

        Args:
            rule_type: 规则类型（regex/keyword/function/schema）
            rule_spec: 规则定义
            sample: 待检查的样本

        Returns:
            是否命中规则
        """
        import re

        # 提取匹配字段内容
        match_field = rule_spec.get("match_field", "content")
        text = str(sample.get(match_field, "") or sample.get("input", ""))

        try:
            if rule_type == "regex":
                # 正则规则：支持单个 pattern 或多个 patterns
                patterns = rule_spec.get("patterns", [])
                if not patterns and rule_spec.get("pattern"):
                    patterns = [rule_spec["pattern"]]
                flags_str = rule_spec.get("flags", "IGNORECASE")
                flags = re.IGNORECASE if "IGNORECASE" in flags_str.upper() else 0
                for pattern in patterns:
                    try:
                        if re.search(pattern, text, flags):
                            return True
                    except re.error:
                        continue

            elif rule_type == "keyword":
                # 关键词规则
                keywords = rule_spec.get("keywords", [])
                match_mode = rule_spec.get("match_mode", "any")
                case_sensitive = rule_spec.get("case_sensitive", False)
                if not case_sensitive:
                    text_lower = text.lower()
                    keywords = [k.lower() for k in keywords]
                else:
                    text_lower = text

                if match_mode == "any":
                    return any(kw in text_lower for kw in keywords)
                elif match_mode == "all":
                    return all(kw in text_lower for kw in keywords)

            elif rule_type == "function":
                # 函数规则：检查预注册函数名
                func_name = rule_spec.get("function_name", "")
                # 仅允许预注册函数，不允许任意执行
                _ALLOWED_FUNCTIONS = {"check_factuality", "check_permission", "check_tool_param_combination"}
                if func_name in _ALLOWED_FUNCTIONS:
                    # 简化：检查 forbidden_tools 是否在 expected_tools 中
                    if func_name == "check_permission":
                        context = sample.get("context", {})
                        permissions = context.get("permissions") or context.get("user_role")
                        if permissions and isinstance(permissions, str):
                            return permissions.lower() in ("restricted", "guest", "limited")
                    elif func_name == "check_factuality":
                        # 简化：有 safety_constraints 则认为可能命中
                        return bool(sample.get("safety_constraints"))
                return False

            elif rule_type == "schema":
                # Schema 规则：简化检查
                return False

        except Exception as e:
            logger.warning("应用 spec 05 规则异常: %s", e)
            return False

        return False

    async def _check_compatibility(
        self,
        candidate: Spec05Candidate,
        eval_suite: list[Fixture],
    ) -> float:
        """兼容性检查：确认规则不引入回归

        在完整评估套件上检查规则不会误伤正常用例。
        兼容性 = 未被误伤的用例数 / 总用例数

        Args:
            candidate: 候选规则
            eval_suite: 评估套件

        Returns:
            兼容性（0-1），无评估套件时返回 1.0
        """
        if not eval_suite:
            return 1.0

        total = len(eval_suite)
        unaffected = 0

        rule_spec = candidate.rule_spec or {}
        rule_type = candidate.rule_type.value if hasattr(candidate.rule_type, "value") else str(candidate.rule_type)

        for fixture in eval_suite:
            # 统一转换为字典格式，兼容 Fixture 对象和 dict
            fixture_dict = self._fixture_to_dict(fixture)
            # 正常用例不应被规则命中
            severity = str(fixture_dict.get("severity", "normal")).lower()
            if severity in ("adversarial", "edge"):
                # 对抗用例被命中是正常的，不影响兼容性
                unaffected += 1
                continue

            sample = {
                "fixture_id": fixture_dict.get("fixture_id", ""),
                "input": fixture_dict.get("input", ""),
                "expected_tools": fixture_dict.get("expected_tools", []),
                "context": fixture_dict.get("context", {}),
            }

            if not self._apply_rule_v2(rule_type, rule_spec, sample):
                unaffected += 1

        return unaffected / total if total > 0 else 1.0
