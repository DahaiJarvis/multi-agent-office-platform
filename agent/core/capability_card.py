"""Agent 自描述协议（Capability Card）

每个 Agent 注册时声明自己的能力卡片，包含：
  - 基本信息：名称、描述、版本
  - 能力声明：支持的意图、输入输出格式
  - 意图级配置：每个意图的协作模式和审核规则
  - 依赖声明：所需的 MCP 服务和工具
  - 性能指标：平均响应时间、成功率
  - 限制说明：不支持的场景、安全约束

Supervisor 根据能力卡片进行智能路由，而非硬编码的意图映射。

支持从 YAML 文件加载能力卡片，实现配置外置化。
YAML 文件位于 config/capabilities/ 目录下，每个 Agent 一个文件。

使用方式：
    from agent.core.capability_card import CapabilityCard, get_capability_registry

    # 注册能力卡片
    card = CapabilityCard(
        agent_name="EmailAgent",
        description="企业邮件处理专家",
        supported_intents=["email_query", "email_send", "email_classify"],
        intent_configs=[
            IntentConfig(intent="email_query", mode="direct", review=False),
            IntentConfig(intent="email_send", mode="selector", review=True),
        ],
        required_services=["email"],
    )
    registry = get_capability_registry()
    registry.register(card)

    # 查询匹配的 Agent
    agents = registry.find_by_intent("email_send")

    # 查询意图的路由配置
    routing = registry.get_routing_for_intent("email_send")
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CAPABILITIES_DIR = Path(__file__).parent.parent.parent / "config" / "capabilities"


class IntentConfig(BaseModel):
    """意图级路由配置"""

    intent: str = Field(..., description="意图标签名称")
    mode: str = Field(default="direct", description="协作模式: direct/selector/swarm")
    review: bool = Field(default=False, description="是否需要审核")


class CapabilityCard(BaseModel):
    """Agent 能力卡片"""

    agent_name: str = Field(..., description="Agent 名称")
    description: str = Field(default="", description="Agent 描述")
    version: str = Field(default="1.0.0", description="版本号")
    category: str = Field(default="domain", description="分类: supervisor/domain/utility")

    supported_intents: list[str] = Field(default_factory=list, description="支持的意图列表")
    supported_actions: list[str] = Field(default_factory=list, description="支持的操作列表")
    capability_keywords: list[str] = Field(default_factory=list, description="能力关键词列表，用于补充需求的能力匹配检测")

    intent_configs: list[IntentConfig] = Field(default_factory=list, description="意图级路由配置")

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
    支持从 YAML 文件加载能力卡片，优先使用 YAML 配置，降级到代码内嵌默认值。
    """

    def __init__(self) -> None:
        self._cards: dict[str, CapabilityCard] = {}
        self._loaded: bool = False

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

    def get_routing_for_intent(self, intent: str) -> dict[str, Any] | None:
        """根据意图获取路由配置

        优先从 CapabilityCard 的 intent_configs 中查找精确配置，
        如果未配置则根据意图名称推断（_query 后缀用 direct，其他用 selector）。

        Args:
            intent: 意图标签

        Returns:
            路由配置字典 {"agent": ..., "mode": ..., "review": ...}，未找到返回 None
        """
        self._ensure_loaded()

        matched = self.find_by_intent(intent)
        if not matched:
            return None

        best_card = matched[0]

        # 优先从 intent_configs 查找精确配置
        for cfg in best_card.intent_configs:
            if cfg.intent == intent:
                return {
                    "agent": best_card.agent_name,
                    "mode": cfg.mode,
                    "review": cfg.review,
                }

        # 降级：根据意图名称推断
        is_query_intent = intent.endswith("_query") or intent in (
            "knowledge_search", "knowledge_qa", "document_query",
            "email_classify", "email_summary",
        )
        review_required = (not is_query_intent) and (
            bool(best_card.security_constraints) or intent in {
                "approval_action", "email_send", "hr_action",
                "finance_action", "kb_manage", "report_generate",
                "cross_system", "complex_task",
            }
        )
        mode = "selector" if review_required else "direct"
        if intent in ("cross_system", "complex_task"):
            mode = "swarm"

        return {
            "agent": best_card.agent_name,
            "mode": mode,
            "review": review_required,
        }

    def check_agent_capability(
        self,
        agent_name: str,
        message: str,
    ) -> dict[str, Any]:
        """检测Agent是否具备处理指定消息的能力

        通过能力关键词匹配和限制声明，判断Agent是否能处理
        用户消息中包含的需求。用于步骤执行前的能力预检测。

        Args:
            agent_name: Agent名称
            message: 用户消息（包含原始需求和补充需求）

        Returns:
            检测结果字典:
            - can_handle: bool, 是否能处理
            - matched_keywords: list[str], 匹配到的能力关键词
            - unmatched_keywords: list[str], 未匹配到的需求关键词
            - limitations: list[str], Agent的限制说明
            - suggested_agents: list[str], 建议的替代Agent
        """
        self._ensure_loaded()

        card = self._cards.get(agent_name)
        if card is None:
            return {
                "can_handle": False,
                "matched_keywords": [],
                "unmatched_keywords": [],
                "limitations": [],
                "suggested_agents": [],
            }

        matched_keywords: list[str] = []
        for kw in card.capability_keywords:
            if kw in message:
                matched_keywords.append(kw)

        all_capability_keywords: set[str] = set()
        for c in self._cards.values():
            if c.enabled:
                all_capability_keywords.update(c.capability_keywords)

        unmatched_keywords: list[str] = []
        for kw in all_capability_keywords:
            if kw in message and kw not in card.capability_keywords:
                unmatched_keywords.append(kw)

        can_handle = len(unmatched_keywords) == 0

        suggested_agents: list[str] = []
        if not can_handle:
            for other_card in self._cards.values():
                if not other_card.enabled or other_card.agent_name == agent_name:
                    continue
                for ukw in unmatched_keywords:
                    if ukw in other_card.capability_keywords:
                        if other_card.agent_name not in suggested_agents:
                            suggested_agents.append(other_card.agent_name)

        return {
            "can_handle": can_handle,
            "matched_keywords": matched_keywords,
            "unmatched_keywords": unmatched_keywords,
            "limitations": card.limitations,
            "suggested_agents": suggested_agents,
        }

    def find_agent_by_keywords(
        self,
        message: str,
        exclude_agent: str | None = None,
    ) -> list[tuple[str, int]]:
        """根据消息中的关键词查找最匹配的Agent

        按匹配到的能力关键词数量排序，返回最合适的Agent列表。

        Args:
            message: 用户消息
            exclude_agent: 排除的Agent名称

        Returns:
            [(agent_name, match_count), ...] 按匹配数降序排列
        """
        self._ensure_loaded()

        results: list[tuple[str, int]] = []
        for card in self._cards.values():
            if not card.enabled or card.agent_name == exclude_agent:
                continue
            match_count = sum(1 for kw in card.capability_keywords if kw in message)
            if match_count > 0:
                results.append((card.agent_name, match_count))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def _ensure_loaded(self) -> None:
        """确保能力卡片已加载"""
        if self._loaded:
            return
        self._load_from_files()
        if not self._cards:
            _register_default_cards(self)
        self._loaded = True
        self._validate_cards()

    def _validate_cards(self) -> None:
        """校验能力卡片配置的完整性和一致性

        校验项：
          1. intent_configs 中的意图必须在 supported_intents 中声明
          2. intent_configs 中的 mode 值必须是合法的协作模式
          3. 意图标签列表中的每个意图至少有一个 Agent 支持
          4. 同一意图不应被多个已启用 Agent 以相同优先级声明
        """
        if not self._cards:
            logger.warning("Schema校验: 未加载到任何能力卡片，路由功能将不可用")
            return

        valid_modes = {"direct", "selector", "swarm"}
        error_count = 0
        covered_intents: set[str] = set()

        # 校验1 & 2：逐卡片校验 intent_configs
        for card in self._cards.values():
            config_intents = {cfg.intent for cfg in card.intent_configs}
            declared_intents = set(card.supported_intents)

            # intent_configs 中的意图必须在 supported_intents 中
            undeclared = config_intents - declared_intents
            if undeclared:
                logger.error(
                    "Schema校验[ERROR]: %s 的 intent_configs 包含未在 supported_intents 中声明的意图: %s",
                    card.agent_name, undeclared,
                )
                error_count += 1

            # mode 值必须合法
            for cfg in card.intent_configs:
                if cfg.mode not in valid_modes:
                    logger.error(
                        "Schema校验[ERROR]: %s 的 intent_configs 中意图 %s 的 mode 值非法: '%s'，合法值: %s",
                        card.agent_name, cfg.intent, cfg.mode, valid_modes,
                    )
                    error_count += 1

        # 校验3：每个意图标签至少有一个 Agent 支持
        from agent.core.prompt_registry import get_prompt_registry
        try:
            prompt_registry = get_prompt_registry()
            intent_names = {i.name for i in prompt_registry.get_intents()}
            for card in self._cards.values():
                if card.enabled:
                    covered_intents.update(card.supported_intents)

            uncovered = intent_names - covered_intents
            if uncovered:
                logger.error(
                    "Schema校验[ERROR]: 以下意图标签没有任何 Agent 支持: %s",
                    uncovered,
                )
                error_count += 1
        except Exception:
            logger.warning("Schema校验: 无法获取意图标签列表，跳过意图覆盖校验")

        # 校验4：同一意图不应被多个已启用 Agent 以相同优先级声明
        intent_agents: dict[str, list[tuple[str, int]]] = {}
        for card in self._cards.values():
            if not card.enabled:
                continue
            for intent in card.supported_intents:
                if intent not in intent_agents:
                    intent_agents[intent] = []
                intent_agents[intent].append((card.agent_name, card.priority))

        for intent, agents in intent_agents.items():
            if len(agents) <= 1:
                continue
            priorities = [p for _, p in agents]
            if len(priorities) != len(set(priorities)):
                agent_names = [a for a, _ in agents]
                logger.warning(
                    "Schema校验[WARN]: 意图 %s 被多个 Agent 以相同优先级声明: %s，路由结果可能不确定",
                    intent, agent_names,
                )

        if error_count == 0:
            logger.info(
                "Schema校验: 能力卡片校验通过, 共 %d 个 Agent, 覆盖 %d 个意图",
                len(self._cards), len(covered_intents),
            )
        else:
            logger.error("Schema校验: 能力卡片校验发现 %d 个错误", error_count)

    def _load_from_files(self) -> None:
        """从 YAML 文件加载能力卡片"""
        if not CAPABILITIES_DIR.exists():
            logger.info("能力卡片配置目录不存在: %s，使用代码内嵌默认值", CAPABILITIES_DIR)
            return

        yaml_files = list(CAPABILITIES_DIR.glob("*.yaml")) + list(CAPABILITIES_DIR.glob("*.yml"))
        if not yaml_files:
            logger.info("能力卡片配置目录为空: %s，使用代码内嵌默认值", CAPABILITIES_DIR)
            return

        for yaml_file in yaml_files:
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue

                intent_configs = []
                for cfg_data in data.get("intent_configs", []):
                    intent_configs.append(IntentConfig(
                        intent=cfg_data.get("intent", ""),
                        mode=cfg_data.get("mode", "direct"),
                        review=cfg_data.get("review", False),
                    ))

                card = CapabilityCard(
                    agent_name=data.get("agent_name", yaml_file.stem),
                    description=data.get("description", ""),
                    version=data.get("version", "1.0.0"),
                    category=data.get("category", "domain"),
                    supported_intents=data.get("supported_intents", []),
                    supported_actions=data.get("supported_actions", []),
                    capability_keywords=data.get("capability_keywords", []),
                    intent_configs=intent_configs,
                    required_services=data.get("required_services", []),
                    required_tools=data.get("required_tools", []),
                    security_constraints=data.get("security_constraints", []),
                    limitations=data.get("limitations", []),
                    priority=data.get("priority", 0),
                    enabled=data.get("enabled", True),
                )
                self._cards[card.agent_name] = card
                logger.info("从YAML加载能力卡片: %s (intents=%s)", card.agent_name, card.supported_intents)

            except Exception as e:
                logger.error("加载能力卡片文件 %s 失败: %s", yaml_file.name, e)


