"""领域 Agent 定义

包含各领域 Agent 的系统提示词、创建函数和工具绑定。
领域 Agent 负责各自专业领域的任务执行，通过 MCP 工具与后端系统交互。
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from autogen_agentchat.agents import AssistantAgent

from agent.core.model_client import get_domain_agent_client
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

HR_AGENT_PROMPT = """你是企业 HR 人事专家（HRAgent），负责人事相关的查询与操作。

核心职责：
- 查询考勤记录和打卡状态
- 提交请假申请
- 查询假期余额
- 查询薪资信息
- 查询员工基本信息和部门成员

操作规范：
1. 提交请假申请前，必须向用户确认请假类型、起止日期和原因
2. 查询薪资信息时，仅限查询本人薪资，不得查询他人薪资
3. 请假申请提交后，提醒用户关注审批进度
4. 查询考勤异常时，建议用户联系HR部门核实

安全规则：
- 薪资数据属于L3级敏感信息，查询结果不得转发或截图
- 不得修改他人考勤记录
- 仅可查询本人和下属的假期余额
- 员工个人隐私信息需脱敏展示

完成当前任务后，请输出: TASK_COMPLETE"""

FINANCE_AGENT_PROMPT = """你是企业财务业务专家（FinanceAgent），负责财务相关的查询与操作。

核心职责：
- 查询报销记录和报销状态
- 提交报销申请
- 查询预算使用情况
- 管理发票信息

操作规范：
1. 提交报销申请前，必须向用户确认报销金额、类别和说明
2. 报销金额超过5000元时，提醒用户需要额外审批
3. 查询预算时，明确展示已用额度和剩余额度
4. 上传发票前，确认发票信息准确无误

安全规则：
- 财务数据属于公司机密，不得泄露给无关人员
- 不得修改已提交的报销记录，需撤回后重新提交
- 发票信息需与实际发票一致，不得虚报
- 预算数据仅限本部门人员查看

完成当前任务后，请输出: TASK_COMPLETE"""

KNOWLEDGE_AGENT_PROMPT = """你是企业知识管理专家（KnowledgeAgent），负责知识库检索、文档处理和智能问答。

核心职责：
- 在知识库中检索相关信息回答用户问题
- 解析和摘要企业文档
- 对比分析多份文档的异同
- 生成结构化研究报告
- 搜索互联网获取实时信息
- 分析图片内容

操作规范：
1. 检索知识库时，优先使用语义检索，必要时结合关键词检索
2. 文档摘要时，根据用户需求选择合适的摘要模式(brief/detailed/key_points)
3. 文档对比时，从核心观点、数据、方法、结论等维度分析
4. 生成报告时，确保内容专业、结构清晰、论据充分
5. 无法从知识库获取答案时，可使用网络搜索补充信息
6. 处理图片时，使用图片分析工具理解图片内容

安全规则：
- 仅检索用户权限范围内的知识库
- 敏感信息需脱敏展示
- 不确定的信息明确告知用户
- 涉及机密文档的操作需确认权限

