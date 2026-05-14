"""团队工厂 - 根据协作模式创建 Agent 团队

================================================================================
模块职责
================================================================================

根据意图分类结果（IntentResult）创建对应的 Agent 团队实例。

================================================================================
支持的协作模式
================================================================================

基础模式（由 AutoGen 框架提供）：
  - DIRECT: 单 Agent 直连
    用于简单查询任务，直接路由到单个领域 Agent
    示例："查看待审批列表" -> ApprovalAgent

  - SELECTOR: SelectorGroupChat 模式
    用于跨系统中等复杂任务，由 Selector 自动选择合适的 Agent 执行
    涉及敏感操作时自动注入 Reviewer
    示例："发送邮件给张总" -> EmailAgent + Reviewer

  - SWARM: Swarm 协作模式
    用于复杂多步任务，Agent 之间通过 Handoff 消息传递任务
    包含 Supervisor 作为 Planner 角色
    示例："分析项目可行性并生成报告" -> Supervisor -> KnowledgeAgent -> Reviewer

高级编排模式（自研实现，在 advanced_orchestration.py 中）：
  - PARALLEL: 并行执行，多 Agent 同时处理同一任务
  - DEBATE: 辩论模式，多 Agent 从不同角度讨论达成共识
  - VOTE: 投票模式，多 Agent 独立给出答案，多数决定

================================================================================
模式选择规则
================================================================================

意图分类时，根据 intent 字段和 collaboration_mode 字段决定：

| 意图类型 | collaboration_mode | 创建的团队 |
|---------|-------------------|-----------|
| approval_query | DIRECT | ApprovalAgent |
| email_send | SELECTOR | EmailAgent + Reviewer |
| cross_system | SWARM | 高级编排 PARALLEL |
| complex_task | SWARM | 高级编排 DEBATE/VOTE |

================================================================================
与其他模块的关系
================================================================================

- agent.agents.supervisor：提供 Supervisor Agent 和意图分类结果
- agent.agents.domain：提供领域 Agent 创建能力
- agent.agents.reviewer：提供 Reviewer Agent 创建能力
- agent.core.model_client：提供模型客户端
- agent.core.mcp_integration：提供 MCP 工具加载能力
- agent.teams.advanced_orchestration：提供高级编排模式

================================================================================
使用示例
================================================================================

    from agent.agents.supervisor import IntentResult, CollaborationMode

    # 创建 DIRECT 模式团队
    intent = IntentResult(
        intent="approval_query",
        confidence=0.95,
        target_agent="ApprovalAgent",
        collaboration_mode=CollaborationMode.DIRECT,
        review_required=False,
    )
    agent = await create_team(intent)

    # 创建 SELECTOR 模式团队
    intent = IntentResult(
        intent="email_send",
        confidence=0.90,
        target_agent="EmailAgent",
        collaboration_mode=CollaborationMode.SELECTOR,
        review_required=True,
    )
    team = await create_team(intent)
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
# 用于防止无限循环，超过最大轮次后强制终止
MAX_ROUNDS = {
    CollaborationMode.DIRECT: 5,      # 单 Agent 模式，轮次较少
    CollaborationMode.SELECTOR: 15,   # 多 Agent 协作，允许更多轮次
    CollaborationMode.SWARM: 20,      # 复杂任务，允许最多轮次
}


async def create_team(intent: IntentResult) -> Any:
    """根据意图结果创建对应的 Agent 团队

    这是团队工厂的主入口函数，根据意图的 collaboration_mode 创建对应的团队。

    创建逻辑：
    -------------------------------------------------------------------------
    1. 如果意图是 cross_system 或 complex_task：
       - 调用 advanced_orchestration.create_advanced_team()
       - 使用高级编排模式（PARALLEL/DEBATE/VOTE）

    2. 如果 collaboration_mode 是 DIRECT：
       - 创建单个领域 Agent
       - 直接返回 Agent 实例

    3. 如果 collaboration_mode 是 SELECTOR：
       - 创建 SelectorGroupChat 团队
       - 包含目标 Agent + Reviewer（如果需要审核）

    4. 如果 collaboration_mode 是 SWARM：
       - 创建 Swarm 团队
       - 包含 Supervisor + 目标 Agent + OfficeAssistant + Reviewer（如果需要审核）
    -------------------------------------------------------------------------

    Args:
        intent: 意图分类结果，包含以下关键字段：
            - intent: 意图标签（如 approval_query、cross_system）
            - target_agent: 目标 Agent 名称
            - collaboration_mode: 协作模式（DIRECT/SELECTOR/SWARM）
            - review_required: 是否需要审核

    Returns:
        Agent 实例（DIRECT 模式）或 Agent 团队实例（SELECTOR/SWARM/高级编排模式）

    Raises:
        ValueError: 不支持的协作模式

    示例：
        # DIRECT 模式返回单个 Agent
        intent = IntentResult(
            intent="approval_query",
            target_agent="ApprovalAgent",
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )
        agent = await create_team(intent)
        # agent 是 AssistantAgent 实例

        # SELECTOR 模式返回团队
        intent = IntentResult(
            intent="email_send",
            target_agent="EmailAgent",
            collaboration_mode=CollaborationMode.SELECTOR,
            review_required=True,
        )
        team = await create_team(intent)
        # team 是 SelectorGroupChat 实例
    """
    # 高级编排模式判断
    # cross_system 和 complex_task 使用高级编排（PARALLEL/DEBATE/VOTE）
    if intent.intent in ("cross_system", "complex_task"):
        from agent.teams.advanced_orchestration import create_advanced_team
        return await create_advanced_team(intent)

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

    特点：
    - 只有一个 Agent，无协作
    - 无需 Reviewer（简单查询不涉及敏感操作）
    - 响应速度快，延迟低

    适用场景：
    - 查询类操作（查看待审批、查看邮件、查看日程）
    - 知识库检索
    - 简单问答

    Args:
        intent: 意图分类结果，target_agent 指定要创建的 Agent

    Returns:
        领域 Agent 实例（AssistantAgent）
    """
    agent = await create_domain_agent(intent.target_agent)
    logger.info("创建直连 Agent: %s", intent.target_agent)
    return agent


