"""Agent 自描述协议（Capability Card）

每个 Agent 注册时声明自己的能力卡片，包含：
  - 基本信息：名称、描述、版本
  - 能力声明：支持的意图、输入输出格式
  - 依赖声明：所需的 MCP 服务和工具
  - 性能指标：平均响应时间、成功率
  - 限制说明：不支持的场景、安全约束

Supervisor 根据能力卡片进行智能路由，而非硬编码的意图映射。

使用方式：
    from agent.core.capability_card import CapabilityCard, get_capability_registry

    # 注册能力卡片
    card = CapabilityCard(
        agent_name="EmailAgent",
        description="企业邮件处理专家",
        supported_intents=["email_query", "email_send", "email_classify"],
        required_services=["email"],
    )
    registry = get_capability_registry()
    registry.register(card)

    # 查询匹配的 Agent
    agents = registry.find_by_intent("email_send")
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CapabilityCard(BaseModel):
    """Agent 能力卡片"""

    agent_name: str = Field(..., description="Agent 名称")
    description: str = Field(default="", description="Agent 描述")
    version: str = Field(default="1.0.0", description="版本号")
    category: str = Field(default="domain", description="分类: supervisor/domain/utility")

    supported_intents: list[str] = Field(default_factory=list, description="支持的意图列表")
    supported_actions: list[str] = Field(default_factory=list, description="支持的操作列表")

    required_services: list[str] = Field(default_factory=list, description="依赖的 MCP 服务")
    required_tools: list[str] = Field(default_factory=list, description="依赖的工具列表")

    input_format: dict[str, Any] = Field(default_factory=dict, description="输入格式说明")
    output_format: dict[str, Any] = Field(default_factory=dict, description="输出格式说明")

    avg_response_ms: float = Field(default=0, description="平均响应时间(毫秒)")
    success_rate: float = Field(default=1.0, description="成功率")
    max_concurrent: int = Field(default=10, description="最大并发数")

    limitations: list[str] = Field(default_factory=list, description="限制说明")
    security_constraints: list[str] = Field(default_factory=list, description="安全约束")

    priority: int = Field(default=0, description="优先级（数值越大优先级越高）")
    enabled: bool = Field(default=True, description="是否启用")


class CapabilityRegistry:
    """能力注册中心

    管理所有 Agent 的能力卡片，支持按意图、服务、分类查询。
    """

    def __init__(self) -> None:
        self._cards: dict[str, CapabilityCard] = {}

    def register(self, card: CapabilityCard) -> None:
        """注册能力卡片"""
        self._cards[card.agent_name] = card
        logger.info("注册能力卡片: %s (intents=%s)", card.agent_name, card.supported_intents)

    def unregister(self, agent_name: str) -> bool:
        """注销能力卡片"""
        if agent_name in self._cards:
            del self._cards[agent_name]
            return True
        return False

    def get(self, agent_name: str) -> CapabilityCard | None:
        """获取指定 Agent 的能力卡片"""
        return self._cards.get(agent_name)

    def find_by_intent(self, intent: str) -> list[CapabilityCard]:
        """根据意图查找匹配的 Agent

        返回支持该意图的所有已启用 Agent，按优先级排序。
        """
        matched = [
            card for card in self._cards.values()
            if card.enabled and intent in card.supported_intents
        ]
        matched.sort(key=lambda c: c.priority, reverse=True)
        return matched

    def find_by_service(self, service_name: str) -> list[CapabilityCard]:
        """根据 MCP 服务查找依赖的 Agent"""
        return [
            card for card in self._cards.values()
            if card.enabled and service_name in card.required_services
        ]

    def find_by_category(self, category: str) -> list[CapabilityCard]:
        """根据分类查找 Agent"""
        return [
            card for card in self._cards.values()
            if card.enabled and card.category == category
        ]

    def list_all(self) -> list[CapabilityCard]:
        """列出所有已注册的能力卡片"""
        return list(self._cards.values())

    def list_enabled(self) -> list[CapabilityCard]:
        """列出所有已启用的能力卡片"""
        return [card for card in self._cards.values() if card.enabled]

    def get_intent_mapping(self) -> dict[str, list[str]]:
        """获取意图到 Agent 的映射关系

        Returns:
            {intent: [agent_name1, agent_name2, ...]}
        """
        mapping: dict[str, list[str]] = {}
        for card in self._cards.values():
            if not card.enabled:
                continue
            for intent in card.supported_intents:
                if intent not in mapping:
                    mapping[intent] = []
                mapping[intent].append(card.agent_name)
        return mapping

    def validate_dependencies(self) -> dict[str, list[str]]:
        """校验所有 Agent 的依赖是否满足

        检查 required_services 和 required_tools 是否有对应的提供者。

        Returns:
            {agent_name: [missing_dependency1, ...]}
        """
        all_services: set[str] = set()
        all_tools: set[str] = set()

        for card in self._cards.values():
            all_services.update(card.required_services)
            for tool in card.required_tools:
                all_tools.add(tool)

        issues: dict[str, list[str]] = {}
        for card in self._cards.values():
            missing: list[str] = []
            for svc in card.required_services:
                providers = self.find_by_service(svc)
                if not providers:
                    missing.append(f"服务 {svc} 无提供者")
            if missing:
                issues[card.agent_name] = missing

        return issues


# 全局能力注册中心实例
_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    """获取全局能力注册中心实例"""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _register_default_cards(_registry)
    return _registry


def _register_default_cards(registry: CapabilityRegistry) -> None:
    """注册默认的 Agent 能力卡片"""
    cards = [
        CapabilityCard(
            agent_name="Supervisor",
            description="总控 Agent，负责意图识别和任务路由",
            category="supervisor",
            supported_intents=["greeting", "general_query", "help"],
            supported_actions=["route", "classify", "summarize"],
            required_services=[],
            priority=0,
        ),
        CapabilityCard(
            agent_name="ApprovalAgent",
            description="企业审批处理专家",
            category="domain",
            supported_intents=["approval_query", "approval_action", "approval_track"],
            supported_actions=["query_list", "approve", "reject", "transfer"],
            required_services=["oa", "approval"],
            security_constraints=["仅处理本人权限范围内的审批", "超过5000元需特别确认"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="EmailAgent",
            description="企业邮件处理专家",
            category="domain",
            supported_intents=["email_query", "email_send", "email_classify", "email_summary"],
            supported_actions=["search", "send", "classify", "summarize", "delete"],
            required_services=["email"],
            security_constraints=["不得发送含敏感数据的邮件", "群发需确认范围"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="CalendarAgent",
            description="企业日程管理专家",
            category="domain",
            supported_intents=["calendar_query", "calendar_create", "calendar_update", "calendar_conflict"],
            supported_actions=["query", "create", "update", "cancel", "conflict_check"],
            required_services=["calendar"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="CRMAgent",
            description="CRM 客户管理专家",
            category="domain",
            supported_intents=["crm_query", "crm_update", "crm_analysis"],
            supported_actions=["query_customer", "update_customer", "analyze"],
            required_services=["crm"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="HRAgent",
            description="HR 人事管理专家",
            category="domain",
            supported_intents=["hr_query", "hr_leave", "hr_attendance"],
            supported_actions=["query_info", "apply_leave", "check_attendance"],
            required_services=["hr"],
            security_constraints=["仅查看本人人事信息"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="FinanceAgent",
            description="财务管理专家",
            category="domain",
            supported_intents=["finance_query", "finance_reimburse", "finance_budget"],
            supported_actions=["query", "reimburse", "budget_check"],
            required_services=["finance"],
            security_constraints=["报销需确认金额和凭证"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="KnowledgeAgent",
            description="知识库检索专家",
            category="domain",
            supported_intents=["knowledge_search", "knowledge_qa", "document_query"],
            supported_actions=["search", "qa", "summarize"],
            required_services=["knowledge"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="Reviewer",
            description="质量审核 Agent，负责结果审核和合规检查",
            category="utility",
            supported_intents=["review", "compliance_check"],
            supported_actions=["review", "check", "validate"],
            required_services=["oa", "approval", "hr", "finance"],
            priority=5,
        ),
    ]

    for card in cards:
        registry.register(card)
