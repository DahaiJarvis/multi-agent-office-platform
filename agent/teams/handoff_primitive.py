"""Handoff 原生原语

================================================================================
模块职责
================================================================================
提供轻量级 Agent 间控制权移交原语，作为 Supervisor + CollaborationMode 协作模式的
补充。适用于简单多 Agent 流（不需要 Supervisor 全局规划的场景）。

对标 OpenAI Agents SDK 的 handoff 模型：Agent 在执行过程中可显式地将控制权移交
给另一个 Agent，并携带必要的上下文，无需 Supervisor 介入调度。

================================================================================
核心组件
================================================================================
- Handoff: 移交原语数据模型（运行时实例）
- HandoffRelation: 注册表中的静态关系声明
- HandoffRegistry: 关系注册中心，提供注册/查询/条件匹配
- HandoffGuard: 防循环 + 脱敏 + 审计
- HandoffContextBuilder: 上下文组装器

================================================================================
设计原则
================================================================================
- 补充而非替代：与 SWARM 模式并存，不修改既有协作模式语义
- 显式可治理：所有 handoff 关系必须显式注册，未注册的转交被拒绝
- 安全第一：敏感操作不可绕过审核，敏感字段脱敏后入 context_payload
- 防循环：单任务 handoff 深度上限 5，重复 (from,to) 对去重

================================================================================
使用示例
================================================================================
    from agent.teams.handoff_primitive import get_handoff_registry, HandoffGuard

    # 注册 handoff 关系
    registry = get_handoff_registry()
    registry.register("OfficeAssistant", "FinanceAgent",
                      condition="当用户问题涉及财务/报销/预算时",
                      condition_keywords=["报销", "预算", "财务", "发票"])

    # 运行时尝试触发 handoff
    handoff = await registry.try_handoff("OfficeAssistant",
                                         context={"user_message": "我的报销到哪了"})
    if handoff:
        guard = HandoffGuard(session_id="sess-1", user_id="user-1")
        allowed, reason = guard.check_and_record(handoff)
        if allowed:
            await guard.audit(handoff, True, "")
            await guard.publish(handoff)
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================


class Handoff(BaseModel):
    """Agent 间控制权移交原语

    对标 OpenAI Agents SDK 的 handoff 模型。
    与 Supervisor 模式并存：简单场景用 Handoff，复杂场景用 Supervisor。

    Attributes:
        from_agent: 移交方 Agent 名称
        to_agent: 接收方 Agent 名称
        reason: 移交原因（自然语言描述，用于审计与可观测性）
        context_payload: 移交的上下文（携带给接收方的结构化数据，已脱敏）
        condition: 触发条件（自然语言描述，注册时声明）
    """

    from_agent: str = Field(..., description="移交方 Agent 名称")
    to_agent: str = Field(..., description="接收方 Agent 名称")
    reason: str = Field(default="", description="移交原因")
    context_payload: dict = Field(default_factory=dict, description="移交的上下文（已脱敏）")
    condition: str | None = Field(default=None, description="触发条件（自然语言描述）")


class HandoffRelation(BaseModel):
    """Handoff 关系内部表示（注册表存储用）

    与 Handoff 的区别：
    - HandoffRelation 是注册时的静态关系声明（含 condition）
    - Handoff 是运行时触发后产生的移交实例（含 context_payload、reason）

    Attributes:
        from_agent: 移交方 Agent 名称
        to_agent: 接收方 Agent 名称
        condition: 触发条件（自然语言描述）
        tenant_id: 租户 ID，空字符串表示平台级关系
        condition_keywords: 条件关键词，用于规则层快速匹配
        enabled: 是否启用
    """

    from_agent: str = Field(..., description="移交方 Agent 名称")
    to_agent: str = Field(..., description="接收方 Agent 名称")
    condition: str = Field(..., description="触发条件（自然语言）")
    tenant_id: str = Field(default="", description="租户 ID")
    condition_keywords: list[str] = Field(
        default_factory=list, description="条件关键词，用于规则层快速匹配"
    )
    enabled: bool = Field(default=True, description="是否启用")


# ============================================================================
# HandoffRegistry 注册中心
# ============================================================================


class HandoffRegistry:
    """Handoff 关系注册中心

    集中管理 Agent 间的 handoff 关系，提供注册、查询、条件匹配能力。
    所有 handoff 关系必须显式注册，未注册的转交在条件匹配阶段被拒绝。

    设计要点：
    -------------------------------------------------------------------------
    1. 注册时校验 from/to Agent 是否在 CapabilityRegistry 中存在
    2. 支持多租户隔离，tenant_id="" 表示平台级关系
    3. 同一 (from, to) 对可注册多个条件，任一命中即触发
    4. 条件匹配采用规则优先 + LLM 兜底两层策略
       - 规则层：关键词命中、字段匹配（< 10ms）
       - LLM 层：规则未命中时调用轻量模型判断（< 100ms）
    5. 线程安全，使用 threading.Lock 保护内部字典
    -------------------------------------------------------------------------
    """

    def __init__(self) -> None:
        """初始化注册中心

        内部使用三级字典索引：{tenant_id: {from_agent: [HandoffRelation, ...]}}
        """
        # 关系表: {tenant_id: {from_agent: [HandoffRelation, ...]}}
        self._relations: dict[str, dict[str, list[HandoffRelation]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._lock = threading.Lock()

    def register(
        self,
        from_agent: str,
        to_agent: str,
        condition: str,
        tenant_id: str = "",
        condition_keywords: list[str] | None = None,
    ) -> None:
        """注册 Agent 间的 handoff 关系

        注册前校验：
            1. from_agent 与 to_agent 不能相同
            2. from_agent 与 to_agent 必须在 CapabilityRegistry 中已注册
            3. 同一 (from, to, condition) 三元组不重复注册

        Args:
            from_agent: 移交方 Agent 名称
            to_agent: 接收方 Agent 名称
            condition: 触发条件（自然语言描述）
            tenant_id: 租户 ID，空字符串表示平台级关系
            condition_keywords: 条件关键词列表，用于规则层快速匹配。
                                为空时从 condition 中自动提取关键词

        Raises:
            ValueError: from/to 相同、Agent 不存在、重复注册时抛出
        """
        # 校验 1: from/to 不能相同
        if from_agent == to_agent:
            raise ValueError(f"from_agent 与 to_agent 不能相同: {from_agent}")

        # 校验 2: from/to 必须在 CapabilityRegistry 中已注册
        if not self._agent_exists(from_agent):
            raise ValueError(f"from_agent 不存在于 CapabilityRegistry: {from_agent}")
        if not self._agent_exists(to_agent):
            raise ValueError(f"to_agent 不存在于 CapabilityRegistry: {to_agent}")

        # 自动提取关键词（如果未显式提供）
        if condition_keywords is None:
            condition_keywords = self._extract_keywords(condition)

        relation = HandoffRelation(
            from_agent=from_agent,
            to_agent=to_agent,
            condition=condition,
            tenant_id=tenant_id,
            condition_keywords=condition_keywords,
        )

        with self._lock:
            # 校验 3: 同一 (from, to, condition) 三元组不重复注册
            existing = self._relations.get(tenant_id, {}).get(from_agent, [])
            for r in existing:
                if r.to_agent == to_agent and r.condition == condition:
                    raise ValueError(
                        f"handoff 关系已注册: {from_agent} -> {to_agent}, condition={condition}"
                    )

            self._relations[tenant_id][from_agent].append(relation)

        logger.info(
            "注册 handoff 关系: %s -> %s, condition=%s, tenant=%s, keywords=%s",
            from_agent, to_agent, condition, tenant_id, condition_keywords,
        )

    def unregister(
        self,
        from_agent: str,
        to_agent: str,
        tenant_id: str = "",
    ) -> bool:
        """注销 Agent 间的 handoff 关系（清除该方向所有条件）

        Args:
            from_agent: 移交方 Agent 名称
            to_agent: 接收方 Agent 名称
            tenant_id: 租户 ID

        Returns:
            是否成功注销（不存在则返回 False）
        """
        with self._lock:
            relations = self._relations.get(tenant_id, {}).get(from_agent, [])
            original_count = len(relations)
            relations[:] = [r for r in relations if r.to_agent != to_agent]
            removed = original_count - len(relations)

            if removed > 0:
                logger.info(
                    "注销 handoff 关系: %s -> %s, tenant=%s, 移除 %d 条",
                    from_agent, to_agent, tenant_id, removed,
                )
                return True
            return False

    def get_targets(self, from_agent: str, tenant_id: str = "") -> list[str]:
        """查询某 Agent 可 handoff 的目标 Agent 列表

        用于团队工厂构建 Agent 时配置 handoffs 参数。

        Args:
            from_agent: 移交方 Agent 名称
            tenant_id: 租户 ID

        Returns:
            目标 Agent 名称列表，按注册顺序排列，去重
        """
        with self._lock:
            relations = self._relations.get(tenant_id, {}).get(from_agent, [])
            # 按注册顺序排列，去重，仅返回 enabled 的目标
            seen: set[str] = set()
            targets: list[str] = []
            for r in relations:
                if r.enabled and r.to_agent not in seen:
                    seen.add(r.to_agent)
                    targets.append(r.to_agent)
            return targets

    def list_relations(
        self,
        tenant_id: str = "",
    ) -> list[HandoffRelation]:
        """列出指定租户下全部 handoff 关系（管理界面用）

        Args:
            tenant_id: 租户 ID

        Returns:
            HandoffRelation 列表
        """
        with self._lock:
            result: list[HandoffRelation] = []
            tenant_relations = self._relations.get(tenant_id, {})
            for from_agent, relations in tenant_relations.items():
                result.extend(relations)
            return result

    async def try_handoff(
        self,
        current_agent: str,
        context: dict,
        tenant_id: str = "",
    ) -> Handoff | None:
        """尝试触发 handoff

        匹配流程：
        -------------------------------------------------------------------------
        1. 从注册表中取出 current_agent 的全部 handoff 关系
        2. 规则层匹配：遍历每条关系的 condition_keywords 做命中检测
           - 命中则构造 Handoff 返回
        3. LLM 兜底层：规则层未命中时，调用轻量模型判断是否应 handoff
           - 仅当存在未命中条件的关系时才调用，避免无效 LLM 开销
        4. 全部未命中返回 None（当前 Agent 继续执行）
        -------------------------------------------------------------------------

        Args:
            current_agent: 当前执行 Agent 名称
            context: 当前上下文，包含 user_message、对话历史、已采集信息等
            tenant_id: 租户 ID

        Returns:
            匹配到的 Handoff 对象，未匹配返回 None
        """
        with self._lock:
            relations = list(self._relations.get(tenant_id, {}).get(current_agent, []))

        if not relations:
            return None

        # 过滤出启用的关系
        active_relations = [r for r in relations if r.enabled]
        if not active_relations:
            return None

        # 提取上下文文本用于匹配
        context_text = self._extract_context_text(context)

        # 1. 规则层匹配：关键词命中检测
        matched_relation = self._rule_based_match(active_relations, context_text)
        if matched_relation is not None:
            logger.info(
                "规则层匹配 handoff: %s -> %s, keywords=%s",
                matched_relation.from_agent,
                matched_relation.to_agent,
                matched_relation.condition_keywords,
            )
            return Handoff(
                from_agent=matched_relation.from_agent,
                to_agent=matched_relation.to_agent,
                reason=f"规则匹配: {matched_relation.condition}",
                condition=matched_relation.condition,
            )

        # 2. LLM 兜底层：调用轻量模型判断
        llm_matched = await self._llm_based_match(active_relations, context)
        if llm_matched is not None:
            logger.info(
                "LLM 兜底层匹配 handoff: %s -> %s",
                llm_matched.from_agent,
                llm_matched.to_agent,
            )
            return Handoff(
                from_agent=llm_matched.from_agent,
                to_agent=llm_matched.to_agent,
                reason=f"LLM 判断: {llm_matched.condition}",
                condition=llm_matched.condition,
            )

        # 3. 全部未命中
        return None

    # -------------------- 内部辅助方法 --------------------

    def _agent_exists(self, agent_name: str) -> bool:
        """校验 Agent 是否在 CapabilityRegistry 中已注册

        Reviewer 等内置角色不在 CapabilityRegistry 中但允许注册 handoff，
        因此对内置角色做白名单放行。

        Args:
            agent_name: Agent 名称

        Returns:
            是否存在
        """
        # 内置角色白名单（不在 CapabilityRegistry 中但允许参与 handoff）
        builtin_roles = {"Supervisor", "Reviewer", "OfficeAssistant"}
        if agent_name in builtin_roles:
            return True

        try:
            from agent.core.skill.capability_card import get_capability_registry
            registry = get_capability_registry()
            card = registry.get(agent_name)
            return card is not None
        except Exception as e:
            logger.warning("CapabilityRegistry 校验失败，放行 %s: %s", agent_name, e)
            # 降级：CapabilityRegistry 不可用时放行，避免阻塞注册
            return True

    def _extract_keywords(self, condition: str) -> list[str]:
        """从条件描述中提取关键词

        简单策略：将条件文本按标点/空格分词，过滤停用词和短词。

        Args:
            condition: 条件描述文本

        Returns:
            关键词列表
        """
        if not condition:
            return []

        import re
        # 按非中文/非字母数字字符分割
        tokens = re.findall(r"[\u4e00-\u9fa5]+|[a-zA-Z_]+", condition)

        # 停用词
        stop_words = {
            "当", "的", "时", "涉及", "需要", "进行", "可以", "应", "该",
            "是", "在", "为", "与", "和", "或", "及", "等", "中", "上",
            "下", "后", "前", "the", "a", "an", "is", "are", "when",
        }

        keywords = [
            t for t in tokens
            if len(t) >= 2 and t not in stop_words
        ]
        return keywords

    def _extract_context_text(self, context: dict) -> str:
        """从上下文中提取用于匹配的文本

        Args:
            context: 上下文字典

        Returns:
            拼接后的文本
        """
        parts: list[str] = []
        # 优先取 user_message
        user_msg = context.get("user_message", "")
        if isinstance(user_msg, str):
            parts.append(user_msg)

        # 取对话历史中的文本
        history = context.get("history", [])
        if isinstance(history, list):
            for msg in history:
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        parts.append(content)
                elif isinstance(msg, str):
                    parts.append(msg)

        # 取 collected_info 中的文本值
        collected = context.get("collected_info", {})
        if isinstance(collected, dict):
            for v in collected.values():
                if isinstance(v, str):
                    parts.append(v)

        return " ".join(parts)

    def _rule_based_match(
        self,
        relations: list[HandoffRelation],
        context_text: str,
    ) -> HandoffRelation | None:
        """规则层匹配：关键词命中检测

        遍历每条关系的 condition_keywords，若任一关键词在上下文文本中命中，
        则返回该关系。

        Args:
            relations: 候选关系列表
            context_text: 上下文文本

        Returns:
            命中的关系，未命中返回 None
        """
        if not context_text:
            return None

        context_lower = context_text.lower()
        for relation in relations:
            for keyword in relation.condition_keywords:
                if keyword and keyword.lower() in context_lower:
                    return relation
        return None

    async def _llm_based_match(
        self,
        relations: list[HandoffRelation],
        context: dict,
    ) -> HandoffRelation | None:
        """LLM 兜底层匹配

        规则层未命中时，调用轻量模型判断当前上下文是否应触发 handoff。
        LLM 不可用时降级为不匹配（返回 None）。

        Args:
            relations: 候选关系列表
            context: 当前上下文

        Returns:
            命中的关系，未命中或 LLM 不可用时返回 None
        """
        try:
            from agent.core.model.model_client import get_lightweight_client
            from security.injection_detection import detect_injection
        except ImportError as e:
            logger.warning("LLM 兜底层依赖不可用，跳过: %s", e)
            return None

        # SEC-10: LLM 输入注入防护
        user_message = str(context.get("user_message", ""))
        try:
            injection_result = detect_injection(user_message)
            if hasattr(injection_result, "is_injection") and injection_result.is_injection:
                logger.warning("LLM 兜底层检测到注入攻击，跳过 handoff 匹配")
                return None
        except Exception as e:
            logger.warning("注入检测失败，降级跳过 LLM 兜底: %s", e)
            return None

        # 构造 LLM 提示
        handoff_options = "\n".join(
            f"- 转交给 {r.to_agent}: {r.condition}"
            for r in relations
        )
        prompt = (
            f"你是一个 handoff 决策助手。根据用户消息判断是否需要将任务转交给其他 Agent。\n\n"
            f"可选的 handoff 目标:\n{handoff_options}\n\n"
            f"用户消息: {user_message[:500]}\n\n"
            f"如果需要转交，回复目标 Agent 名称（如 FinanceAgent）。"
            f"如果不需要转交，回复 NONE。\n"
            f"只回复 Agent 名称或 NONE，不要其他内容。"
        )

        try:
            client = get_lightweight_client()
            from autogen_core.models import UserMessage
            response = await client.create([UserMessage(content=prompt, source="handoff_judge")])
            content = response.content.strip() if response.content else ""

            if content and content.upper() != "NONE":
                # 匹配返回的 Agent 名称
                for relation in relations:
                    if relation.to_agent.lower() in content.lower():
                        return relation
        except Exception as e:
            logger.warning("LLM 兜底层调用失败，降级为不匹配: %s", e)
            return None

        return None


# ============================================================================
# HandoffGuard 防循环与审计
# ============================================================================


class HandoffGuard:
    """Handoff 安全守卫

    职责：
    -------------------------------------------------------------------------
    1. 防循环：记录单任务 handoff 链路，超过深度上限拒绝
    2. 去重：同一 (from, to) 对在单任务内只允许一次（防止 A->B->A->B 振荡）
    3. 脱敏：对 context_payload 中的 PII 字段脱敏
    4. 审计：每次 handoff 触发写入审计日志
    5. 事件：发布 handoff 事件供前端/监控消费
    -------------------------------------------------------------------------
    """

    MAX_HANDOFF_DEPTH = 5  # 单任务最大 handoff 次数
    MAX_HANDOFF_CHAIN = 5  # 链路深度上限（含起点 Agent）

    def __init__(self, session_id: str, user_id: str) -> None:
        """初始化 HandoffGuard

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
        """
        self._session_id = session_id
        self._user_id = user_id
        # handoff 链路：记录经过的 Agent 顺序
        self._chain: list[str] = []
        # 已发生的 (from, to) 对，用于去重
        self._pairs: set[tuple[str, str]] = set()

    def init_chain(self, start_agent: str) -> None:
        """初始化链路起点

        在团队开始执行时调用，将初始 Agent 加入链路。

        Args:
            start_agent: 初始执行 Agent 名称
        """
        if not self._chain:
            self._chain.append(start_agent)
            logger.debug("初始化 handoff 链路: %s", start_agent)

    def check_and_record(self, handoff: Handoff) -> tuple[bool, str]:
        """校验 handoff 是否允许，并记录到链路

        校验规则：
            1. 链路深度未超上限（当前链路长度 < MAX_HANDOFF_CHAIN）
            2. handoff 次数未超上限（已发生次数 < MAX_HANDOFF_DEPTH）
            3. (from, to) 对未重复（防止振荡）

        Args:
            handoff: 待校验的 handoff 实例

        Returns:
            (是否允许, 拒绝原因)，允许时原因为空字符串
        """
        # 校验 1: 链路深度
        if len(self._chain) >= self.MAX_HANDOFF_CHAIN:
            return False, "chain_depth_exceeded"

        # 校验 2: handoff 次数（_pairs 的长度即为已发生次数）
        if len(self._pairs) >= self.MAX_HANDOFF_DEPTH:
            return False, "handoff_count_exceeded"

        # 校验 3: (from, to) 对去重
        pair = (handoff.from_agent, handoff.to_agent)
        if pair in self._pairs:
            return False, "duplicate_pair_oscillation"

        # 校验通过，记录到链路
        self._chain.append(handoff.to_agent)
        self._pairs.add(pair)

        logger.debug(
            "handoff 校验通过: %s -> %s, chain=%s, pairs=%d",
            handoff.from_agent, handoff.to_agent, self._chain, len(self._pairs),
        )
        return True, ""

    async def audit(self, handoff: Handoff, allowed: bool, reason: str) -> None:
        """记录 handoff 审计日志

        复用 agent.core.observability.audit.audit_log，事件类型为 AGENT，
        action 为 "handoff" / "handoff_rejected"。

        Args:
            handoff: handoff 实例
            allowed: 是否被允许
            reason: 拒绝原因（allowed=True 时为空）
        """
        try:
            from agent.core.observability.audit import AuditEventType, audit_log

            action = "handoff" if allowed else "handoff_rejected"
            detail: dict[str, Any] = {
                "from_agent": handoff.from_agent,
                "to_agent": handoff.to_agent,
                "reason": handoff.reason,
                "chain_depth": len(self._chain),
                "handoff_count": len(self._pairs),
            }
            if not allowed:
                detail["reject_reason"] = reason

            await audit_log(
                event_type=AuditEventType.AGENT,
                action=action,
                user_id=self._user_id,
                session_id=self._session_id,
                agent_name=handoff.from_agent,
                detail=detail,
            )
        except Exception as e:
            logger.warning("审计日志写入失败: %s", e)

    async def publish(self, handoff: Handoff) -> None:
        """发布 handoff 事件

        复用 agent.core.infrastructure.event_bus.publish_event，
        事件类型复用 EventType.AGENT_START（to_agent 启动）。

        Args:
            handoff: handoff 实例
        """
        try:
            from agent.core.infrastructure.event_bus import EventType, publish_event

            publish_event(
                event_type=EventType.AGENT_START,
                session_id=self._session_id,
                data={
                    "from_agent": handoff.from_agent,
                    "to_agent": handoff.to_agent,
                    "reason": handoff.reason,
                    "condition": handoff.condition or "",
                    "chain_depth": len(self._chain),
                    "handoff_count": len(self._pairs),
                    "context_keys": list(handoff.context_payload.keys()),
                },
            )
        except Exception as e:
            logger.warning("handoff 事件发布失败: %s", e)

    def sanitize_payload(self, payload: dict) -> dict:
        """对 context_payload 中的敏感字段脱敏

        复用 security.pii_detection 的脱敏能力，对手机号、身份证、邮箱、
        银行卡号等字段脱敏后再传递。

        策略：递归遍历 payload 中的字符串值，对含 PII 的字符串进行脱敏。

        Args:
            payload: 原始上下文

        Returns:
            脱敏后的上下文
        """
        if not payload:
            return {}

        return self._sanitize_dict(payload)

    def _sanitize_dict(self, data: dict) -> dict:
        """递归脱敏字典中的字符串值

        Args:
            data: 原始字典

        Returns:
            脱敏后的字典
        """
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str):
                sanitized[key] = self._sanitize_string(value)
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_dict(value)
            elif isinstance(value, list):
                sanitized[key] = self._sanitize_list(value)
            else:
                sanitized[key] = value
        return sanitized

    def _sanitize_list(self, data: list) -> list:
        """递归脱敏列表中的字符串值

        Args:
            data: 原始列表

        Returns:
            脱敏后的列表
        """
        sanitized: list[Any] = []
        for item in data:
            if isinstance(item, str):
                sanitized.append(self._sanitize_string(item))
            elif isinstance(item, dict):
                sanitized.append(self._sanitize_dict(item))
            elif isinstance(item, list):
                sanitized.append(self._sanitize_list(item))
            else:
                sanitized.append(item)
        return sanitized

    def _sanitize_string(self, text: str) -> str:
        """对单个字符串进行 PII 脱敏

        复用 security.pii_detection.detect_pii 检测并返回脱敏后的内容。
        检测失败时返回原文（降级策略：不因脱敏失败阻断 handoff）。

        Args:
            text: 原始文本

        Returns:
            脱敏后的文本
        """
        if not text:
            return text

        try:
            from security.pii_detection import detect_pii
            result = detect_pii(text)
            if result.has_pii and result.redacted_content:
                return result.redacted_content
        except Exception as e:
            logger.warning("PII 脱敏失败，返回原文: %s", e)

        return text

    @property
    def chain(self) -> list[str]:
        """当前 handoff 链路"""
        return list(self._chain)

    @property
    def handoff_count(self) -> int:
        """已发生的 handoff 次数"""
        return len(self._pairs)


# ============================================================================
# HandoffContextBuilder 上下文组装
# ============================================================================


class HandoffContextBuilder:
    """Handoff 上下文组装器

    在 handoff 触发时，从当前 Agent 的执行上下文中提取必要信息，
    组装为 context_payload 传递给接收方。
    """

    def build(
        self,
        from_agent: str,
        to_agent: str,
        reason: str,
        current_context: dict,
        chain_depth: int = 1,
    ) -> dict:
        """组装 context_payload

        组装内容：
        -------------------------------------------------------------------------
        1. original_query: 用户原始请求
        2. from_agent_summary: 移交方已完成的处理摘要
        3. collected_info: 已采集的结构化信息（如查询结果、解析结果）
        4. handoff_reason: 移交原因
        5. handoff_timestamp: 移交时间（ISO8601）
        6. chain_depth: 当前链路深度
        -------------------------------------------------------------------------

        Args:
            from_agent: 移交方
            to_agent: 接收方
            reason: 移交原因
            current_context: 当前执行上下文
            chain_depth: 当前链路深度

        Returns:
            context_payload 字典（未脱敏，由 HandoffGuard.sanitize_payload 脱敏）
        """
        payload: dict[str, Any] = {
            "original_query": str(current_context.get("user_message", "")),
            "from_agent_summary": str(current_context.get("agent_summary", "")),
            "collected_info": current_context.get("collected_info", {}),
            "handoff_reason": reason,
            "handoff_timestamp": datetime.now().isoformat(),
            "chain_depth": chain_depth,
        }

        logger.debug(
            "组装 context_payload: from=%s to=%s keys=%s",
            from_agent, to_agent, list(payload.keys()),
        )
        return payload


# ============================================================================
# 全局单例
# ============================================================================


_registry: HandoffRegistry | None = None


def get_handoff_registry() -> HandoffRegistry:
    """获取全局 HandoffRegistry 单例

    Returns:
        HandoffRegistry 实例
    """
    global _registry
    if _registry is None:
        _registry = HandoffRegistry()
    return _registry


def reset_handoff_registry() -> None:
    """重置全局 HandoffRegistry 单例（测试用）

    清除所有已注册的 handoff 关系，用于测试间隔离。
    """
    global _registry
    _registry = None
