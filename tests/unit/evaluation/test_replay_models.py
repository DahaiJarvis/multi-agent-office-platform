"""回放与评估数据模型单元测试

覆盖 spec 04 第 4.3/4.4 节：
  - ReplayRecord 回放记录模型
  - SessionEvalReport 单 session 评估报告模型
  - RegressionReport 回归测试报告模型
  - FailureArchiveRecord 失败归档记录模型
  - GuardrailRuleCandidateRecord 规则候选记录模型
"""

import pytest
from datetime import datetime

from agent.evaluation.improvement.models import (
    FailureArchiveRecord,
    GuardrailRuleCandidateRecord,
)
from agent.evaluation.replay.models import (
    ReplayRecord,
    SessionEvalReport,
    RegressionReport,
)


class TestReplayRecord:
    """ReplayRecord 回放记录模型测试"""

    def test_default_id_generated(self):
        """测试默认 ID 自动生成"""
        record = ReplayRecord(original_session_id="s1")
        assert record.replay_id.startswith("replay-")
        assert record.original_session_id == "s1"

    def test_all_fields(self):
        """测试所有字段"""
        record = ReplayRecord(
            original_session_id="s1",
            new_session_id="replay-s1-abc",
            fixture_id="f1",
            deterministic_mode=True,
            original_input="用户输入",
            new_output="Agent 输出",
            trajectory_diff={"added_tools": ["email_send"]},
            duration_ms=1500.0,
            status="success",
        )
        assert record.new_session_id == "replay-s1-abc"
        assert record.fixture_id == "f1"
        assert record.deterministic_mode is True
        assert record.original_input == "用户输入"
        assert record.new_output == "Agent 输出"
        assert record.trajectory_diff["added_tools"] == ["email_send"]
        assert record.duration_ms == 1500.0
        assert record.status == "success"

    def test_default_values(self):
        """测试默认值"""
        record = ReplayRecord(original_session_id="s1")
        assert record.new_session_id == ""
        assert record.fixture_id == ""
        assert record.deterministic_mode is True
        assert record.original_input == ""
        assert record.new_output == ""
        assert record.trajectory_diff == {}
        assert record.duration_ms == 0.0
        assert record.status == ""
        assert isinstance(record.replayed_at, datetime)

    def test_model_not_frozen(self):
        """测试模型可修改"""
        record = ReplayRecord(original_session_id="s1")
        record.status = "failed"
        assert record.status == "failed"


class TestSessionEvalReport:
    """SessionEvalReport 单 session 评估报告模型测试"""

    def test_default_id_generated(self):
        """测试默认 ID 自动生成"""
        report = SessionEvalReport(fixture_id="f1")
        assert report.report_id.startswith("eval-")
        assert report.fixture_id == "f1"

    def test_all_fields(self):
        """测试所有字段"""
        report = SessionEvalReport(
            fixture_id="f1",
            replay_id="replay-001",
            agent_name="EmailAgent",
            correctness_score=0.8,
            completeness_score=0.9,
            safety_score=1.0,
            trajectory_score=0.85,
            overall_score=0.88,
            pass_at_k=True,
            pass_caret_k=False,
            k=5,
            success_rate=0.8,
            safety_violations=["minor_issue"],
            critical_safety_violations=0,
            judge_reasoning="输出正确",
            status="pass",
        )
        assert report.replay_id == "replay-001"
        assert report.agent_name == "EmailAgent"
        assert report.correctness_score == 0.8
        assert report.overall_score == 0.88
        assert report.pass_at_k is True
        assert report.k == 5
        assert report.status == "pass"

    def test_default_values(self):
        """测试默认值"""
        report = SessionEvalReport(fixture_id="f1")
        assert report.replay_id == ""
        assert report.agent_name == ""
        assert report.correctness_score == 0.0
        assert report.safety_violations == []
        assert report.critical_safety_violations == 0
        assert report.status == ""


