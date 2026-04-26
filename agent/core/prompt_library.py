"""Prompt 模板库

降低使用门槛、提升首次使用成功率，与 M365 Copilot Prompt Gallery 对齐。

能力：
  - 预置模板：覆盖办公常见场景的 Prompt 模板
  - 分类管理：按业务场景分类组织
  - 变量插值：支持模板变量，动态填充
  - 使用统计：跟踪模板使用频率和效果
  - 自定义模板：用户可创建和分享模板
  - 智能推荐：根据上下文推荐相关模板
"""

import logging
import re
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PromptCategory(str, Enum):
    """模板分类"""

    WRITING = "writing"
    ANALYSIS = "analysis"
    CODING = "coding"
    TRANSLATION = "translation"
    SUMMARY = "summary"
    BRAINSTORM = "brainstorm"
    EMAIL = "email"
    MEETING = "meeting"
    REPORT = "report"
    CUSTOM = "custom"


class PromptVariable(BaseModel):
    """模板变量"""

    name: str = Field(description="变量名")
    display_name: str = Field(default="", description="显示名称")
    description: str = Field(default="", description="变量说明")
    default_value: str = Field(default="", description="默认值")
    required: bool = Field(default=True)
    example: str = Field(default="", description="示例值")


class PromptTemplate(BaseModel):
    """Prompt 模板"""

    template_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=100, description="模板名称")
    description: str = Field(default="", max_length=500, description="模板描述")
    category: PromptCategory = Field(default=PromptCategory.CUSTOM)

    template: str = Field(min_length=1, max_length=8000, description="模板内容，使用 {{变量名}} 插值")
    variables: list[PromptVariable] = Field(default_factory=list)

    is_official: bool = Field(default=False, description="是否官方模板")
    is_public: bool = Field(default=True, description="是否公开")
    created_by: str = Field(default="", description="创建者")

    usage_count: int = Field(default=0, description="使用次数")
    rating: float = Field(default=0.0, ge=0.0, le=5.0, description="评分")
    rating_count: int = Field(default=0, description="评分人数")

    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PromptExecution(BaseModel):
    """模板执行结果"""

    template_id: str
    rendered_prompt: str
    variables_used: dict[str, str]


# ==================== 存储 ====================

_templates_store: dict[str, PromptTemplate] = {}


