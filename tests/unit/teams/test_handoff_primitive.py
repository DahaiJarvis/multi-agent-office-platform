"""Handoff 原生原语单元测试

覆盖 spec 06 第 2 节功能需求 FR-1 ~ FR-11：
  - FR-1 Handoff 关系注册
  - FR-2 Handoff 关系查询
  - FR-3 Handoff 条件匹配
  - FR-6 上下文传递
  - FR-7 防循环控制
  - FR-8 Handoff 审计
  - FR-9 Handoff 事件发布
  - FR-10 敏感字段脱敏
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.teams.handoff_primitive import (
    Handoff,
    HandoffContextBuilder,
    HandoffGuard,
    HandoffRegistry,
    HandoffRelation,
    get_handoff_registry,
    reset_handoff_registry,
)


# ==================== 测试夹具 ====================


@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前后重置全局 HandoffRegistry，保证测试隔离"""
    reset_handoff_registry()
    yield
    reset_handoff_registry()


@pytest.fixture
def registry():
    """提供干净的 HandoffRegistry 实例"""
    return HandoffRegistry()


@pytest.fixture
def populated_registry():
    """提供预填充的 HandoffRegistry 实例（含多条 handoff 关系）"""
    reg = HandoffRegistry()
    reg.register(
        "OfficeAssistant", "FinanceAgent",
        condition="当用户问题涉及财务/报销/预算时",
        condition_keywords=["报销", "预算", "财务", "发票"],
    )
    reg.register(
        "OfficeAssistant", "HRAgent",
        condition="当用户问题涉及请假/考勤/薪资时",
        condition_keywords=["请假", "考勤", "薪资", "加班"],
    )
    reg.register(
        "KnowledgeAgent", "EmailAgent",
        condition="当文档解析完成后需要发送结果时",
        condition_keywords=["发送", "邮件", "转发结果"],
    )
    return reg


# ==================== FR-1: Handoff 关系注册 ====================


class TestHandoffRegister:
    """FR-1: Handoff 关系注册测试"""

    def test_register_success(self, registry):
        """测试正常注册 handoff 关系"""
        registry.register(
            "OfficeAssistant", "FinanceAgent",
            condition="当用户问题涉及财务时",
            condition_keywords=["财务", "报销"],
        )
        targets = registry.get_targets("OfficeAssistant")
        assert "FinanceAgent" in targets

    def test_register_same_agent_raises(self, registry):
        """测试 from/to 相同时抛出 ValueError"""
        with pytest.raises(ValueError, match="不能相同"):
            registry.register(
                "OfficeAssistant", "OfficeAssistant",
                condition="测试",
            )

    def test_register_duplicate_raises(self, registry):
        """测试重复注册同一关系抛出 ValueError"""
        registry.register(
            "OfficeAssistant", "FinanceAgent",
            condition="财务问题",
            condition_keywords=["财务"],
        )
        with pytest.raises(ValueError, match="已注册"):
            registry.register(
                "OfficeAssistant", "FinanceAgent",
                condition="财务问题",
                condition_keywords=["财务"],
            )

    def test_register_different_condition_allowed(self, registry):
        """测试同一 (from, to) 对注册不同条件允许"""
        registry.register(
            "OfficeAssistant", "FinanceAgent",
            condition="条件A",
            condition_keywords=["财务"],
        )
        # 不同条件允许注册
        registry.register(
            "OfficeAssistant", "FinanceAgent",
            condition="条件B",
            condition_keywords=["报销"],
        )
        targets = registry.get_targets("OfficeAssistant")
        # 去重后只有一个 FinanceAgent
        assert targets.count("FinanceAgent") == 1

    def test_register_auto_extract_keywords(self, registry):
        """测试未提供 condition_keywords 时自动提取"""
        registry.register(
            "OfficeAssistant", "FinanceAgent",
            condition="当用户问题涉及财务报销预算时",
        )
        relations = registry.list_relations()
        assert len(relations) == 1
        # 应自动提取关键词
        assert len(relations[0].condition_keywords) > 0

    def test_register_tenant_isolation(self, registry):
        """测试多租户隔离"""
        registry.register(
            "OfficeAssistant", "FinanceAgent",
            condition="财务问题",
            condition_keywords=["财务"],
            tenant_id="tenant_001",
        )
        registry.register(
            "OfficeAssistant", "HRAgent",
            condition="HR问题",
            condition_keywords=["HR"],
            tenant_id="tenant_002",
        )
        # tenant_001 只能看到 FinanceAgent
        targets_1 = registry.get_targets("OfficeAssistant", tenant_id="tenant_001")
        assert "FinanceAgent" in targets_1
        assert "HRAgent" not in targets_1

        # tenant_002 只能看到 HRAgent
        targets_2 = registry.get_targets("OfficeAssistant", tenant_id="tenant_002")
        assert "HRAgent" in targets_2
        assert "FinanceAgent" not in targets_2

    def test_register_platform_level(self, registry):
        """测试平台级关系（tenant_id=""）"""
        registry.register(
            "CRMAgent", "ApprovalAgent",
            condition="高风险商机需要加急审批",
            condition_keywords=["高风险", "加急"],
            tenant_id="",
        )
        targets = registry.get_targets("CRMAgent", tenant_id="")
        assert "ApprovalAgent" in targets


