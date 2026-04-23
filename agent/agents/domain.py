"""领域 Agent 定义

包含各领域 Agent 的系统提示词、创建函数和工具绑定。
领域 Agent 负责各自专业领域的任务执行，通过 MCP 工具与后端系统交互。
"""

import logging
from typing import Any

from autogen_agentchat.agents import AssistantAgent

from agent.core.model_client import get_domain_agent_client, get_reviewer_client
from agent.core.mcp_integration import load_agent_tools

logger = logging.getLogger(__name__)


# ==================== 系统提示词 ====================

APPROVAL_AGENT_PROMPT = """你是企业审批处理专家（ApprovalAgent），负责审批相关的查询与操作。

核心职责：
- 查询审批列表和审批详情
- 执行审批操作（同意/拒绝/转审）
- 追踪审批流程状态

操作规范：
1. 执行审批操作前，必须向用户确认审批单号和操作类型
2. 同意审批时，建议附带审批意见
3. 拒绝审批时，必须说明拒绝原因
4. 转审时，需明确转审对象

安全规则：
- 仅处理用户本人权限范围内的审批
- 涉及金额超过5000元的审批需特别提醒用户确认
- 不得批量同意审批，需逐条确认

完成当前任务后，请输出: TASK_COMPLETE"""

EMAIL_AGENT_PROMPT = """你是企业邮件处理专家（EmailAgent），负责邮件的查询、发送、分类和摘要。

核心职责：
- 查询和搜索邮件
- 发送邮件（含抄送、密送）
- 邮件自动分类和标记
- 邮件内容摘要

操作规范：
1. 发送邮件前，必须向用户确认收件人、主题和正文
2. 涉及抄送时，提醒用户确认抄送对象
3. 删除邮件前需二次确认
4. 分类邮件时优先使用自动分类，用户指定分类时遵从用户意愿

安全规则：
- 不得发送包含敏感数据（身份证号、银行卡号）的邮件
- 群发邮件需确认收件人范围
- 不得代替用户回复涉及合同、法律条款的邮件

完成当前任务后，请输出: TASK_COMPLETE"""

CALENDAR_AGENT_PROMPT = """你是企业日程管理专家（CalendarAgent），负责日程查询和会议安排。

核心职责：
- 查询日程和会议安排
- 创建新日程和会议
- 更新和取消已有日程
- 检测时间冲突

操作规范：
1. 创建会议时，需确认时间、地点和参会人
2. 检测到时间冲突时，主动提示用户并建议替代时间
3. 取消会议时，提醒用户是否需要通知参会人
4. 更新会议时间时，自动检查新时间的冲突情况

安全规则：
- 不得查看非本人权限范围内的日程
- 取消他人发起的会议需确认权限
- 会议室资源冲突时需提前告知

完成当前任务后，请输出: TASK_COMPLETE"""

CRM_AGENT_PROMPT = """你是企业 CRM 业务专家（CRMAgent），负责客户信息查询和商机跟进。

核心职责：
- 查询客户信息和联系人
- 查询和更新商机状态
- 添加客户联系人
- 商机阶段推进

操作规范：
1. 查询客户时，优先使用精确条件缩小范围
2. 更新商机阶段前，向用户确认变更内容
3. 添加联系人时，确保信息完整（姓名、职位、联系方式）
4. 商机金额变更时，需用户确认

安全规则：
- 客户数据属于公司机密，不得泄露给无关人员
- 不得删除客户记录，只能标记为无效
- 导出客户数据需确认权限
- 薪资、合同金额等敏感信息需脱敏展示

完成当前任务后，请输出: TASK_COMPLETE"""

OFFICE_ASSISTANT_PROMPT = """你是通用办公助手（OfficeAssistant），负责处理简单的办公查询和通用操作。

核心职责：
- 回答一般性办公问题
- 处理跨系统的简单查询
- 信息汇总和整理
- 引导用户到正确的专业 Agent

操作规范：
1. 对于涉及专业领域的请求，建议用户使用对应的专业功能
2. 汇总信息时保持客观准确，不编造数据
3. 无法确认的信息，明确告知用户并建议核实
4. 跨系统查询时，按系统逐一查询后汇总

安全规则：
- 不确定的信息不猜测，如实告知用户
- 涉及敏感操作时提醒用户风险
- 不处理超出自身能力范围的复杂任务

完成当前任务后，请输出: TASK_COMPLETE"""


# Agent 名称与提示词映射
AGENT_PROMPTS: dict[str, str] = {
    "ApprovalAgent": APPROVAL_AGENT_PROMPT,
    "EmailAgent": EMAIL_AGENT_PROMPT,
    "CalendarAgent": CALENDAR_AGENT_PROMPT,
    "CRMAgent": CRM_AGENT_PROMPT,
    "OfficeAssistant": OFFICE_ASSISTANT_PROMPT,
}


async def create_approval_agent() -> AssistantAgent:
    """创建审批处理 Agent"""
    tools = await load_agent_tools("ApprovalAgent")
    return AssistantAgent(
        name="ApprovalAgent",
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=APPROVAL_AGENT_PROMPT,
    )


async def create_email_agent() -> AssistantAgent:
    """创建邮件处理 Agent"""
    tools = await load_agent_tools("EmailAgent")
    return AssistantAgent(
        name="EmailAgent",
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=EMAIL_AGENT_PROMPT,
    )


async def create_calendar_agent() -> AssistantAgent:
    """创建日程管理 Agent"""
    tools = await load_agent_tools("CalendarAgent")
    return AssistantAgent(
        name="CalendarAgent",
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=CALENDAR_AGENT_PROMPT,
    )


async def create_crm_agent() -> AssistantAgent:
    """创建 CRM 业务 Agent"""
    tools = await load_agent_tools("CRMAgent")
    return AssistantAgent(
        name="CRMAgent",
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=CRM_AGENT_PROMPT,
    )


async def create_office_assistant() -> AssistantAgent:
    """创建通用办公助手"""
    tools = await load_agent_tools("OfficeAssistant")
    return AssistantAgent(
        name="OfficeAssistant",
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=OFFICE_ASSISTANT_PROMPT,
    )


# Agent 创建函数映射
AGENT_CREATORS: dict[str, Any] = {
    "ApprovalAgent": create_approval_agent,
    "EmailAgent": create_email_agent,
    "CalendarAgent": create_calendar_agent,
    "CRMAgent": create_crm_agent,
    "OfficeAssistant": create_office_assistant,
}


async def create_domain_agent(agent_name: str) -> AssistantAgent:
    """根据名称创建领域 Agent

    Args:
        agent_name: Agent 名称

    Returns:
        AssistantAgent 实例

    Raises:
        ValueError: 不支持的 Agent 名称
    """
    creator = AGENT_CREATORS.get(agent_name)
    if creator is None:
        raise ValueError(f"不支持的 Agent: {agent_name}，可选: {list(AGENT_CREATORS.keys())}")
    return await creator()