def _init_default_templates() -> None:
    """初始化官方 Prompt 模板"""
    if _templates_store:
        return

    defaults = [
        PromptTemplate(
            template_id="pt-email-reply",
            name="邮件回复助手",
            description="根据收到的邮件内容，生成专业的回复邮件",
            category=PromptCategory.EMAIL,
            is_official=True,
            template=(
                "请帮我回复以下邮件，要求语气{{tone}}，内容简洁专业。\n\n"
                "收到的邮件：\n{{original_email}}\n\n"
                "我的回复要点：\n{{key_points}}\n\n"
                "请直接生成回复邮件内容，不需要额外解释。"
            ),
            variables=[
                PromptVariable(name="tone", display_name="语气", default_value="正式", required=False,
                               example="正式/友好/委婉"),
                PromptVariable(name="original_email", display_name="原始邮件", required=True,
                               example="Dear Team, ..."),
                PromptVariable(name="key_points", display_name="回复要点", required=True,
                               example="确认收到，将于周五前完成"),
            ],
            tags=["邮件", "回复", "办公"],
        ),
        PromptTemplate(
            template_id="pt-meeting-summary",
            name="会议纪要生成",
            description="根据会议内容自动生成结构化的会议纪要",
            category=PromptCategory.MEETING,
            is_official=True,
            template=(
                "请根据以下会议内容，生成结构化的会议纪要。\n\n"
                "会议主题：{{meeting_topic}}\n"
                "参会人员：{{participants}}\n"
                "会议日期：{{meeting_date}}\n\n"
                "会议内容：\n{{meeting_content}}\n\n"
                "请按以下格式输出：\n"
                "1. 会议概述\n"
                "2. 关键决策\n"
                "3. 行动项（含负责人和截止日期）\n"
                "4. 待跟进事项"
            ),
            variables=[
                PromptVariable(name="meeting_topic", display_name="会议主题", required=True,
                               example="Q3 产品规划"),
                PromptVariable(name="participants", display_name="参会人员", required=True,
                               example="张三、李四、王五"),
                PromptVariable(name="meeting_date", display_name="会议日期", required=True,
                               example="2025-01-15"),
                PromptVariable(name="meeting_content", display_name="会议内容", required=True,
                               example="讨论了Q3的产品路线图..."),
            ],
            tags=["会议", "纪要", "办公"],
        ),
        PromptTemplate(
            template_id="pt-report-weekly",
            name="周报生成",
            description="根据工作内容自动生成周报",
            category=PromptCategory.REPORT,
            is_official=True,
            template=(
                "请根据以下工作内容，生成一份结构清晰的周报。\n\n"
                "姓名：{{name}}\n"
                "部门：{{department}}\n"
                "报告周期：{{period}}\n\n"
                "本周完成工作：\n{{completed_work}}\n\n"
                "遇到的问题：\n{{problems}}\n\n"
                "下周计划：\n{{next_plan}}\n\n"
                "请按以下格式输出：\n"
                "1. 本周工作总结\n"
                "2. 关键成果\n"
                "3. 问题与风险\n"
                "4. 下周工作计划"
            ),
            variables=[
                PromptVariable(name="name", display_name="姓名", required=True, example="张三"),
                PromptVariable(name="department", display_name="部门", required=True, example="产品部"),
                PromptVariable(name="period", display_name="报告周期", required=True, example="2025-W03"),
                PromptVariable(name="completed_work", display_name="完成工作", required=True,
                               example="1. 完成需求评审\n2. 修复3个Bug"),
                PromptVariable(name="problems", display_name="遇到问题", required=False,
                               example="接口响应慢"),
                PromptVariable(name="next_plan", display_name="下周计划", required=True,
                               example="1. 完成V2.0开发\n2. 准备上线"),
            ],
            tags=["周报", "报告", "办公"],
        ),
        PromptTemplate(
            template_id="pt-doc-summary",
            name="文档摘要",
            description="对长文档进行摘要提取，保留关键信息",
            category=PromptCategory.SUMMARY,
            is_official=True,
            template=(
                "请对以下文档进行摘要，要求：\n"
                "- 摘要长度约为原文的{{length_ratio}}\n"
                "- 保留关键数据和结论\n"
                "- 使用{{output_format}}格式输出\n\n"
                "文档内容：\n{{document_content}}"
            ),
            variables=[
                PromptVariable(name="length_ratio", display_name="摘要比例", default_value="20%",
                               required=False, example="20%/30%/10%"),
                PromptVariable(name="output_format", display_name="输出格式", default_value="段落",
                               required=False, example="段落/要点/表格"),
                PromptVariable(name="document_content", display_name="文档内容", required=True,
                               example="（粘贴文档内容）"),
            ],
            tags=["摘要", "文档", "阅读"],
        ),
        PromptTemplate(
            template_id="pt-translation",
            name="专业翻译",
            description="高质量专业翻译，支持行业术语",
            category=PromptCategory.TRANSLATION,
            is_official=True,
            template=(
                "请将以下内容从{{source_lang}}翻译为{{target_lang}}。\n\n"
                "要求：\n"
                "- 使用{{domain}}领域的专业术语\n"
                "- 保持原文的语气和风格\n"
                "- 确保翻译准确、自然\n\n"
                "原文：\n{{source_text}}\n\n"
                "请直接输出翻译结果。"
            ),
            variables=[
                PromptVariable(name="source_lang", display_name="源语言", default_value="英文",
                               required=True, example="英文/日文/韩文"),
                PromptVariable(name="target_lang", display_name="目标语言", default_value="中文",
                               required=True, example="中文/英文"),
                PromptVariable(name="domain", display_name="行业领域", default_value="通用",
                               required=False, example="科技/金融/法律/医疗"),
                PromptVariable(name="source_text", display_name="原文", required=True,
                               example="（粘贴原文）"),
            ],
            tags=["翻译", "多语言"],
        ),
        PromptTemplate(
            template_id="pt-brainstorm",
            name="创意头脑风暴",
            description="围绕主题进行创意发散，生成多种方案",
            category=PromptCategory.BRAINSTORM,
            is_official=True,
            template=(
                "请围绕以下主题进行创意头脑风暴，生成{{count}}个创意方案。\n\n"
                "主题：{{topic}}\n"
                "背景：{{context}}\n"
                "约束条件：{{constraints}}\n\n"
                "要求：\n"
                "- 方案具有创新性和可行性\n"
                "- 每个方案包含简要描述和预期效果\n"
                "- 按创新程度从高到低排列"
            ),
            variables=[
                PromptVariable(name="topic", display_name="主题", required=True,
                               example="提升员工满意度的创新方案"),
                PromptVariable(name="context", display_name="背景", default_value="无", required=False,
                               example="公司规模500人，IT行业"),
                PromptVariable(name="constraints", display_name="约束条件", default_value="无", required=False,
                               example="预算不超过10万"),
                PromptVariable(name="count", display_name="方案数量", default_value="5", required=False,
                               example="5/10"),
            ],
            tags=["创意", "头脑风暴", "方案"],
        ),
        PromptTemplate(
            template_id="pt-data-insight",
            name="数据洞察分析",
            description="对数据进行深度分析，发现趋势和异常",
            category=PromptCategory.ANALYSIS,
            is_official=True,
            template=(
                "请对以下数据进行深度分析，发现关键洞察。\n\n"
                "分析目标：{{analysis_goal}}\n"
                "数据描述：{{data_description}}\n\n"
                "数据内容：\n{{data_content}}\n\n"
                "请从以下维度分析：\n"
                "1. 趋势分析：数据随时间的变化趋势\n"
                "2. 异常检测：是否存在异常值或异常波动\n"
                "3. 相关性：各指标之间的关联关系\n"
                "4. 建议：基于分析结果给出行动建议"
            ),
            variables=[
                PromptVariable(name="analysis_goal", display_name="分析目标", required=True,
                               example="了解销售业绩下滑原因"),
                PromptVariable(name="data_description", display_name="数据描述", required=True,
                               example="2024年Q1-Q4各区域销售数据"),
                PromptVariable(name="data_content", display_name="数据内容", required=True,
                               example="（粘贴数据或描述）"),
            ],
            tags=["数据", "分析", "洞察"],
        ),
        PromptTemplate(
            template_id="pt-code-review",
            name="代码审查",
            description="对代码进行专业审查，发现潜在问题",
            category=PromptCategory.CODING,
            is_official=True,
            template=(
                "请对以下{{language}}代码进行专业审查。\n\n"
                "审查重点：{{focus_areas}}\n\n"
                "代码：\n```{{language}}\n{{code}}\n```\n\n"
                "请从以下方面审查：\n"
                "1. 代码质量：可读性、命名规范、注释\n"
                "2. 安全性：潜在安全漏洞\n"
                "3. 性能：性能瓶颈和优化建议\n"
                "4. 最佳实践：是否符合{{language}}最佳实践\n"
                "5. Bug：潜在逻辑错误"
            ),
            variables=[
                PromptVariable(name="language", display_name="编程语言", default_value="Python",
                               required=True, example="Python/Java/Go/TypeScript"),
                PromptVariable(name="focus_areas", display_name="审查重点", default_value="全部",
                               required=False, example="安全性/性能/代码质量"),
                PromptVariable(name="code", display_name="代码", required=True,
                               example="（粘贴代码）"),
            ],
            tags=["代码", "审查", "开发"],
        ),
    ]

    for tpl in defaults:
        _templates_store[tpl.template_id] = tpl


