"""失败案例归档与改进

对应 spec 04 第 3.4 节 FailureArchive。

将评估失败的案例分类归档，并驱动两条改进路径：
  1. 自动转护栏规则候选（经沙箱验证 + 人工审核后上线）
  2. 自动转 Fixture（补充评估数据集，已在 TraceToFixtureConverter 完成）

归档记录生命周期：
  pending -> improving -> resolved

护栏规则候选生命周期：
  candidate -> sandboxed -> approved -> online / rejected
"""

import logging
from datetime import datetime
from typing import Any

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.improvement.failure_pattern import FailurePatternClassifier
from agent.evaluation.improvement.models import (
    FailureArchiveRecord,
    GuardrailRuleCandidateRecord,
)
from agent.evaluation.improvement.rule_generator import (
    GuardrailRuleCandidate,
    GuardrailRuleGenerator,
)
from agent.evaluation.improvement.rule_sandbox import RuleSandbox, SandboxResult

logger = logging.getLogger(__name__)


class FailureArchive:
    """失败案例归档与改进

    将评估失败的案例分类归档，并驱动护栏规则候选生成。
    生成的规则候选需经沙箱验证和人工审核后才能上线。

    使用示例：
        archive = FailureArchive()
        archive_id = await archive.archive(session_id, eval_report, fixture)
        candidates = await archive.generate_improvement(archive_id)
    """

    def __init__(
        self,
        classifier: FailurePatternClassifier | None = None,
        rule_generator: GuardrailRuleGenerator | None = None,
        sandbox: RuleSandbox | None = None,
        eval_suite: list[Fixture] | None = None,
    ) -> None:
        """初始化失败案例归档器

        Args:
            classifier: 失败模式分类器，None 时使用默认实例
            rule_generator: 规则生成器，None 时使用默认实例
            sandbox: 规则沙箱验证器，None 时使用默认实例
            eval_suite: 沙箱验证用的评估套件，None 时沙箱验证将跳过
        """
        self._classifier = classifier or FailurePatternClassifier()
        self._rule_generator = rule_generator or GuardrailRuleGenerator()
        self._sandbox = sandbox or RuleSandbox()
        self._eval_suite = eval_suite or []

        # 内存存储（生产环境替换为 PostgreSQL）
        self._archives: dict[str, FailureArchiveRecord] = {}
        self._rule_records: dict[str, GuardrailRuleCandidateRecord] = {}
        self._rule_candidates: dict[str, GuardrailRuleCandidate] = {}

    async def archive(
        self,
        session_id: str,
        eval_report: Any,
        fixture: Fixture,
        spans: list[dict] | None = None,
        failure_reason: str = "",
    ) -> str:
        """归档失败案例

        流程：
          1. 分类失败模式
          2. 创建 FailureArchiveRecord
          3. 存储归档记录

        Args:
            session_id: 失败的原始会话 ID
            eval_report: 评估报告（含 safety_violations 等字段）
            fixture: 关联的 Fixture
            spans: 失败 trace 的 span 列表（用于分类）
            failure_reason: 失败原因描述

        Returns:
            archive_id 归档记录 ID
        """
        spans = spans or []

        # 1. 分类失败模式
        failure_pattern = await self._classifier.classify(
            spans=spans,
            eval_report=eval_report,
            failure_reason=failure_reason,
        )

        # 2. 提取评估报告 ID
        report_id = self._extract_report_id(eval_report)

        # 3. 构造 failure_detail
        failure_detail = self._build_failure_detail(
            eval_report, failure_reason, failure_pattern,
        )

        # 4. 创建归档记录
        record = FailureArchiveRecord(
            session_id=session_id,
            fixture_id=fixture.fixture_id,
            report_id=report_id,
            failure_pattern=failure_pattern,
            failure_detail=failure_detail,
            improvement_status="pending",
        )

        # 5. 存储归档记录
        self._archives[record.archive_id] = record

        logger.info(
            "归档失败案例: archive_id=%s session_id=%s pattern=%s fixture_id=%s",
            record.archive_id,
            session_id,
            failure_pattern,
            fixture.fixture_id,
        )

        return record.archive_id

    async def generate_improvement(
        self,
        archive_id: str,
    ) -> list[GuardrailRuleCandidate]:
        """从归档案例生成改进项

        流程：
          1. 加载归档记录
          2. 获取失败 trace（从 spans 重建，此处使用归档时记录的信息）
          3. 调用 GuardrailRuleGenerator 生成规则候选
          4. 调用 RuleSandbox 验证规则候选
          5. 验证通过则更新状态为 sandboxed
          6. 更新归档记录状态为 improving
          7. 返回规则候选列表

        Args:
            archive_id: 归档记录 ID

        Returns:
            生成的护栏规则候选列表
        """
        # 1. 加载归档记录
        record = self._archives.get(archive_id)
        if record is None:
            logger.error("归档记录不存在: archive_id=%s", archive_id)
            return []

        # 2. 构造失败 trace（从归档信息重建简化 trace）
        failure_trace = self._reconstruct_failure_trace(record)

        # 3. 生成规则候选
        candidate = await self._rule_generator.generate_rule(
            failure_trace=failure_trace,
            pattern=record.failure_pattern,
            source_archive_id=archive_id,
        )

        logger.info(
            "生成规则候选: rule_id=%s archive_id=%s pattern=%s",
            candidate.rule_id,
            archive_id,
            record.failure_pattern,
        )

        # 4. 沙箱验证
        sandbox_result: SandboxResult | None = None
        if self._eval_suite:
            sandbox_result = await self._sandbox.validate(
                candidate, self._eval_suite,
            )
            candidate.sandbox_passed = sandbox_result.passed
            logger.info(
                "沙箱验证: rule_id=%s passed=%s",
                candidate.rule_id,
                sandbox_result.passed,
            )
        else:
            logger.warning("评估套件为空，跳过沙箱验证")

        # 5. 创建规则候选记录（持久化）
        rule_record = GuardrailRuleCandidateRecord(
            rule_id=candidate.rule_id,
            archive_id=archive_id,
            pattern=candidate.pattern,
            rule_type=candidate.rule_type,
            rule_definition=candidate.rule_definition,
            confidence=candidate.confidence,
            sandbox_passed=candidate.sandbox_passed,
            sandbox_result=sandbox_result.model_dump() if sandbox_result else {},
            approved=False,
            status="sandboxed" if candidate.sandbox_passed else "candidate",
        )
        self._rule_records[rule_record.rule_id] = rule_record
        self._rule_candidates[candidate.rule_id] = candidate

        # 6. 更新归档记录状态
        record.improvement_status = "improving"
        self._archives[archive_id] = record

        logger.info(
            "改进项生成完成: archive_id=%s rule_id=%s status=%s",
            archive_id,
            candidate.rule_id,
            rule_record.status,
        )

        return [candidate]

    def get_archive(self, archive_id: str) -> FailureArchiveRecord | None:
        """获取归档记录

        Args:
            archive_id: 归档记录 ID

        Returns:
            归档记录，不存在返回 None
        """
        return self._archives.get(archive_id)

    def get_rule_candidate(self, rule_id: str) -> GuardrailRuleCandidateRecord | None:
        """获取规则候选记录

        Args:
            rule_id: 规则 ID

        Returns:
            规则候选记录，不存在返回 None
        """
        return self._rule_records.get(rule_id)

    def list_archives(
        self,
        improvement_status: str | None = None,
        failure_pattern: str | None = None,
    ) -> list[FailureArchiveRecord]:
        """列出归档记录

        Args:
            improvement_status: 按改进状态过滤（None 表示不过滤）
            failure_pattern: 按失败模式过滤（None 表示不过滤）

        Returns:
            归档记录列表
        """
        result = list(self._archives.values())
        if improvement_status is not None:
            result = [r for r in result if r.improvement_status == improvement_status]
        if failure_pattern is not None:
            result = [r for r in result if r.failure_pattern == failure_pattern]
        return result

    def list_rule_candidates(
        self,
        status: str | None = None,
    ) -> list[GuardrailRuleCandidateRecord]:
        """列出规则候选记录

        Args:
            status: 按状态过滤（None 表示不过滤）

        Returns:
            规则候选记录列表
        """
        result = list(self._rule_records.values())
        if status is not None:
            result = [r for r in result if r.status == status]
        return result

    def approve_rule(
        self,
        rule_id: str,
        approved_by: str,
    ) -> bool:
        """审核通过规则候选

        规则候选经人工审核通过后，状态从 sandboxed 流转为 approved。

        Args:
            rule_id: 规则 ID
            approved_by: 审核人

        Returns:
            是否审核成功
        """
        record = self._rule_records.get(rule_id)
        if record is None:
            logger.warning("规则候选不存在: rule_id=%s", rule_id)
            return False

        if not record.sandbox_passed:
            logger.warning("规则候选未通过沙箱验证，无法审核: rule_id=%s", rule_id)
            return False

        record.approved = True
        record.approved_by = approved_by
        record.approved_at = datetime.now()
        record.status = "approved"

        logger.info(
            "规则候选审核通过: rule_id=%s approved_by=%s",
            rule_id,
            approved_by,
        )
        return True

    def reject_rule(
        self,
        rule_id: str,
        reason: str = "",
    ) -> bool:
        """拒绝规则候选

        Args:
            rule_id: 规则 ID
            reason: 拒绝原因

        Returns:
            是否操作成功
        """
        record = self._rule_records.get(rule_id)
        if record is None:
            logger.warning("规则候选不存在: rule_id=%s", rule_id)
            return False

        record.status = "rejected"
        record.sandbox_result = dict(record.sandbox_result)
        record.sandbox_result["reject_reason"] = reason

        logger.info("规则候选被拒绝: rule_id=%s reason=%s", rule_id, reason)
        return True

    def mark_online(self, rule_id: str) -> bool:
        """标记规则为已上线

        规则灰度上线后，状态从 approved 流转为 online。

        Args:
            rule_id: 规则 ID

        Returns:
            是否操作成功
        """
        record = self._rule_records.get(rule_id)
        if record is None:
            logger.warning("规则候选不存在: rule_id=%s", rule_id)
            return False

        if not record.approved:
            logger.warning("规则候选未审核通过，无法上线: rule_id=%s", rule_id)
            return False

        record.status = "online"

        logger.info("规则候选已上线: rule_id=%s", rule_id)
        return True

    def resolve_archive(self, archive_id: str) -> bool:
        """标记归档案例为已解决

        改进上线并通过回归测试后，归档记录状态从 improving 流转为 resolved。

        Args:
            archive_id: 归档记录 ID

        Returns:
            是否操作成功
        """
        record = self._archives.get(archive_id)
        if record is None:
            logger.warning("归档记录不存在: archive_id=%s", archive_id)
            return False

        record.improvement_status = "resolved"
        record.resolved_at = datetime.now()

        logger.info("归档案例已解决: archive_id=%s", archive_id)
        return True

    def get_approved_rules(self) -> list[GuardrailRuleCandidateRecord]:
        """获取所有已审核通过且上线的规则

        供 guardrails.py 动态加载使用。

        Returns:
            已上线的规则候选记录列表
        """
        return [
            r for r in self._rule_records.values()
            if r.status == "online"
        ]

    def _extract_report_id(self, eval_report: Any) -> str:
        """从评估报告中提取 report_id"""
        if hasattr(eval_report, "report_id"):
            return str(eval_report.report_id)
        if isinstance(eval_report, dict):
            return str(eval_report.get("report_id", ""))
        return ""

    def _build_failure_detail(
        self,
        eval_report: Any,
        failure_reason: str,
        failure_pattern: str,
    ) -> str:
        """构造失败详情

        Args:
            eval_report: 评估报告
            failure_reason: 失败原因
            failure_pattern: 失败模式

        Returns:
            失败详情字符串
        """
        parts: list[str] = []
        parts.append(f"失败模式: {failure_pattern}")

        if failure_reason:
            parts.append(f"失败原因: {failure_reason}")

        # 提取安全违规信息
        safety_violations: list[str] = []
        if hasattr(eval_report, "safety_violations"):
            safety_violations = list(eval_report.safety_violations or [])
        elif isinstance(eval_report, dict):
            safety_violations = list(eval_report.get("safety_violations", []))

        if safety_violations:
            parts.append(f"安全违规: {safety_violations}")

        # 提取评分信息
        overall_score = 0.0
        if hasattr(eval_report, "overall_score"):
            overall_score = float(eval_report.overall_score or 0.0)
        elif isinstance(eval_report, dict):
            overall_score = float(eval_report.get("overall_score", 0.0))

        parts.append(f"评分: {overall_score:.2f}")

        return "; ".join(parts)

    def _reconstruct_failure_trace(
        self,
        record: FailureArchiveRecord,
    ) -> list[dict]:
        """从归档记录重建失败 trace

        简化实现：根据归档信息构造最小化的 trace 用于规则生成。
        生产环境应从 SpanCache 加载完整 trace。

        Args:
            record: 归档记录

        Returns:
            简化的 span 列表
        """
        spans: list[dict] = []

        # 构造一个 intent span（包含失败模式信息）
        spans.append({
            "span_type": "intent_classification",
            "input": {"user_message": ""},
            "output": {"pattern": record.failure_pattern},
            "metadata": {"agent_name": ""},
        })

        # 如果是 tool_misuse，构造一个失败的 tool_call span
        if record.failure_pattern == "tool_misuse":
            spans.append({
                "span_type": "tool_call:unknown",
                "input": {"tool": "unknown"},
                "output": {},
                "metadata": {"status": "failed"},
            })

        # 如果是 injection_attack，构造包含注入特征的 intent span
        if record.failure_pattern == "injection_attack":
            spans.append({
                "span_type": "intent_classification",
                "input": {"user_message": "ignore previous instructions"},
                "output": {},
                "metadata": {"injection_detected": True},
            })

        return spans