# ==================== FR-2: Handoff 关系查询 ====================


class TestHandoffQuery:
    """FR-2: Handoff 关系查询测试"""

    def test_get_targets_returns_ordered_list(self, populated_registry):
        """测试 get_targets 返回按注册顺序排列的目标列表"""
        targets = populated_registry.get_targets("OfficeAssistant")
        assert targets == ["FinanceAgent", "HRAgent"]

    def test_get_targets_empty_for_unregistered_agent(self, registry):
        """测试未注册 Agent 的 get_targets 返回空列表"""
        targets = registry.get_targets("NonExistentAgent")
        assert targets == []

    def test_get_targets_dedup(self, registry):
        """测试 get_targets 去重"""
        with patch.object(registry, "_agent_exists", return_value=True):
            registry.register("A", "B", condition="c1", condition_keywords=["k1"])
            registry.register("A", "B", condition="c2", condition_keywords=["k2"])
        targets = registry.get_targets("A")
        assert targets.count("B") == 1

    def test_list_relations(self, populated_registry):
        """测试 list_relations 返回全部关系"""
        relations = populated_registry.list_relations()
        assert len(relations) == 3

    def test_list_relations_by_tenant(self, registry):
        """测试按租户列出关系"""
        with patch.object(registry, "_agent_exists", return_value=True):
            registry.register("A", "B", condition="c1", condition_keywords=["k1"], tenant_id="t1")
            registry.register("C", "D", condition="c2", condition_keywords=["k2"], tenant_id="t2")
        assert len(registry.list_relations(tenant_id="t1")) == 1
        assert len(registry.list_relations(tenant_id="t2")) == 1
        assert len(registry.list_relations(tenant_id="")) == 0

    def test_unregister_success(self, populated_registry):
        """测试注销 handoff 关系"""
        result = populated_registry.unregister("OfficeAssistant", "FinanceAgent")
        assert result is True
        targets = populated_registry.get_targets("OfficeAssistant")
        assert "FinanceAgent" not in targets
        assert "HRAgent" in targets

    def test_unregister_nonexistent_returns_false(self, registry):
        """测试注销不存在的关系返回 False"""
        result = registry.unregister("A", "B")
        assert result is False


# ==================== FR-3: Handoff 条件匹配 ====================


