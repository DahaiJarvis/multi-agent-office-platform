"""团队工厂 - 根据协作模式创建 Agent 团队

支持三种协作模式：
  - DIRECT: 单 Agent 直连，用于简单查询
  - SELECTOR: SelectorGroupChat，用于跨系统中等复杂任务
  - SWARM: Swarm 协作，用于复杂多步任务，支持 Handoff 消息传递
"""

import logging
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.teams import SelectorGroupChat, Swarm

from agent.agents.supervisor import (
    CollaborationMode,
    IntentResult,
    SUPERVISOR_SYSTEM_PROMPT,
)
from agent.agents.domain import create_domain_agent, AGENT_PROMPTS
from agent.agents.reviewer import create_reviewer_agent, REVIEWER_SYSTEM_PROMPT
from agent.core.model_client import get_supervisor_client, get_domain_agent_client, get_reviewer_client
from agent.core.mcp_integration import load_agent_tools

logger = logging.getLogger(__name__)

# 各协作模式的最大轮次
MAX_ROUNDS = {
    CollaborationMode.DIRECT: 5,
    CollaborationMode.SELECTOR: 15,
    CollaborationMode.SWARM: 20,
}


async def create_team(intent: IntentResult) -> Any:
    """根据意图结果创建对应的 Agent 团队

    Args:
        intent: 意图分类结果

    Returns:
        Agent 实例（DIRECT 模式）或 Agent 团队实例（SELECTOR/SWARM 模式）
    """
    mode = intent.collaboration_mode
    max_rounds = MAX_ROUNDS[mode]

    if mode == CollaborationMode.DIRECT:
        return await _create_direct_team(intent)

    if mode == CollaborationMode.SELECTOR:
        return await _create_selector_team(intent, max_rounds)

    if mode == CollaborationMode.SWARM:
        return await _create_swarm_team(intent, max_rounds)

    raise ValueError(f"不支持的协作模式: {mode}")


async def _create_direct_team(intent: IntentResult) -> AssistantAgent:
    """创建单 Agent 直连模式

    用于简单查询任务，直接路由到单个领域 Agent。
    """
    agent = await create_domain_agent(intent.target_agent)
    logger.info("创建直连 Agent: %s", intent.target_agent)
    return agent


async def _create_selector_team(intent: IntentResult, max_rounds: int) -> Any:
    """创建 SelectorGroupChat 模式

    用于跨系统中等复杂任务，由 Selector 自动选择合适的 Agent 执行。
    涉及敏感操作时自动注入 Reviewer。
    """
    participants: list[AssistantAgent] = []

    target_agent = await create_domain_agent(intent.target_agent)
    participants.append(target_agent)

    if intent.review_required:
        reviewer = await create_reviewer_agent()
        participants.append(reviewer)

    termination = TextMentionTermination("TASK_COMPLETE") | MaxMessageTermination(max_rounds)

    team = SelectorGroupChat(
        participants=participants,
        model_client=get_supervisor_client(),
        termination_condition=termination,
        max_turns=max_rounds,
    )

    logger.info(
        "创建 SelectorGroupChat: participants=%s review=%s",
        [p.name for p in participants],
        intent.review_required,
    )
    return team


async def _create_swarm_agent_with_handoffs(
    agent_name: str,
    handoff_targets: list[str],
) -> AssistantAgent:
    """创建带 Handoff 配置的 Agent（用于 Swarm 模式）

    Swarm 模式要求 Agent 在构造时指定 handoffs，以便产生 HandoffMessage。
    第一个参与者必须能产生 HandoffMessage。

    Args:
        agent_name: Agent 名称
        handoff_targets: 可移交的目标 Agent 名称列表

    Returns:
        带 handoffs 配置的 AssistantAgent
    """
    if agent_name == "Supervisor":
        return AssistantAgent(
            name="Supervisor",
            model_client=get_supervisor_client(),
            system_message=SUPERVISOR_SYSTEM_PROMPT,
            handoffs=handoff_targets,
        )

    if agent_name == "Reviewer":
        tools = await load_agent_tools("Reviewer")
        return AssistantAgent(
            name="Reviewer",
            model_client=get_reviewer_client(),
            tools=tools,
            system_message=REVIEWER_SYSTEM_PROMPT,
            handoffs=handoff_targets,
        )

    # 领域 Agent
    tools = await load_agent_tools(agent_name)
    system_prompt = AGENT_PROMPTS.get(agent_name, "你是一个办公助手。")
    return AssistantAgent(
        name=agent_name,
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=system_prompt,
        handoffs=handoff_targets,
    )


async def _create_swarm_team(intent: IntentResult, max_rounds: int) -> Any:
    """创建 Swarm 协作模式

    用于复杂多步任务，Agent 之间通过 Handoff 消息传递任务。
    包含 Supervisor 作为 Planner 角色，以及需要的领域 Agent 和 Reviewer。
    """
    # 确定参与者列表
    participant_names: list[str] = ["Supervisor", intent.target_agent]

    if intent.target_agent != "OfficeAssistant":
        participant_names.append("OfficeAssistant")

    if intent.review_required:
        participant_names.append("Reviewer")

    # 为每个 Agent 创建带 handoffs 的实例
    participants: list[AssistantAgent] = []
    for name in participant_names:
        handoff_targets = [n for n in participant_names if n != name]
        agent = await _create_swarm_agent_with_handoffs(name, handoff_targets)
        participants.append(agent)

    termination = (
        TextMentionTermination("TASK_COMPLETE")
        | TextMentionTermination("TEAM_TASK_COMPLETE")
        | MaxMessageTermination(max_rounds)
    )

    team = Swarm(
        participants=participants,
        termination_condition=termination,
    )

    logger.info(
        "创建 Swarm: participants=%s review=%s",
        participant_names,
        intent.review_required,
    )
    return team