_init_default_templates()


# ==================== CRUD ====================


def create_template(template: PromptTemplate, created_by: str = "") -> PromptTemplate:
    """创建 Prompt 模板"""
    template.created_by = created_by
    template.is_official = False
    template.created_at = time.time()
    template.updated_at = template.created_at

    _templates_store[template.template_id] = template
    logger.info("Prompt 模板已创建: id=%s name=%s", template.template_id, template.name)
    return template


def get_template(template_id: str) -> PromptTemplate | None:
    """获取模板"""
    return _templates_store.get(template_id)


def list_templates(
    category: PromptCategory | None = None,
    tags: list[str] | None = None,
    keyword: str = "",
    is_official: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[PromptTemplate]:
    """列出模板"""
    templates = list(_templates_store.values())

    if category:
        templates = [t for t in templates if t.category == category]
    if tags:
        templates = [t for t in templates if any(tag in t.tags for tag in tags)]
    if keyword:
        kw_lower = keyword.lower()
        templates = [t for t in templates if kw_lower in t.name.lower() or kw_lower in t.description.lower()]
    if is_official is not None:
        templates = [t for t in templates if t.is_official == is_official]
    if not (category or tags or keyword or is_official is not None):
        templates = [t for t in templates if t.is_public]

    templates.sort(key=lambda t: (not t.is_official, -t.usage_count, -t.rating))
    return templates[offset:offset + limit]


def update_template(template_id: str, updates: dict[str, Any]) -> PromptTemplate | None:
    """更新模板"""
    template = _templates_store.get(template_id)
    if not template:
        return None

    if template.is_official:
        raise ValueError("官方模板不可修改")

    for key, value in updates.items():
        if hasattr(template, key) and key not in ("template_id", "is_official", "created_by", "created_at"):
            setattr(template, key, value)

    template.updated_at = time.time()
    return template


def delete_template(template_id: str) -> bool:
    """删除模板"""
    template = _templates_store.get(template_id)
    if not template:
        return False

    if template.is_official:
        raise ValueError("官方模板不可删除")

    del _templates_store[template_id]
    return True


# ==================== 模板渲染 ====================


def render_template(template_id: str, variables: dict[str, str]) -> PromptExecution | None:
    """渲染模板

    将模板中的 {{变量名}} 替换为实际值。

    Args:
        template_id: 模板ID
        variables: 变量值映射

    Returns:
        PromptExecution 或 None
    """
    template = _templates_store.get(template_id)
    if not template:
        return None

    filled_vars: dict[str, str] = {}
    for var in template.variables:
        value = variables.get(var.name, var.default_value)
        if var.required and not value:
            raise ValueError(f"必填变量 '{var.display_name or var.name}' 未提供值")
        filled_vars[var.name] = value

    rendered = template.template
    for var_name, var_value in filled_vars.items():
        rendered = rendered.replace(f"{{{{{var_name}}}}}", var_value)

    unmatched = re.findall(r"\{\{(\w+)\}\}", rendered)
    for unmatched_var in unmatched:
        rendered = rendered.replace(f"{{{{{unmatched_var}}}}}", "")

    template.usage_count += 1

    return PromptExecution(
        template_id=template_id,
        rendered_prompt=rendered,
        variables_used=filled_vars,
    )


# ==================== 智能推荐 ====================


def recommend_templates(query: str, limit: int = 5) -> list[PromptTemplate]:
    """根据用户输入推荐相关模板

    Args:
        query: 用户输入
        limit: 返回数量

    Returns:
        推荐模板列表
    """
    query_lower = query.lower()
    scored: list[tuple[float, PromptTemplate]] = []

    for tpl in _templates_store.values():
        if not tpl.is_public:
            continue

        score = 0.0

        for tag in tpl.tags:
            if tag.lower() in query_lower:
                score += 2.0

        if tpl.name.lower() in query_lower:
            score += 3.0

        for word in query_lower.split():
            if word in tpl.description.lower():
                score += 1.0
            if word in tpl.template.lower():
                score += 0.5

        score += tpl.usage_count * 0.01
        score += tpl.rating * 0.5

        if score > 0:
            scored.append((score, tpl))

    scored.sort(key=lambda x: -x[0])
    return [tpl for _, tpl in scored[:limit]]


# ==================== 评分 ====================


def rate_template(template_id: str, rating: float) -> PromptTemplate | None:
    """为模板评分

    Args:
        template_id: 模板ID
        rating: 评分 (1-5)

    Returns:
        更新后的模板
    """
    template = _templates_store.get(template_id)
    if not template:
        return None

    total_score = template.rating * template.rating_count + rating
    template.rating_count += 1
    template.rating = round(total_score / template.rating_count, 1)

    return template