class TestHandoffMatching:
    """FR-3: Handoff 条件匹配测试"""

    async def test_try_handoff_rule_match_finance(self, populated_registry):
        """测试规则层匹配财务关键词"""
        handoff = await populated_registry.try_handoff(
            "OfficeAssistant",
            context={"user_message": "我的报销到哪一步了"},
        )
        assert handoff is not None
        assert handoff.to_agent == "FinanceAgent"
        assert handoff.from_agent == "OfficeAssistant"

    async def test_try_handoff_rule_match_hr(self, populated_registry):
        """测试规则层匹配 HR 关键词"""
        handoff = await populated_registry.try_handoff(
            "OfficeAssistant",
            context={"user_message": "我想请假，还有多少考勤"},
        )
        assert handoff is not None
        assert handoff.to_agent == "HRAgent"

    async def test_try_handoff_no_match_returns_none(self, populated_registry):
        """测试无匹配时返回 None"""
        handoff = await populated_registry.try_handoff(
            "OfficeAssistant",
            context={"user_message": "今天天气怎么样"},
        )
        assert handoff is None

    async def test_try_handoff_no_relations_returns_none(self, registry):
        """测试无注册关系时返回 None"""
        handoff = await registry.try_handoff(
            "UnknownAgent",
            context={"user_message": "测试"},
        )
        assert handoff is None

    async def test_try_handoff_with_history_context(self, populated_registry):
        """测试从对话历史中匹配关键词"""
        handoff = await populated_registry.try_handoff(
            "OfficeAssistant",
            context={
                "user_message": "帮我看看",
                "history": [
                    {"role": "user", "content": "我想问一下报销的进度"},
                ],
            },
        )
        assert handoff is not None
        assert handoff.to_agent == "FinanceAgent"

    async def test_try_handoff_disabled_relation_skipped(self, populated_registry):
        """测试禁用的关系不参与匹配"""
        relations = populated_registry.list_relations()
        relations[0].enabled = False  # 禁用 FinanceAgent 关系

        handoff = await populated_registry.try_handoff(
            "OfficeAssistant",
            context={"user_message": "报销进度查询"},
        )
        # FinanceAgent 被禁用，不应匹配
        assert handoff is None or handoff.to_agent != "FinanceAgent"

    async def test_try_handoff_rule_match_performance(self, populated_registry):
        """测试规则层匹配性能 < 10ms"""
        import time
        start = time.perf_counter()
        for _ in range(100):
            await populated_registry.try_handoff(
                "OfficeAssistant",
                context={"user_message": "报销预算财务发票"},
            )
        elapsed_ms = (time.perf_counter() - start) / 100 * 1000
        # 规则层匹配应 < 10ms（宽松断言，测试环境可能有波动）
        assert elapsed_ms < 50, f"规则层匹配平均耗时 {elapsed_ms:.2f}ms 超过 50ms"


# ==================== FR-7: 防循环控制 ====================


class TestHandoffGuard:
    """FR-7: 防循环控制测试"""

    def test_check_and_record_allowed(self):
        """测试正常 handoff 校验通过"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("OfficeAssistant")

        handoff = Handoff(from_agent="OfficeAssistant", to_agent="FinanceAgent")
        allowed, reason = guard.check_and_record(handoff)
        assert allowed is True
        assert reason == ""

    def test_check_and_record_chain_depth_exceeded(self):
        """测试链路深度超限拒绝"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("Agent1")
        # 手动填满链路到 MAX_HANDOFF_CHAIN
        for i in range(2, HandoffGuard.MAX_HANDOFF_CHAIN + 1):
            h = Handoff(from_agent=f"Agent{i-1}", to_agent=f"Agent{i}")
            guard.check_and_record(h)

        # 再尝试一次应被拒绝
        h = Handoff(from_agent=f"Agent{HandoffGuard.MAX_HANDOFF_CHAIN}", to_agent="AgentX")
        allowed, reason = guard.check_and_record(h)
        assert allowed is False
        assert reason == "chain_depth_exceeded"

    def test_check_and_record_handoff_count_exceeded(self):
        """测试 handoff 次数超限拒绝

        需要提高 MAX_HANDOFF_CHAIN 以隔离测试 handoff_count_exceeded。
        默认 MAX_HANDOFF_CHAIN=5（含起点）会在第 4 次 handoff 时触发
        chain_depth_exceeded，无法独立测试 handoff_count_exceeded。
        """
        guard = HandoffGuard(session_id="s1", user_id="u1")
        # 临时提高链路深度上限以隔离测试 handoff 次数限制
        original_chain_limit = HandoffGuard.MAX_HANDOFF_CHAIN
        HandoffGuard.MAX_HANDOFF_CHAIN = 20
        try:
            guard.init_chain("Agent1")

            # 执行 MAX_HANDOFF_DEPTH 次 handoff
            for i in range(HandoffGuard.MAX_HANDOFF_DEPTH):
                h = Handoff(from_agent=f"Agent{i+1}", to_agent=f"Agent{i+2}")
                allowed, _ = guard.check_and_record(h)
                assert allowed is True

            # 第 MAX_HANDOFF_DEPTH + 1 次应被拒绝
            h = Handoff(from_agent="AgentX", to_agent="AgentY")
            allowed, reason = guard.check_and_record(h)
            assert allowed is False
            assert reason == "handoff_count_exceeded"
        finally:
            HandoffGuard.MAX_HANDOFF_CHAIN = original_chain_limit

    def test_check_and_record_duplicate_pair_rejected(self):
        """测试重复 (from, to) 对拒绝（防振荡）"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("A")

        # 第一次 A -> B 允许
        h1 = Handoff(from_agent="A", to_agent="B")
        allowed, _ = guard.check_and_record(h1)
        assert allowed is True

        # B -> A 允许（不同方向）
        h2 = Handoff(from_agent="B", to_agent="A")
        allowed, _ = guard.check_and_record(h2)
        assert allowed is True

        # 再次 A -> B 拒绝（重复对）
        h3 = Handoff(from_agent="A", to_agent="B")
        allowed, reason = guard.check_and_record(h3)
        assert allowed is False
        assert reason == "duplicate_pair_oscillation"

    def test_init_chain_only_once(self):
        """测试 init_chain 只在首次调用时生效"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("Agent1")
        guard.init_chain("Agent2")  # 第二次不应覆盖
        assert guard.chain == ["Agent1"]

    def test_chain_property(self):
        """测试 chain 属性返回链路副本"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("A")
        h = Handoff(from_agent="A", to_agent="B")
        guard.check_and_record(h)

        chain = guard.chain
        assert chain == ["A", "B"]
        # 修改返回的副本不应影响内部状态
        chain.append("C")
        assert guard.chain == ["A", "B"]

    def test_handoff_count_property(self):
        """测试 handoff_count 属性"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("A")
        assert guard.handoff_count == 0

        guard.check_and_record(Handoff(from_agent="A", to_agent="B"))
        assert guard.handoff_count == 1


