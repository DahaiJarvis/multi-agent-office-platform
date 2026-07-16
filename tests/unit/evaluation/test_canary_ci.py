"""金丝雀管理与 CI 门禁单元测试

覆盖 spec 文档 3.6 节 CanaryManager 与 3.7 节/第十一章 CIGate 接口。
"""

import pytest

from agent.evaluation.canaries.canary_manager import CanaryManager
from agent.evaluation.canaries.ci_gate import CIGate
from agent.evaluation.runners.harness_runner import EvalReport
from agent.evaluation.fixtures.fixture_schema import Fixture


class TestCanaryManager:
    """CanaryManager 金丝雀管理器测试"""

    def test_get_fast_suite(self):
        """测试 Fast 套件（canary 标签且非 adversarial）"""
        manager = CanaryManager()
        fast_suite = manager.get_fast_suite()
        # spec 要求 10-15 个 fixture
        assert len(fast_suite) >= 10
        for fixture in fast_suite:
            assert fixture.is_canary()
            assert not fixture.is_adversarial()

    def test_get_slow_suite_includes_adversarial(self):
        """测试 Slow 套件包含 adversarial 场景"""
        manager = CanaryManager()
        slow_suite = manager.get_slow_suite()
        assert len(slow_suite) >= 12
        adversarial_fixtures = [f for f in slow_suite if f.is_adversarial()]
        assert len(adversarial_fixtures) >= 2

    def test_slow_suite_contains_fast_suite(self):
        """测试 Slow 套件包含 Fast 套件全部 fixture"""
        manager = CanaryManager()
        fast_suite = manager.get_fast_suite()
        slow_suite = manager.get_slow_suite()
        fast_ids = {f.fixture_id for f in fast_suite}
        slow_ids = {f.fixture_id for f in slow_suite}
        assert fast_ids.issubset(slow_ids)

    def test_add_canary_tag(self, sample_fixture):
        """测试 add_canary 添加 canary 标签"""
        # sample_fixture 已有 canary 标签，先移除
        sample_fixture.tags.remove("canary")
        manager = CanaryManager()
        manager.add_canary(sample_fixture)
        assert "canary" in sample_fixture.tags

    def test_add_canary_no_duplicate(self, sample_fixture):
        """测试 add_canary 不重复添加标签"""
        manager = CanaryManager()
        initial_count = sample_fixture.tags.count("canary")
        manager.add_canary(sample_fixture)
        assert sample_fixture.tags.count("canary") == initial_count

    def test_get_fixture_by_id(self):
        """测试按 ID 获取 fixture"""
        manager = CanaryManager()
        fixture = manager.get_fixture_by_id("email_query_001")
        assert fixture is not None
        assert fixture.fixture_id == "email_query_001"
        assert fixture.category == "email"

    def test_get_fixture_by_id_not_found(self):
        """测试获取不存在的 fixture 返回 None"""
        manager = CanaryManager()
        assert manager.get_fixture_by_id("nonexistent_xyz") is None

    def test_list_canaries(self):
        """测试列出金丝雀摘要"""
        manager = CanaryManager()
        summaries = manager.list_canaries()
        assert len(summaries) >= 10
        for s in summaries:
            assert "fixture_id" in s
            assert "category" in s


class TestCIGate:
    """CIGate CI 门禁判断器测试"""

    @pytest.fixture
    def gate(self):
        return CIGate()

    def _make_report(
        self,
        pass_caret_5_rate: float = 1.0,
        critical_safety_violations: int = 0,
        cost_variance_ratio: float = 0.0,
        total: int = 10,
        pass_count: int = 10,
    ) -> EvalReport:
        """构造评估报告"""
        return EvalReport(
            suite_name="fast",
            total_fixtures=total,
            pass_count=pass_count,
            fail_count=total - pass_count,
            pass_at_k_rate=pass_caret_5_rate,
            pass_caret_5_rate=pass_caret_5_rate,
            critical_safety_violations=critical_safety_violations,
            safety_violations=critical_safety_violations,
            cost_variance_ratio=cost_variance_ratio,
        )

    def test_gate_pass_all_green(self, gate):
        """测试全部指标达标时门禁通过"""
        report = self._make_report(pass_caret_5_rate=1.0)
        blocked, reason = gate.should_block(report)
        assert blocked is False
        assert "通过" in reason

    def test_gate_block_low_pass_rate(self, gate):
        """测试 pass^5 通过率低于阈值时阻断"""
        report = self._make_report(pass_caret_5_rate=0.80, pass_count=8, total=10)
        blocked, reason = gate.should_block(report)
        assert blocked is True
        assert "pass^5" in reason

    def test_gate_block_critical_safety(self, gate):
        """测试 critical 安全违规时阻断"""
        report = self._make_report(critical_safety_violations=1)
        blocked, reason = gate.should_block(report)
        assert blocked is True
        assert "critical" in reason

    def test_gate_block_high_cost_variance(self, gate):
        """测试成本方差超阈值时阻断"""
        report = self._make_report(cost_variance_ratio=0.50)
        blocked, reason = gate.should_block(report)
        assert blocked is True
        assert "成本方差" in reason

    def test_gate_custom_thresholds(self):
        """测试自定义阈值覆盖默认值"""
        # 放宽 pass^5 阈值到 0.7
        gate = CIGate(pass_caret_5_threshold=0.7)
        report = self._make_report(pass_caret_5_rate=0.8, pass_count=8, total=10)
        blocked, _ = gate.should_block(report)
        assert blocked is False  # 0.8 >= 0.7 通过

    def test_gate_default_thresholds_match_spec(self):
        """测试默认阈值符合 spec 第十一章定义"""
        assert CIGate.BLOCK_THRESHOLDS["pass_caret_5_rate"] == 0.95
        assert CIGate.BLOCK_THRESHOLDS["critical_safety_violations"] == 0
        assert CIGate.BLOCK_THRESHOLDS["cost_variance_ratio"] == 0.30

    def test_gate_boundary_pass_rate_95(self, gate):
        """测试 pass^5 通过率恰好 95% 时不阻断（边界值）"""
        report = self._make_report(pass_caret_5_rate=0.95, pass_count=95, total=100)
        blocked, _ = gate.should_block(report)
        assert blocked is False

    def test_gate_boundary_pass_rate_below_95(self, gate):
        """测试 pass^5 通过率 94.9% 时阻断"""
        report = self._make_report(pass_caret_5_rate=0.949, pass_count=94, total=100)
        blocked, _ = gate.should_block(report)
        assert blocked is True

    def test_gate_boundary_cost_variance_30(self, gate):
        """测试成本方差恰好 30% 时不阻断（边界值）"""
        report = self._make_report(cost_variance_ratio=0.30)
        blocked, _ = gate.should_block(report)
        assert blocked is False

    def test_format_report_contains_core_metrics(self, gate):
        """测试格式化报告包含核心指标"""
        report = self._make_report()
        md = gate.format_report(report)
        assert "Agent 评估报告" in md
        assert "pass^5 通过率" in md
        assert "安全违规" in md
        assert "门禁结果" in md

    def test_format_report_lists_failed_fixtures(self, gate):
        """测试格式化报告列出失败 fixture"""
        report = self._make_report(pass_caret_5_rate=0.8, pass_count=8, total=10)
        report.failed_fixture_ids = ["f1", "f2"]
        md = gate.format_report(report)
        assert "失败 Fixture" in md
        assert "f1" in md
        assert "f2" in md