完成当前任务后，请输出: TASK_COMPLETE"""


# Agent 名称与提示词映射
AGENT_PROMPTS: dict[str, str] = {
    "ApprovalAgent": APPROVAL_AGENT_PROMPT,
    "EmailAgent": EMAIL_AGENT_PROMPT,
    "CalendarAgent": CALENDAR_AGENT_PROMPT,
    "CRMAgent": CRM_AGENT_PROMPT,
    "OfficeAssistant": OFFICE_ASSISTANT_PROMPT,
    "HRAgent": HR_AGENT_PROMPT,
    "FinanceAgent": FINANCE_AGENT_PROMPT,
    "KnowledgeAgent": KNOWLEDGE_AGENT_PROMPT,
}


async def _create_single_agent(agent_name: str) -> AssistantAgent:
    """通用领域 Agent 创建函数

    根据名称查找提示词和工具，创建 AssistantAgent。
    优先从 Prompt Registry 加载外置 Prompt，降级到代码内嵌默认值。

    Args:
        agent_name: Agent 名称，须在 AGENT_PROMPTS 中注册

    Returns:
        AssistantAgent 实例

    Raises:
        ValueError: 不支持的 Agent 名称
    """
    # 优先从 Prompt Registry 加载外置 Prompt
    prompt = await _get_agent_prompt(agent_name)
    if prompt is None:
        raise ValueError(f"不支持的 Agent: {agent_name}，可选: {list(AGENT_PROMPTS.keys())}")
    tools = await load_agent_tools(agent_name)
    return AssistantAgent(
        name=agent_name,
        model_client=get_domain_agent_client(),
        tools=tools,
        system_message=prompt,
    )


async def _get_agent_prompt(agent_name: str) -> str | None:
    """获取 Agent 的 System Prompt

    优先从 Prompt Registry 加载外置版本管理的 Prompt，
    降级到代码内嵌的 AGENT_PROMPTS 默认值。

    Args:
        agent_name: Agent 名称

    Returns:
        System Prompt 字符串，未找到时返回 None
    """
    try:
        from agent.core.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        prompt = await registry.get_prompt(agent_name)
        if prompt:
            logger.debug("Agent %s Prompt 从 Registry 加载", agent_name)
            return prompt
    except Exception as e:
        logger.debug("Prompt Registry 加载失败，降级到默认值: %s", e)

    # 降级到代码内嵌默认值
    return AGENT_PROMPTS.get(agent_name)


# Agent 创建函数映射（统一使用 _create_single_agent）
AGENT_CREATORS: dict[str, Any] = {
    "ApprovalAgent": lambda: _create_single_agent("ApprovalAgent"),
    "EmailAgent": lambda: _create_single_agent("EmailAgent"),
    "CalendarAgent": lambda: _create_single_agent("CalendarAgent"),
    "CRMAgent": lambda: _create_single_agent("CRMAgent"),
    "OfficeAssistant": lambda: _create_single_agent("OfficeAssistant"),
    "HRAgent": lambda: _create_single_agent("HRAgent"),
    "FinanceAgent": lambda: _create_single_agent("FinanceAgent"),
    "KnowledgeAgent": lambda: _create_single_agent("KnowledgeAgent"),
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


# ==================== 技能配置（Skills 轻量化） ====================

class SkillConfig(BaseModel):
    """技能配置

    技能是对 Agent 能力的轻量级描述，用于动态组合 Agent 的能力。
    与 Agent 是多对多关系：一个 Agent 可拥有多个技能，一个技能可被多个 Agent 使用。

    Attributes:
        skill_id: 技能唯一标识
        name: 技能名称
        description: 技能描述
        category: 技能分类（communication/data/workflow/knowledge/management）
        required_tools: 该技能依赖的 MCP 工具列表
        prompt_extension: 附加到系统提示词的技能描述片段
        priority: 优先级（1-10，数值越大越优先）
    """

    skill_id: str = Field(default="")
    name: str = Field(min_length=1, max_length=64, description="技能名称")
    description: str = Field(default="", max_length=512, description="技能描述")
    category: str = Field(default="custom", description="技能分类")
    required_tools: list[str] = Field(default_factory=list, description="依赖的 MCP 工具列表")
    prompt_extension: str = Field(default="", max_length=2048, description="附加提示词片段")
    priority: int = Field(default=5, ge=1, le=10, description="优先级")


# 内置技能注册表
BUILTIN_SKILLS: dict[str, SkillConfig] = {
    "email_send": SkillConfig(
        skill_id="email_send",
        name="邮件发送",
        description="撰写和发送邮件，支持收件人选择、附件添加",
        category="communication",
        required_tools=["email:send", "email:search"],
        prompt_extension="你可以撰写和发送邮件。发送前需确认收件人、主题和正文。",
        priority=7,
    ),
    "email_search": SkillConfig(
        skill_id="email_search",
        name="邮件查询",
        description="搜索和查看邮件，支持关键词和日期筛选",
        category="communication",
        required_tools=["email:search", "email:read"],
        prompt_extension="你可以搜索和查看邮件。搜索时支持关键词和日期范围筛选。",
        priority=5,
    ),
    "approval_process": SkillConfig(
        skill_id="approval_process",
        name="审批处理",
        description="查询审批列表、执行审批操作（同意/拒绝/转审）",
        category="workflow",
        required_tools=["approval:list", "approval:approve", "approval:reject"],
        prompt_extension="你可以处理审批任务。执行审批操作前必须向用户确认。",
        priority=8,
    ),
    "calendar_manage": SkillConfig(
        skill_id="calendar_manage",
        name="日程管理",
        description="创建、查询、修改和删除日程",
        category="management",
        required_tools=["calendar:create", "calendar:query", "calendar:update", "calendar:delete"],
        prompt_extension="你可以管理日程安排。创建日程时需确认时间、参与者和主题。",
        priority=6,
    ),
    "crm_query": SkillConfig(
        skill_id="crm_query",
        name="客户查询",
        description="查询客户信息、跟进记录和销售数据",
        category="data",
        required_tools=["crm:query_customer", "crm:query_followup"],
        prompt_extension="你可以查询客户信息和跟进记录。注意保护客户隐私数据。",
        priority=5,
    ),
    "knowledge_search": SkillConfig(
        skill_id="knowledge_search",
        name="知识检索",
        description="从知识库中检索文档和信息",
        category="knowledge",
        required_tools=["knowledge:search", "knowledge:summarize"],
        prompt_extension="你可以从知识库中检索信息。检索结果需标注来源。",
        priority=6,
    ),
    "finance_query": SkillConfig(
        skill_id="finance_query",
        name="财务查询",
        description="查询报销单据、预算和财务数据",
        category="data",
        required_tools=["finance:query_expense", "finance:query_budget"],
        prompt_extension="你可以查询财务数据。敏感财务信息需脱敏展示。",
        priority=7,
    ),
    "hr_query": SkillConfig(
        skill_id="hr_query",
        name="人事查询",
        description="查询员工信息、考勤和假期数据",
        category="data",
        required_tools=["hr:query_employee", "hr:query_attendance"],
        prompt_extension="你可以查询人事信息。员工个人隐私数据需脱敏处理。",
        priority=5,
    ),
}

# Agent 与技能的绑定关系（一个 Agent 可拥有多个技能）
AGENT_SKILL_BINDINGS: dict[str, list[str]] = {
    "EmailAgent": ["email_send", "email_search"],
    "ApprovalAgent": ["approval_process"],
    "CalendarAgent": ["calendar_manage"],
    "CRMAgent": ["crm_query"],
    "KnowledgeAgent": ["knowledge_search"],
    "FinanceAgent": ["finance_query"],
    "HRAgent": ["hr_query"],
    "OfficeAssistant": ["email_search", "calendar_manage", "knowledge_search"],
}


def get_agent_skills(agent_name: str) -> list[SkillConfig]:
    """获取 Agent 绑定的技能列表

    Args:
        agent_name: Agent 名称

    Returns:
        技能配置列表，按优先级降序排列
    """
    skill_ids = AGENT_SKILL_BINDINGS.get(agent_name, [])
    skills = []
    for sid in skill_ids:
        skill = BUILTIN_SKILLS.get(sid)
        if skill:
            skills.append(skill)
    skills.sort(key=lambda s: s.priority, reverse=True)
    return skills


def register_skill(skill: SkillConfig) -> None:
    """注册新技能到内置技能表

    Args:
        skill: 技能配置
    """
    if not skill.skill_id:
        import uuid
        skill.skill_id = str(uuid.uuid4())
    BUILTIN_SKILLS[skill.skill_id] = skill
    logger.info("注册技能: %s (%s)", skill.name, skill.skill_id)


def bind_skill_to_agent(agent_name: str, skill_id: str) -> None:
    """为 Agent 绑定技能

    Args:
        agent_name: Agent 名称
        skill_id: 技能ID
    """
    if agent_name not in AGENT_SKILL_BINDINGS:
        AGENT_SKILL_BINDINGS[agent_name] = []
    if skill_id not in AGENT_SKILL_BINDINGS[agent_name]:
        AGENT_SKILL_BINDINGS[agent_name].append(skill_id)
        logger.info("绑定技能 %s 到 Agent %s", skill_id, agent_name)