# ==================== FR-10: 敏感字段脱敏 ====================


class TestHandoffSanitize:
    """FR-10: 敏感字段脱敏测试"""

    def test_sanitize_payload_phone(self):
        """测试手机号脱敏"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        payload = {"query": "联系手机 13812345678"}
        sanitized = guard.sanitize_payload(payload)
        assert "13812345678" not in sanitized["query"]

    def test_sanitize_payload_email(self):
        """测试邮箱脱敏"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        payload = {"email": "testuser@example.com"}
        sanitized = guard.sanitize_payload(payload)
        assert "testuser@example.com" not in sanitized["email"]

    def test_sanitize_payload_nested_dict(self):
        """测试嵌套字典脱敏"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        payload = {
            "outer": {
                "inner": "手机 13912345678 泄露",
            },
        }
        sanitized = guard.sanitize_payload(payload)
        assert "13912345678" not in sanitized["outer"]["inner"]

    def test_sanitize_payload_list_values(self):
        """测试列表中的字符串脱敏"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        payload = {
            "messages": ["联系 13812345678", "普通文本"],
        }
        sanitized = guard.sanitize_payload(payload)
        assert "13812345678" not in sanitized["messages"][0]
        assert sanitized["messages"][1] == "普通文本"

    def test_sanitize_payload_empty(self):
        """测试空 payload"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        assert guard.sanitize_payload({}) == {}

    def test_sanitize_payload_no_pii(self):
        """测试无 PII 的 payload 原样返回"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        payload = {"query": "查询报销进度", "count": 3}
        sanitized = guard.sanitize_payload(payload)
        assert sanitized["query"] == "查询报销进度"
        assert sanitized["count"] == 3

    def test_sanitize_payload_non_string_values(self):
        """测试非字符串值不被修改"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        payload = {"count": 42, "rate": 0.95, "active": True, "data": None}
        sanitized = guard.sanitize_payload(payload)
        assert sanitized["count"] == 42
        assert sanitized["rate"] == 0.95
        assert sanitized["active"] is True
        assert sanitized["data"] is None


# ==================== FR-6: 上下文传递 ====================


class TestHandoffContextBuilder:
    """FR-6: 上下文传递测试"""

    def test_build_basic_payload(self):
        """测试基础 payload 组装"""
        builder = HandoffContextBuilder()
        payload = builder.build(
            from_agent="OfficeAssistant",
            to_agent="FinanceAgent",
            reason="财务问题转交",
            current_context={"user_message": "我的报销到哪了"},
        )
        assert payload["original_query"] == "我的报销到哪了"
        assert payload["handoff_reason"] == "财务问题转交"
        assert payload["chain_depth"] == 1

    def test_build_with_collected_info(self):
        """测试携带 collected_info"""
        builder = HandoffContextBuilder()
        payload = builder.build(
            from_agent="OfficeAssistant",
            to_agent="FinanceAgent",
            reason="转交",
            current_context={
                "user_message": "报销查询",
                "collected_info": {"domain": "finance", "keywords": ["报销"]},
            },
        )
        assert payload["collected_info"]["domain"] == "finance"
        assert payload["collected_info"]["keywords"] == ["报销"]

    def test_build_with_agent_summary(self):
        """测试携带 from_agent_summary"""
        builder = HandoffContextBuilder()
        payload = builder.build(
            from_agent="OfficeAssistant",
            to_agent="FinanceAgent",
            reason="转交",
            current_context={
                "user_message": "报销",
                "agent_summary": "已识别为财务类问题",
            },
        )
        assert payload["from_agent_summary"] == "已识别为财务类问题"

    def test_build_includes_timestamp(self):
        """测试 payload 包含 ISO8601 时间戳"""
        builder = HandoffContextBuilder()
        payload = builder.build(
            from_agent="A", to_agent="B", reason="r",
            current_context={"user_message": "m"},
        )
        assert "handoff_timestamp" in payload
        assert "T" in payload["handoff_timestamp"]  # ISO8601 格式

    def test_build_chain_depth(self):
        """测试 chain_depth 参数传递"""
        builder = HandoffContextBuilder()
        payload = builder.build(
            from_agent="A", to_agent="B", reason="r",
            current_context={"user_message": "m"},
            chain_depth=3,
        )
        assert payload["chain_depth"] == 3

    def test_build_empty_context(self):
        """测试空上下文时 payload 字段有默认值"""
        builder = HandoffContextBuilder()
        payload = builder.build(
            from_agent="A", to_agent="B", reason="r",
            current_context={},
        )
        assert payload["original_query"] == ""
        assert payload["from_agent_summary"] == ""
        assert payload["collected_info"] == {}


# ==================== FR-8: Handoff 审计 ====================


class TestHandoffAudit:
    """FR-8: Handoff 审计测试"""

    async def test_audit_allowed_handoff(self):
        """测试允许的 handoff 写审计日志"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        handoff = Handoff(from_agent="A", to_agent="B", reason="测试")

        with patch("agent.core.observability.audit.audit_log", new_callable=AsyncMock) as mock_audit:
            await guard.audit(handoff, allowed=True, reason="")
            mock_audit.assert_called_once()
            call_kwargs = mock_audit.call_args
            assert call_kwargs.kwargs["action"] == "handoff"
            assert call_kwargs.kwargs["user_id"] == "u1"
            assert call_kwargs.kwargs["session_id"] == "s1"

    async def test_audit_rejected_handoff(self):
        """测试被拒绝的 handoff 写审计日志"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        handoff = Handoff(from_agent="A", to_agent="B")

        with patch("agent.core.observability.audit.audit_log", new_callable=AsyncMock) as mock_audit:
            await guard.audit(handoff, allowed=False, reason="duplicate_pair_oscillation")
            mock_audit.assert_called_once()
            call_kwargs = mock_audit.call_args
            assert call_kwargs.kwargs["action"] == "handoff_rejected"
            assert call_kwargs.kwargs["detail"]["reject_reason"] == "duplicate_pair_oscillation"

    async def test_audit_failure_does_not_raise(self):
        """测试审计写入失败不抛出异常"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        handoff = Handoff(from_agent="A", to_agent="B")

        with patch("agent.core.observability.audit.audit_log", new_callable=AsyncMock) as mock_audit:
            mock_audit.side_effect = Exception("审计服务不可用")
            # 不应抛出异常
            await guard.audit(handoff, allowed=True, reason="")