async def _create_selector_team(intent: IntentResult, max_rounds: int) -> Any:
    """创建 SelectorGroupChat 模式

    用于跨系统中等复杂任务，由 Selector 自动选择合适的 Agent 执行。

    工作原理：
    -------------------------------------------------------------------------
    1. 用户发送消息
    2. Selector（使用 LLM）分析消息，选择最合适的 Agent
    3. 被选中的 Agent 执行任务
    4. 如果需要审核，Reviewer 检查执行结果
    5. 返回最终结果
    -------------------------------------------------------------------------

    特点：
    - 自动选择 Agent，无需手动指定
    - 支持多轮对话
    - 涉及敏感操作时自动注入 Reviewer

    适用场景：
    - 发送邮件（需要审核）
    - 提交审批（需要审核）
    - 创建日程（需要审核）

    Args:
        intent: 意图分类结果
        max_rounds: 最大轮次，防止无限循环

    Returns:
        SelectorGroupChat 团队实例
    """
    participants: list[AssistantAgent] = []

    # 创建目标 Agent
    target_agent = await create_domain_agent(intent.target_agent)
    participants.append(target_agent)

    # 如果需要审核，注入 Reviewer
    # Reviewer 负责检查敏感操作（如发送邮件、提交审批）
    if intent.review_required:
        reviewer = await create_reviewer_agent()
        participants.append(reviewer)

    # 终止条件：输出 "TASK_COMPLETE" 或达到最大消息数
    termination = TextMentionTermination("TASK_COMPLETE") | MaxMessageTermination(max_rounds)

    team = SelectorGroupChat(
        participants=participants,
        model_client=get_supervisor_client(),  # Selector 使用 Supervisor 的模型
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
    Handoff 是 Swarm 模式的核心机制，允许 Agent 将任务移交给其他 Agent。

    Handoff 工作原理：
    -------------------------------------------------------------------------
    1. Agent A 执行任务
    2. Agent A 发现需要其他 Agent 协助
    3. Agent A 产生 HandoffMessage，指定目标 Agent
    4. 系统将任务移交给 Agent B
    5. Agent B 继续执行
    -------------------------------------------------------------------------

    Args:
        agent_name: Agent 名称（如 Supervisor、KnowledgeAgent、Reviewer）
        handoff_targets: 可移交的目标 Agent 名称列表

    Returns:
        带 handoffs 配置的 AssistantAgent
    """
    if agent_name == "Supervisor":
        # Supervisor 是规划者，负责将任务分配给合适的领域 Agent
        return AssistantAgent(
            name="Supervisor",
            model_client=get_supervisor_client(),
            system_message=SUPERVISOR_SYSTEM_PROMPT,
            handoffs=handoff_targets,
        )

    if agent_name == "Reviewer":
        # Reviewer 是审核者，负责检查敏感操作
        tools = await load_agent_tools("Reviewer")
        return AssistantAgent(
            name="Reviewer",
            model_client=get_reviewer_client(),
            tools=tools,
            system_message=REVIEWER_SYSTEM_PROMPT,
            handoffs=handoff_targets,
            reflect_on_tool_use=True,
            max_tool_iterations=5,
        )

    # 领域 Agent（如 KnowledgeAgent、EmailAgent 等）
    tools = await load_agent_tools(agent_name)
    system_prompt = AGENT_PROMPTS.get(agent_name, "你是一个办公助手。")
    return AssistantAgent(
        name=agent_name,
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=system_prompt,
        handoffs=handoff_targets,
        reflect_on_tool_use=True,
        max_tool_iterations=5,
    )


async def _create_swarm_team(intent: IntentResult, max_rounds: int) -> Any:
    """创建 Swarm 协作模式

    用于复杂多步任务，Agent 之间通过 Handoff 消息传递任务。

    工作原理：
    -------------------------------------------------------------------------
    1. Supervisor 接收用户请求，分析并规划任务
    2. Supervisor 通过 Handoff 将任务移交给合适的领域 Agent
    3. 领域 Agent 执行任务
    4. 如果需要审核，Reviewer 检查执行结果
    5. 完成后输出 "TASK_COMPLETE"
    -------------------------------------------------------------------------

    参与者：
    - Supervisor：规划者，负责任务分解和分配
    - 目标 Agent：主要执行者（如 KnowledgeAgent）
    - OfficeAssistant：通用助手，处理非特定领域的请求
    - Reviewer：审核者，检查敏感操作（如果需要）

    特点：
    - 支持复杂多步任务
    - Agent 之间可以相互移交任务
    - 灵活性高，适应性强

    适用场景：
    - 复杂分析任务（如项目可行性分析）
    - 跨系统操作（如查 CRM + 发邮件 + 更新审批）
    - 多步骤工作流

    Args:
        intent: 意图分类结果
        max_rounds: 最大轮次，防止无限循环

    Returns:
        Swarm 团队实例
    """
    # 确定参与者列表
    participant_names: list[str] = ["Supervisor", intent.target_agent]

    # 如果目标 Agent 不是 OfficeAssistant，添加 OfficeAssistant 作为备用
    if intent.target_agent != "OfficeAssistant":
        participant_names.append("OfficeAssistant")

    # 如果需要审核，添加 Reviewer
    if intent.review_required:
        participant_names.append("Reviewer")

    # 为每个 Agent 创建带 handoffs 的实例
    participants: list[AssistantAgent] = []
    for name in participant_names:
        # 每个 Agent 可以移交给其他所有 Agent
        handoff_targets = [n for n in participant_names if n != name]
        agent = await _create_swarm_agent_with_handoffs(name, handoff_targets)
        participants.append(agent)

    # 终止条件：输出 "TASK_COMPLETE" 或 "TEAM_TASK_COMPLETE" 或达到最大消息数
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
