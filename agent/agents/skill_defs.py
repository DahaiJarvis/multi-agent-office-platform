"""技能定义与绑定关系

定义系统内置技能（BUILTIN_SKILLS）和 Agent 与技能的绑定关系（AGENT_SKILL_BINDINGS）。
从 domain.py 提取，消除 domain.py 与 skill_adapter.py 之间的循环导入。

此模块不依赖 skill_adapter.py 或 mcp_integration.py，仅包含纯数据定义。
"""

from pydantic import BaseModel, Field


class SkillConfig(BaseModel):
    """内置技能配置

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
# -------------------------------------------------------------------------
# 定义系统内置的技能，每个技能包含：
#   - skill_id: 技能唯一标识
#   - name: 技能名称
#   - description: 技能描述
#   - category: 技能分类
#   - required_tools: 依赖的工具列表（MCP 工具名或原生工具名，需与实际注册名一致）
#   - prompt_extension: 附加提示词
#   - priority: 优先级
# -------------------------------------------------------------------------
BUILTIN_SKILLS: dict[str, SkillConfig] = {
    "email_send": SkillConfig(
        skill_id="email_send",
        name="邮件发送",
        description="撰写和发送邮件，支持收件人选择、附件添加",
        category="communication",
        required_tools=["send_email", "search_emails"],
        prompt_extension="你可以撰写和发送邮件。发送前需确认收件人、主题和正文。",
        priority=7,
    ),
    "email_search": SkillConfig(
        skill_id="email_search",
        name="邮件查询",
        description="搜索和查看邮件，支持关键词和日期筛选",
        category="communication",
        required_tools=["search_emails"],
        prompt_extension="你可以搜索和查看邮件。搜索时支持关键词和日期范围筛选。",
        priority=5,
    ),
    "approval_process": SkillConfig(
        skill_id="approval_process",
        name="审批处理",
        description="查询审批列表、执行审批操作（同意/拒绝/转审）",
        category="workflow",
        required_tools=["query_approval_status", "submit_approval", "approve_request", "reject_request"],
        prompt_extension="你可以处理审批任务。执行审批操作前必须向用户确认。",
        priority=8,
    ),
    "calendar_manage": SkillConfig(
        skill_id="calendar_manage",
        name="日程管理",
        description="创建、查询、修改和删除日程",
        category="management",
        required_tools=["query_schedule"],
        prompt_extension="你可以管理日程安排。创建日程时需确认时间、参与者和主题。",
        priority=6,
    ),
    "crm_query": SkillConfig(
        skill_id="crm_query",
        name="客户查询",
        description="查询客户信息、跟进记录和销售数据",
        category="data",
        required_tools=["query_customer", "query_customer_orders"],
        prompt_extension="你可以查询客户信息和跟进记录。注意保护客户隐私数据。",
        priority=5,
    ),
    "knowledge_search": SkillConfig(
        skill_id="knowledge_search",
        name="知识检索",
        description="从知识库中检索文档和信息",
        category="knowledge",
        required_tools=["search_knowledge", "web_search"],
        prompt_extension="你可以从知识库中检索信息。检索结果需标注来源。",
        priority=6,
    ),
    "finance_query": SkillConfig(
        skill_id="finance_query",
        name="财务查询",
        description="查询报销单据、预算和财务数据",
        category="data",
        required_tools=["query_financial_report", "query_payment_status"],
        prompt_extension="你可以查询财务数据。敏感财务信息需脱敏展示。",
        priority=7,
    ),
    "hr_query": SkillConfig(
        skill_id="hr_query",
        name="人事查询",
        description="查询员工信息、考勤和假期数据",
        category="data",
        required_tools=["query_employee_info", "query_leave_balance"],
        prompt_extension="你可以查询人事信息。员工个人隐私数据需脱敏处理。",
        priority=5,
    ),
}

# Agent 与技能的绑定关系（一个 Agent 可拥有多个技能）
# -------------------------------------------------------------------------
# 定义每个 Agent 绑定的技能列表
# 用于：
#   - 展示 Agent 的能力范围
#   - 用户自定义 Agent 时参考
# -------------------------------------------------------------------------
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