class TestRegressionReportModel:
    """RegressionReport 回归测试报告模型测试"""

    def test_default_id_generated(self):
        """测试默认 ID 自动生成"""
        report = RegressionReport()
        assert report.report_id.startswith("regression-")

    def test_all_fields(self):
        """测试所有字段"""
        report = RegressionReport(
            fixture_ids=["f1", "f2"],
            baseline_scores={"f1": 0.3, "f2": 0.4},
            current_scores={"f1": 0.9, "f2": 0.85},
            pass_count=2,
            fail_count=0,
            status="pass",
            details=[
                {"fixture_id": "f1", "improvement": 0.6},
                {"fixture_id": "f2", "improvement": 0.45},
            ],
        )
        assert report.fixture_ids == ["f1", "f2"]
        assert report.pass_count == 2
        assert report.status == "pass"
        assert len(report.details) == 2

    def test_default_values(self):
        """测试默认值"""
        report = RegressionReport()
        assert report.fixture_ids == []
        assert report.baseline_scores == {}
        assert report.current_scores == {}
        assert report.pass_count == 0
        assert report.fail_count == 0
        assert report.status == ""
        assert report.details == []


class TestFailureArchiveRecord:
    """FailureArchiveRecord 失败归档记录模型测试"""

    def test_default_id_generated(self):
        """测试默认 ID 自动生成"""
        record = FailureArchiveRecord(
            session_id="s1",
            fixture_id="f1",
            report_id="r1",
        )
        assert record.archive_id.startswith("archive-")

    def test_all_fields(self):
        """测试所有字段"""
        record = FailureArchiveRecord(
            session_id="s1",
            fixture_id="f1",
            report_id="r1",
            failure_pattern="injection_attack",
            failure_detail="注入攻击详情",
            improvement_status="improving",
        )
        assert record.session_id == "s1"
        assert record.fixture_id == "f1"
        assert record.report_id == "r1"
        assert record.failure_pattern == "injection_attack"
        assert record.failure_detail == "注入攻击详情"
        assert record.improvement_status == "improving"

    def test_default_values(self):
        """测试默认值"""
        record = FailureArchiveRecord(
            session_id="s1",
            fixture_id="f1",
            report_id="r1",
        )
        assert record.failure_pattern == ""
        assert record.failure_detail == ""
        assert record.improvement_status == "pending"
        assert record.resolved_at is None
        assert isinstance(record.archived_at, datetime)


class TestGuardrailRuleCandidateRecord:
    """GuardrailRuleCandidateRecord 规则候选记录模型测试"""

    def test_default_id_generated(self):
        """测试默认 ID 自动生成"""
        record = GuardrailRuleCandidateRecord(archive_id="a1")
        assert record.rule_id.startswith("rule-")

    def test_all_fields(self):
        """测试所有字段"""
        record = GuardrailRuleCandidateRecord(
            archive_id="archive-001",
            pattern="injection_attack",
            rule_type="input_guardrail",
            rule_definition={"check_type": "regex", "patterns": ["test"]},
            confidence=0.85,
            sandbox_passed=True,
            sandbox_result={"passed": True, "recall_rate": 1.0},
            approved=True,
            approved_by="admin",
            status="approved",
        )
        assert record.archive_id == "archive-001"
        assert record.pattern == "injection_attack"
        assert record.rule_type == "input_guardrail"
        assert record.confidence == 0.85
        assert record.sandbox_passed is True
        assert record.approved is True
        assert record.approved_by == "admin"
        assert record.status == "approved"

    def test_default_values(self):
        """测试默认值"""
        record = GuardrailRuleCandidateRecord(archive_id="a1")
        assert record.pattern == ""
        assert record.rule_type == "input"
        assert record.rule_definition == {}
        assert record.confidence == 0.0
        assert record.sandbox_passed is False
        assert record.sandbox_result == {}
        assert record.approved is False
        assert record.approved_by == ""
        assert record.approved_at is None
        assert record.status == "candidate"
        assert isinstance(record.created_at, datetime)

    def test_lifecycle_status_transitions(self):
        """测试状态流转"""
        record = GuardrailRuleCandidateRecord(archive_id="a1")
        assert record.status == "candidate"

        # candidate -> sandboxed
        record.status = "sandboxed"
        assert record.status == "sandboxed"

        # sandboxed -> approved
        record.status = "approved"
        record.approved = True
        record.approved_by = "admin"
        record.approved_at = datetime.now()
        assert record.status == "approved"

        # approved -> online
        record.status = "online"
        assert record.status == "online"