# 全局能力注册中心实例
_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    """获取全局能力注册中心实例

    优先从 config/capabilities/ 目录加载 YAML 配置，
    如果目录不存在或为空，则降级到代码内嵌默认值。
    """
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _registry._ensure_loaded()
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
            capability_keywords=["路由", "分类", "调度", "规划"],
            limitations=["不执行具体业务操作", "不直接调用MCP工具"],
            required_services=[],
            priority=0,
        ),
        CapabilityCard(
            agent_name="ApprovalAgent",
            description="企业审批处理专家",
            category="domain",
            supported_intents=["approval_query", "approval_action", "approval_track"],
            supported_actions=["query_list", "approve", "reject", "transfer"],
            capability_keywords=["审批", "待审批", "OA", "同意", "拒绝", "转审", "审批单"],
            limitations=["仅处理审批相关操作", "不支持创建审批流程"],
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
            capability_keywords=["邮件", "收件箱", "发邮件", "发送邮件", "抄送", "附件", "邮件摘要"],
            limitations=["不支持邮件模板管理", "不支持邮件规则配置"],
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
            capability_keywords=["日程", "会议", "日历", "预约", "会议室", "时间冲突", "日历"],
            limitations=["不支持会议室资源管理", "不支持跨组织日程共享"],
            required_services=["calendar"],
            security_constraints=["创建日程需确认参与者是否冲突", "删除他人日程需特别确认"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="CRMAgent",
            description="CRM 客户管理专家",
            category="domain",
            supported_intents=["crm_query", "crm_update", "crm_analysis"],
            supported_actions=["query_customer", "update_customer", "analyze"],
            capability_keywords=["客户", "商机", "CRM", "销售", "联系人", "合同"],
            limitations=["不支持客户数据导出", "不支持批量数据修改"],
            required_services=["crm"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="HRAgent",
            description="HR 人事管理专家",
            category="domain",
            supported_intents=["hr_query", "hr_leave", "hr_attendance"],
            supported_actions=["query_info", "apply_leave", "check_attendance"],
            capability_keywords=["考勤", "请假", "薪资", "假期", "HR", "加班", "打卡", "人事"],
            limitations=["不支持薪资修改", "不支持人事档案管理"],
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
            capability_keywords=["报销", "预算", "发票", "财务", "付款", "费用", "对账"],
            limitations=["不支持财务报表生成", "不支持银行对账操作"],
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
            capability_keywords=["知识库", "文档", "搜索", "问答", "摘要", "解析", "对比", "报告", "网络搜索", "图片分析"],
            limitations=["不支持视频处理", "不支持音频处理", "不支持代码执行"],
            required_services=["knowledge"],
            priority=10,
        ),
        CapabilityCard(
            agent_name="Reviewer",
            description="质量审核 Agent，负责结果审核和合规检查",
            category="utility",
            supported_intents=["review", "compliance_check"],
            supported_actions=["review", "check", "validate"],
            capability_keywords=["审核", "合规", "检查", "安全"],
            limitations=["不执行业务操作", "仅做审核判断"],
            required_services=["oa", "approval", "hr", "finance"],
            priority=5,
        ),
    ]

    for card in cards:
        registry.register(card)