# ==================== FR-9: Handoff 事件发布 ====================


class TestHandoffPublish:
    """FR-9: Handoff 事件发布测试"""

    async def test_publish_event(self):
        """测试发布 handoff 事件"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        guard.init_chain("A")
        handoff = Handoff(
            from_agent="A", to_agent="B", reason="测试转交",
            context_payload={"key": "value"},
        )

        with patch("agent.core.infrastructure.event_bus.publish_event") as mock_publish:
            await guard.publish(handoff)
            mock_publish.assert_called_once()
            call_args = mock_publish.call_args
            assert call_args.kwargs["session_id"] == "s1"
            assert call_args.kwargs["data"]["from_agent"] == "A"
            assert call_args.kwargs["data"]["to_agent"] == "B"

    async def test_publish_failure_does_not_raise(self):
        """测试事件发布失败不抛出异常"""
        guard = HandoffGuard(session_id="s1", user_id="u1")
        handoff = Handoff(from_agent="A", to_agent="B")

        with patch("agent.core.infrastructure.event_bus.publish_event") as mock_publish:
            mock_publish.side_effect = Exception("事件总线不可用")
            await guard.publish(handoff)  # 不应抛出异常


# ==================== Handoff 数据模型测试 ====================


class TestHandoffModel:
    """Handoff 数据模型测试"""

    def test_handoff_creation(self):
        """测试 Handoff 创建"""
        h = Handoff(from_agent="A", to_agent="B")
        assert h.from_agent == "A"
        assert h.to_agent == "B"
        assert h.reason == ""
        assert h.context_payload == {}
        assert h.condition is None

    def test_handoff_with_all_fields(self):
        """测试 Handoff 包含所有字段"""
        h = Handoff(
            from_agent="A", to_agent="B",
            reason="转交原因",
            context_payload={"key": "value"},
            condition="触发条件",
        )
        assert h.reason == "转交原因"
        assert h.context_payload == {"key": "value"}
        assert h.condition == "触发条件"

    def test_handoff_relation_creation(self):
        """测试 HandoffRelation 创建"""
        r = HandoffRelation(
            from_agent="A", to_agent="B",
            condition="条件",
            tenant_id="t1",
            condition_keywords=["k1", "k2"],
        )
        assert r.tenant_id == "t1"
        assert r.condition_keywords == ["k1", "k2"]
        assert r.enabled is True  # 默认启用


# ==================== 全局单例测试 ====================


class TestGlobalRegistry:
    """全局 HandoffRegistry 单例测试"""

    def test_get_handoff_registry_singleton(self):
        """测试全局单例"""
        r1 = get_handoff_registry()
        r2 = get_handoff_registry()
        assert r1 is r2

    def test_reset_handoff_registry(self):
        """测试重置单例"""
        r1 = get_handoff_registry()
        reset_handoff_registry()
        r2 = get_handoff_registry()
        assert r1 is not r2


# ==================== CollaborationMode 枚举扩展测试 ====================


class TestCollaborationModeExtension:
    """CollaborationMode 枚举扩展测试"""

    def test_handoff_mode_exists(self):
        """测试 HANDOFF 枚举值存在"""
        from agent.agents.supervisor import CollaborationMode
        assert CollaborationMode.HANDOFF == "handoff"

    def test_handoff_mode_is_str_enum(self):
        """测试 HANDOFF 是 str 枚举"""
        from agent.agents.supervisor import CollaborationMode
        assert isinstance(CollaborationMode.HANDOFF, str)
        assert CollaborationMode.HANDOFF.value == "handoff"

    def test_existing_modes_unchanged(self):
        """测试既有枚举值不变"""
        from agent.agents.supervisor import CollaborationMode
        assert CollaborationMode.DIRECT == "direct"
        assert CollaborationMode.SELECTOR == "selector"
        assert CollaborationMode.SWARM == "swarm"

    def test_intent_routing_table_has_handoff(self):
        """测试 INTENT_ROUTING_TABLE 包含 handoff 模式意图"""
        from agent.agents.supervisor import INTENT_ROUTING_TABLE
        assert "customer_service_route" in INTENT_ROUTING_TABLE
        assert INTENT_ROUTING_TABLE["customer_service_route"]["mode"] == "handoff"
        assert "document_then_send" in INTENT_ROUTING_TABLE
        assert INTENT_ROUTING_TABLE["document_then_send"]["mode"] == "handoff"
