"""团队工厂 - 根据协作模式创建 Agent 团队"""

import logging
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.teams import SelectorGroupChat

from agent.agents.supervisor import CollaborationMode, IntentResult
from agent.core.model_client import get_domain_agent_client, get_supervisor_client
from agent.core.mcp_integration import load_agent_tools

logger = logging.getLogger(__name__)

# 领域 Agent 系统提示词模板
AGENT_SYSTEM_PROMPTS: dict[str, str] = {
    "OfficeAssistant": "你是通用办公助手，负责处理简单的办公查询和操作。请准确、简洁地回答用户问题。",
    "EmailAgent": "你是邮件处理专家，负责邮件的查询、发送、分类和摘要。操作前请确认收件人和内容。",
    "ApprovalAgent": "你是审批处理专家，负责审批查询和审批操作。涉及审批操作时务必确认信息准确。",
    "CalendarAgent": "你是日程管理专家，负责日程查询和会议安排。注意检测时间冲突。",
    "CRMAgent": "你是 CRM 业务专家，负责客户信息查询和商机跟进。保护客户数据隐私。",
    "HRAgent": "你是 HR 人事专家，负责请假、考勤、薪资等查询和操作。薪资信息需保密。",
    "FinanceAgent": "你是财务处理专家，负责报销、预算查询、发票管理。财务操作务必准确。",
    "Reviewer": "你是安全审核 Agent，负责审核敏感操作的合规性和安全性。发现风险可直接否决操作。",
}

MAX_ROUNDS = {
    CollaborationMode.DIRECT: 5,
    CollaborationMode.SELECTOR: 15,
    CollaborationMode.SWARM: 20,
}


async def create_domain_agent(agent_name: str) -> AssistantAgent:
    """创建领域 Agent

    Args:
        agent_name: Agent 名称

    Returns:
        AssistantAgent 实例
    """
    tools = await load_agent_tools(agent_name)
    system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_name, "你是一个办公助手。")

    return AssistantAgent(
        name=agent_name,
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=system_prompt,
    )


async def create_team(intent: IntentResult) -> Any:
    """根据意图结果创建对应的 Agent 团队

    Args:
        intent: 意图分类结果

    Returns:
        Agent 团队实例
    """
    mode = intent.collaboration_mode
    max_rounds = MAX_ROUNDS[mode]

    if mode == CollaborationMode.DIRECT:
        # 单 Agent 直连，无需创建团队
        agent = await create_domain_agent(intent.target_agent)
        return agent

    # 多 Agent 协作模式
    participants = []

    # 添加目标 Agent
    target_agent = await create_domain_agent(intent.target_agent)
    participants.append(target_agent)

    # 跨系统任务添加 OfficeAssistant
    if intent.target_agent != "OfficeAssistant" and mode == CollaborationMode.SWARM:
        office_agent = await create_domain_agent("OfficeAssistant")
        participants.append(office_agent)

    # 需要审核时添加 Reviewer
    if intent.review_required:
        reviewer = await create_domain_agent("Reviewer")
        participants.append(reviewer)

    # 终止条件
    termination = TextMentionTermination("TASK_COMPLETE") | MaxMessageTermination(max_rounds)

    if mode == CollaborationMode.SELECTOR:
        team = SelectorGroupChat(
            participants=participants,
            model_client=get_supervisor_client(),
            termination_condition=termination,
            max_turns=max_rounds,
        )
    else:
        # Swarm 模式将在 Phase 3 完整实现
        team = SelectorGroupChat(
            participants=participants,
            model_client=get_supervisor_client(),
            termination_condition=termination,
            max_turns=max_rounds,
        )

    logger.info(
        "创建团队: mode=%s participants=%s review=%s",
        mode.value,
        [p.name for p in participants],
        intent.review_required,
    )

    return team
