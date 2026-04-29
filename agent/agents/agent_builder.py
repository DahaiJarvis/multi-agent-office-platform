"""Agent Builder - 自定义 Agent 创建与管理

企业用户可根据自身业务需求创建自定义 Agent，无需开发团队介入。
支持：
  - 自定义系统提示词
  - 选择绑定的 MCP 工具
  - 配置模型级别
  - 设置温度等推理参数
  - 版本管理与发布控制
  - Agent 模板市场

与 AgentForce Builder、Coze 可视化构建器对齐。
"""

import hashlib
import logging
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    """Agent 生命周期状态"""

    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class ModelTier(str, Enum):
    """模型级别"""

    MAX = "max"
    PLUS = "plus"
    TURBO = "turbo"


class CustomAgentConfig(BaseModel):
    """自定义 Agent 配置"""

    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=64, description="Agent 名称，如 'ContractReviewAgent'")
    display_name: str = Field(default="", description="Agent 显示名称")
    description: str = Field(default="", max_length=512, description="Agent 功能描述")
    version: int = Field(default=1, description="配置版本号")
    status: AgentStatus = Field(default=AgentStatus.DRAFT)

    system_prompt: str = Field(min_length=10, max_length=8192, description="系统提示词")
    mcp_servers: list[str] = Field(default_factory=list, description="绑定的 MCP 服务列表")
    model_tier: ModelTier = Field(default=ModelTier.PLUS, description="模型级别")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="推理温度")
    max_rounds: int = Field(default=10, ge=1, le=50, description="最大对话轮次")

    created_by: str = Field(default="", description="创建者用户ID")
    created_at: float = Field(default_factory=time.time, description="创建时间戳")
    updated_at: float = Field(default_factory=time.time, description="最后更新时间戳")
    published_at: float | None = Field(default=None, description="发布时间戳")

    review_required: bool = Field(default=False, description="是否需要安全审核")
    allowed_roles: list[str] = Field(default_factory=lambda: ["employee"], description="允许使用的角色")

    tags: list[str] = Field(default_factory=list, description="标签，便于分类检索")
    icon: str = Field(default="", description="Agent 图标标识")

    canary_percentage: int = Field(default=0, ge=0, le=100, description="灰度发布百分比，0表示全量发布")
    canary_whitelist: list[str] = Field(default_factory=list, description="灰度白名单用户ID列表")
    published_version: int = Field(default=0, description="当前已发布的版本号，0表示未发布")


class AgentTemplate(BaseModel):
    """Agent 模板"""

    template_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(description="模板名称")
    description: str = Field(default="", description="模板描述")
    category: str = Field(default="general", description="模板分类")
    config: CustomAgentConfig = Field(description="模板配置")
    usage_count: int = Field(default=0, description="使用次数")
    is_official: bool = Field(default=False, description="是否官方模板")


class AgentVersionDiff(BaseModel):
    """Agent 版本差异"""

    field: str
    old_value: str
    new_value: str


class AgentVersionRecord(BaseModel):
    """Agent 版本记录"""

    agent_id: str
    version: int
    config: CustomAgentConfig
    diff_from_previous: list[AgentVersionDiff] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    created_by: str = ""


# ==================== 存储 ====================

_agents_store: dict[str, CustomAgentConfig] = {}
_versions_store: dict[str, list[AgentVersionRecord]] = {}
_templates_store: dict[str, AgentTemplate] = {}


def _init_default_templates() -> None:
    """初始化官方 Agent 模板"""
    if _templates_store:
        return

    templates = [
        AgentTemplate(
            template_id="tpl-contract-review",
            name="合同审查助手",
            description="自动审查合同条款，识别风险点并给出修改建议",
            category="legal",
            is_official=True,
            config=CustomAgentConfig(
                name="ContractReviewAgent",
                display_name="合同审查助手",
                description="自动审查合同条款，识别风险点并给出修改建议",
                system_prompt=(
                    "你是企业合同审查专家（ContractReviewAgent），负责审查合同条款并识别风险。\n\n"
                    "核心职责：\n"
                    "- 逐条审查合同条款，识别法律风险和商业风险\n"
                    "- 对比公司标准合同模板，标注偏差条款\n"
                    "- 给出具体修改建议和风险等级评估\n"
                    "- 检查合规性要求（如数据保护、知识产权、竞业限制等）\n\n"
                    "审查维度：\n"
                    "1. 法律合规性：是否符合相关法律法规\n"
                    "2. 商业合理性：条款是否对等、是否有利于公司利益\n"
                    "3. 风险识别：潜在的法律纠纷、财务风险\n"
                    "4. 完整性检查：是否缺少必要条款\n\n"
                    "输出格式：\n"
                    "- 风险等级：高/中/低\n"
                    "- 条款编号及原文\n"
                    "- 风险说明\n"
                    "- 修改建议\n\n"
                    "完成审查后，请输出: TASK_COMPLETE"
                ),
                mcp_servers=["doc", "oa"],
                model_tier=ModelTier.MAX,
                temperature=0.3,
                review_required=True,
                tags=["法务", "合同", "审查"],
                icon="legal",
            ),
        ),
        AgentTemplate(
            template_id="tpl-it-support",
            name="IT 技术支持",
            description="处理企业内部 IT 工单，提供技术支持和故障排查",
            category="it",
            is_official=True,
            config=CustomAgentConfig(
                name="ITSupportAgent",
                display_name="IT 技术支持",
                description="处理企业内部 IT 工单，提供技术支持和故障排查",
                system_prompt=(
                    "你是企业 IT 技术支持专家（ITSupportAgent），负责处理内部技术工单。\n\n"
                    "核心职责：\n"
                    "- 接收和分类 IT 工单\n"
                    "- 提供常见问题的解决方案\n"
                    "- 远程排查网络、设备、软件故障\n"
                    "- 升级复杂问题到二线支持\n\n"
                    "处理流程：\n"
                    "1. 确认问题类型（网络/设备/软件/账号/安全）\n"
                    "2. 查询知识库中的解决方案\n"
                    "3. 提供分步排查指引\n"
                    "4. 记录解决方案并更新知识库\n"
                    "5. 无法解决时升级到专业团队\n\n"
                    "完成当前任务后，请输出: TASK_COMPLETE"
                ),
                mcp_servers=["oa", "im"],
                model_tier=ModelTier.PLUS,
                temperature=0.5,
                tags=["IT", "技术支持", "工单"],
                icon="it",
            ),
        ),
        AgentTemplate(
            template_id="tpl-onboarding",
            name="新员工入职引导",
            description="引导新员工完成入职流程，解答常见问题",
            category="hr",
            is_official=True,
            config=CustomAgentConfig(
                name="OnboardingAgent",
                display_name="新员工入职引导",
                description="引导新员工完成入职流程，解答常见问题",
                system_prompt=(
                    "你是企业新员工入职引导助手（OnboardingAgent），负责帮助新员工顺利入职。\n\n"
                    "核心职责：\n"
                    "- 引导新员工完成入职手续\n"
                    "- 介绍公司制度、福利、文化\n"
                    "- 协助开通各类系统账号\n"
                    "- 解答新员工常见问题\n\n"
                    "引导内容：\n"
                    "1. 入职材料准备与提交\n"
                    "2. 系统账号开通（邮箱、OA、IM等）\n"
                    "3. 办公设备领取\n"
                    "4. 部门介绍与同事认识\n"
                    "5. 公司制度与福利说明\n"
                    "6. 试用期注意事项\n\n"
                    "完成当前任务后，请输出: TASK_COMPLETE"
                ),
                mcp_servers=["hr", "oa", "im"],
                model_tier=ModelTier.PLUS,
                temperature=0.7,
                tags=["HR", "入职", "引导"],
                icon="hr",
            ),
        ),
        AgentTemplate(
            template_id="tpl-data-analyst",
            name="数据分析助手",
            description="自然语言驱动的数据查询与分析，生成数据洞察报告",
            category="analytics",
            is_official=True,
            config=CustomAgentConfig(
                name="DataAnalystAgent",
                display_name="数据分析助手",
                description="自然语言驱动的数据查询与分析，生成数据洞察报告",
                system_prompt=(
                    "你是企业数据分析专家（DataAnalystAgent），负责用自然语言驱动的数据查询与分析。\n\n"
                    "核心职责：\n"
                    "- 将自然语言查询转换为数据查询\n"
                    "- 执行数据统计与聚合分析\n"
                    "- 生成数据洞察和趋势分析\n"
                    "- 提供可视化建议\n\n"
                    "分析流程：\n"
                    "1. 理解用户的数据分析需求\n"
                    "2. 确定所需数据源和查询范围\n"
                    "3. 执行数据查询和统计计算\n"
                    "4. 分析数据趋势和异常\n"
                    "5. 生成分析报告和可视化建议\n\n"
                    "安全规则：\n"
                    "- 仅查询用户权限范围内的数据\n"
                    "- 不暴露原始敏感数据，必要时脱敏\n"
                    "- 涉及个人数据的查询需确认合规性\n\n"
                    "完成当前任务后，请输出: TASK_COMPLETE"
                ),
                mcp_servers=["finance", "crm"],
                model_tier=ModelTier.MAX,
                temperature=0.3,
                review_required=True,
                tags=["数据", "分析", "报表"],
                icon="analytics",
            ),
        ),
    ]

    for tpl in templates:
        _templates_store[tpl.template_id] = tpl


_init_default_templates()


# ==================== Agent CRUD ====================


def create_custom_agent(config: CustomAgentConfig, created_by: str) -> CustomAgentConfig:
    """创建自定义 Agent

    Args:
        config: Agent 配置
        created_by: 创建者用户ID

    Returns:
        创建后的 Agent 配置
    """
    config.created_by = created_by
    config.status = AgentStatus.DRAFT
    config.version = 1
    config.created_at = time.time()
    config.updated_at = config.created_at

    _agents_store[config.agent_id] = config

    _save_version(config, created_by)

    logger.info("自定义 Agent 已创建: id=%s name=%s by=%s", config.agent_id, config.name, created_by)
    return config


def get_custom_agent(agent_id: str) -> CustomAgentConfig | None:
    """获取自定义 Agent 配置"""
    return _agents_store.get(agent_id)


def list_custom_agents(
    created_by: str = "",
    status: AgentStatus | None = None,
    tags: list[str] | None = None,
) -> list[CustomAgentConfig]:
    """列出自定义 Agent

    Args:
        created_by: 按创建者过滤
        status: 按状态过滤
        tags: 按标签过滤

    Returns:
        Agent 配置列表
    """
    agents = list(_agents_store.values())

    if created_by:
        agents = [a for a in agents if a.created_by == created_by]
    if status:
        agents = [a for a in agents if a.status == status]
    if tags:
        agents = [a for a in agents if any(t in a.tags for t in tags)]

    agents.sort(key=lambda a: a.updated_at, reverse=True)
    return agents


def update_custom_agent(
    agent_id: str,
    updates: dict[str, Any],
    updated_by: str,
) -> CustomAgentConfig | None:
    """更新自定义 Agent 配置

    更新时自动创建新版本记录。

    Args:
        agent_id: Agent ID
        updates: 更新字段字典
        updated_by: 更新者用户ID

    Returns:
        更新后的 Agent 配置，或 None（Agent 不存在）
    """
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    if agent.status == AgentStatus.ARCHIVED:
        raise ValueError("已归档的 Agent 不可修改")

    old_config = agent.model_copy()

    for key, value in updates.items():
        if hasattr(agent, key) and key not in ("agent_id", "created_by", "created_at"):
            setattr(agent, key, value)

    agent.version += 1
    agent.updated_at = time.time()

    if agent.status == AgentStatus.PUBLISHED:
        agent.status = AgentStatus.DRAFT
        agent.published_at = None

    diff = _compute_diff(old_config, agent)
    _save_version(agent, updated_by, diff)

    logger.info("自定义 Agent 已更新: id=%s version=%d by=%s", agent_id, agent.version, updated_by)
    return agent


def publish_custom_agent(agent_id: str, published_by: str) -> CustomAgentConfig | None:
    """发布自定义 Agent

    只有 DRAFT 状态的 Agent 可以发布。发布后用户可使用。

    Args:
        agent_id: Agent ID
        published_by: 发布者用户ID

    Returns:
        发布后的 Agent 配置，或 None
    """
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    if agent.status != AgentStatus.DRAFT:
        raise ValueError(f"只有 DRAFT 状态的 Agent 可以发布，当前状态: {agent.status}")

    agent.status = AgentStatus.PUBLISHED
    agent.published_at = time.time()
    agent.updated_at = agent.published_at

    _register_to_runtime(agent)

    logger.info("自定义 Agent 已发布: id=%s name=%s by=%s", agent_id, agent.name, published_by)
    return agent


def disable_custom_agent(agent_id: str) -> CustomAgentConfig | None:
    """禁用自定义 Agent"""
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    agent.status = AgentStatus.DISABLED
    agent.updated_at = time.time()

    _unregister_from_runtime(agent)

    logger.info("自定义 Agent 已禁用: id=%s", agent_id)
    return agent


def archive_custom_agent(agent_id: str) -> CustomAgentConfig | None:
    """归档自定义 Agent"""
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    agent.status = AgentStatus.ARCHIVED
    agent.updated_at = time.time()

    _unregister_from_runtime(agent)

    logger.info("自定义 Agent 已归档: id=%s", agent_id)
    return agent


def delete_custom_agent(agent_id: str) -> bool:
    """删除自定义 Agent（仅限 DRAFT 或 ARCHIVED 状态）"""
    agent = _agents_store.get(agent_id)
    if not agent:
        return False

    if agent.status not in (AgentStatus.DRAFT, AgentStatus.ARCHIVED):
        raise ValueError(f"只有 DRAFT 或 ARCHIVED 状态的 Agent 可以删除，当前状态: {agent.status}")

    del _agents_store[agent_id]
    _versions_store.pop(agent_id, None)

    _unregister_from_runtime(agent)

    logger.info("自定义 Agent 已删除: id=%s", agent_id)
    return True


# ==================== 版本管理 ====================


def get_agent_versions(agent_id: str) -> list[AgentVersionRecord]:
    """获取 Agent 的版本历史"""
    return _versions_store.get(agent_id, [])


def get_agent_version(agent_id: str, version: int) -> AgentVersionRecord | None:
    """获取 Agent 的指定版本"""
    versions = _versions_store.get(agent_id, [])
    for v in versions:
        if v.version == version:
            return v
    return None


def rollback_agent_version(agent_id: str, version: int, rolled_back_by: str) -> CustomAgentConfig | None:
    """回滚 Agent 到指定版本"""
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    target_version = get_agent_version(agent_id, version)
    if not target_version:
        raise ValueError(f"版本 {version} 不存在")

    old_config = agent.model_copy()

    restored = target_version.config.model_copy()
    restored.version = agent.version + 1
    restored.updated_at = time.time()
    restored.status = AgentStatus.DRAFT
    restored.published_at = None

    diff = _compute_diff(old_config, restored)
    _agents_store[agent_id] = restored
    _save_version(restored, rolled_back_by, diff)

    logger.info("自定义 Agent 已回滚: id=%s to_version=%d by=%s", agent_id, version, rolled_back_by)
    return restored


# ==================== 模板管理 ====================


def list_templates(category: str = "") -> list[AgentTemplate]:
    """列出 Agent 模板

    Args:
        category: 按分类过滤

    Returns:
        模板列表
    """
    templates = list(_templates_store.values())
    if category:
        templates = [t for t in templates if t.category == category]
    templates.sort(key=lambda t: (not t.is_official, -t.usage_count))
    return templates


def get_template(template_id: str) -> AgentTemplate | None:
    """获取模板详情"""
    return _templates_store.get(template_id)


def create_from_template(template_id: str, name: str, created_by: str, overrides: dict[str, Any] | None = None) -> CustomAgentConfig | None:
    """从模板创建自定义 Agent

    Args:
        template_id: 模板ID
        name: Agent 名称
        created_by: 创建者用户ID
        overrides: 覆盖的配置字段

    Returns:
        创建后的 Agent 配置
    """
    template = _templates_store.get(template_id)
    if not template:
        return None

    config = template.config.model_copy()
    config.agent_id = str(uuid.uuid4())
    config.name = name
    config.version = 1
    config.status = AgentStatus.DRAFT
    config.created_by = created_by
    config.created_at = time.time()
    config.updated_at = config.created_at
    config.published_at = None

    if overrides:
        for key, value in overrides.items():
            if hasattr(config, key) and key not in ("agent_id", "created_by", "created_at"):
                setattr(config, key, value)

    template.usage_count += 1

    _agents_store[config.agent_id] = config
    _save_version(config, created_by)

    logger.info("从模板创建自定义 Agent: template=%s name=%s by=%s", template_id, name, created_by)
    return config


# ==================== 运行时注册 ====================


def _register_to_runtime(agent: CustomAgentConfig) -> None:
    """将自定义 Agent 注册到运行时

    注册后，Supervisor 的意图路由可识别该 Agent，
    team_factory 可创建该 Agent 的实例。
    """
    from agent.agents.domain import AGENT_PROMPTS, AGENT_CREATORS
    from agent.core.mcp_integration import AGENT_TOOL_BINDINGS

    AGENT_PROMPTS[agent.name] = agent.system_prompt
    AGENT_TOOL_BINDINGS[agent.name] = agent.mcp_servers

    async def _create_custom() -> Any:
        from autogen_agentchat.agents import AssistantAgent
        from agent.core.model_client import get_model_client

        tools = await _load_custom_agent_tools(agent.name)
        return AssistantAgent(
            name=agent.name,
            model_client=get_model_client(agent.model_tier.value),
            tools=tools,
            system_message=agent.system_prompt,
        )

    AGENT_CREATORS[agent.name] = _create_custom

    logger.info("自定义 Agent 已注册到运行时: %s", agent.name)


def _unregister_from_runtime(agent: CustomAgentConfig) -> None:
    """从运行时注销自定义 Agent"""
    from agent.agents.domain import AGENT_PROMPTS, AGENT_CREATORS
    from agent.core.mcp_integration import AGENT_TOOL_BINDINGS

    AGENT_PROMPTS.pop(agent.name, None)
    AGENT_CREATORS.pop(agent.name, None)
    AGENT_TOOL_BINDINGS.pop(agent.name, None)

    logger.info("自定义 Agent 已从运行时注销: %s", agent.name)


async def _load_custom_agent_tools(agent_name: str) -> list[Any]:
    """加载自定义 Agent 的工具"""
    from agent.core.mcp_integration import load_agent_tools
    return await load_agent_tools(agent_name)


def register_all_published_agents() -> None:
    """应用启动时注册所有已发布的自定义 Agent"""
    for agent in _agents_store.values():
        if agent.status == AgentStatus.PUBLISHED:
            try:
                _register_to_runtime(agent)
            except Exception as e:
                logger.error("注册自定义 Agent 失败: name=%s error=%s", agent.name, e)


# ==================== 内部工具 ====================


def _save_version(config: CustomAgentConfig, created_by: str, diff: list[AgentVersionDiff] | None = None) -> None:
    """保存版本记录"""
    record = AgentVersionRecord(
        agent_id=config.agent_id,
        version=config.version,
        config=config.model_copy(),
        diff_from_previous=diff or [],
        created_by=created_by,
    )

    if config.agent_id not in _versions_store:
        _versions_store[config.agent_id] = []
    _versions_store[config.agent_id].append(record)


def _compute_diff(old: CustomAgentConfig, new: CustomAgentConfig) -> list[AgentVersionDiff]:
    """计算两个版本之间的差异"""
    diffs: list[AgentVersionDiff] = []
    diff_fields = [
        "system_prompt", "mcp_servers", "model_tier", "temperature",
        "max_rounds", "review_required", "allowed_roles", "description",
        "display_name",
    ]

    for field_name in diff_fields:
        old_val = getattr(old, field_name, None)
        new_val = getattr(new, field_name, None)
        if old_val != new_val:
            diffs.append(AgentVersionDiff(
                field=field_name,
                old_value=str(old_val),
                new_value=str(new_val),
            ))

    return diffs


# ==================== 灰度发布 ====================


def publish_with_canary(
    agent_id: str,
    published_by: str,
    canary_percentage: int = 0,
    canary_whitelist: list[str] | None = None,
) -> CustomAgentConfig | None:
    """灰度发布 Agent

    支持按百分比和白名单进行灰度发布。

    Args:
        agent_id: Agent ID
        published_by: 发布者用户ID
        canary_percentage: 灰度百分比 (0-100)，0 表示全量发布
        canary_whitelist: 灰度白名单用户ID列表

    Returns:
        发布后的 Agent 配置
    """
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    if agent.status != AgentStatus.DRAFT:
        raise ValueError(f"只有 DRAFT 状态的 Agent 可以发布，当前状态: {agent.status}")

    agent.status = AgentStatus.PUBLISHED
    agent.published_at = time.time()
    agent.updated_at = agent.published_at
    agent.published_version = agent.version
    agent.canary_percentage = canary_percentage
    agent.canary_whitelist = canary_whitelist or []

    _register_to_runtime(agent)

    logger.info(
        "自定义 Agent 灰度发布: id=%s name=%s canary=%d%% by=%s",
        agent_id, agent.name, canary_percentage, published_by,
    )
    return agent


def update_canary_percentage(agent_id: str, percentage: int) -> CustomAgentConfig | None:
    """更新灰度百分比

    Args:
        agent_id: Agent ID
        percentage: 新的灰度百分比

    Returns:
        更新后的 Agent 配置
    """
    agent = _agents_store.get(agent_id)
    if not agent:
        return None

    if agent.status != AgentStatus.PUBLISHED:
        raise ValueError("只有已发布的 Agent 可以调整灰度比例")

    old_pct = agent.canary_percentage
    agent.canary_percentage = percentage
    agent.updated_at = time.time()

    logger.info("灰度百分比更新: id=%s %d%% -> %d%%", agent_id, old_pct, percentage)
    return agent


def is_canary_user(agent_id: str, user_id: str) -> bool:
    """判断用户是否命中灰度

    灰度规则：
      1. 白名单用户始终命中
      2. 按用户ID哈希值与灰度百分比比较

    Args:
        agent_id: Agent ID
        user_id: 用户ID

    Returns:
        是否命中灰度
    """
    agent = _agents_store.get(agent_id)
    if not agent or agent.status != AgentStatus.PUBLISHED:
        return False

    if agent.canary_percentage == 0:
        return True

    if user_id in agent.canary_whitelist:
        return True

    hash_val = int(hashlib.md5(f"{agent_id}:{user_id}".encode()).hexdigest(), 16)
    return (hash_val % 100) < agent.canary_percentage


def compare_versions(agent_id: str, version_a: int, version_b: int) -> dict[str, Any]:
    """对比两个版本的差异

    Args:
        agent_id: Agent ID
        version_a: 版本A
        version_b: 版本B

    Returns:
        差异对比结果
    """
    record_a = get_agent_version(agent_id, version_a)
    record_b = get_agent_version(agent_id, version_b)

    if not record_a or not record_b:
        return {"error": "版本不存在", "version_a_found": record_a is not None, "version_b_found": record_b is not None}

    diff_fields = [
        "system_prompt", "mcp_servers", "model_tier", "temperature",
        "max_rounds", "review_required", "allowed_roles", "description",
        "display_name",
    ]

    diffs: list[dict[str, str]] = []
    for field_name in diff_fields:
        val_a = getattr(record_a.config, field_name, None)
        val_b = getattr(record_b.config, field_name, None)
        if val_a != val_b:
            diffs.append({
                "field": field_name,
                "version_a": str(val_a),
                "version_b": str(val_b),
            })

    return {
        "agent_id": agent_id,
        "version_a": version_a,
        "version_b": version_b,
        "diffs": diffs,
        "is_identical": len(diffs) == 0,
    }


# ==================== 技能组合创建 Agent ====================


async def create_agent_from_skills(
    agent_name: str,
    skill_ids: list[str],
    base_prompt: str = "",
    model_tier: ModelTier = ModelTier.PLUS,
    temperature: float = 0.7,
) -> Any:
    """根据技能列表动态创建 Agent

    将多个技能组合为一个 Agent，自动合并提示词和工具。
    这是 Skills 轻量化的核心方法，避免为每个技能创建独立 Agent。

    实现逻辑：
    1. 从 BUILTIN_SKILLS 获取每个技能的配置
    2. 合并技能的 prompt_extension 为系统提示词
    3. 合并技能的 required_tools 为工具列表
    4. 创建 AssistantAgent 实例

    Args:
        agent_name: Agent 名称
        skill_ids: 技能ID列表
        base_prompt: 基础提示词（可选，会与技能提示词合并）
        model_tier: 模型级别
        temperature: 推理温度

    Returns:
        AssistantAgent 实例

    Raises:
        ValueError: 技能ID不存在
    """
    from autogen_agentchat.agents import AssistantAgent
    from agent.core.model_client import get_model_client
    from agent.core.mcp_integration import load_mcp_tools
    from agent.agents.domain import BUILTIN_SKILLS

    # 收集技能配置
    skills = []
    missing_skills = []
    for sid in skill_ids:
        skill = BUILTIN_SKILLS.get(sid)
        if skill:
            skills.append(skill)
        else:
            missing_skills.append(sid)

    if missing_skills:
        raise ValueError(f"技能不存在: {', '.join(missing_skills)}")

    if not skills:
        raise ValueError("至少需要指定一个技能")

    # 按优先级排序
    skills.sort(key=lambda s: s.priority, reverse=True)

    # 合并系统提示词
    prompt_parts: list[str] = []
    if base_prompt:
        prompt_parts.append(base_prompt)
    else:
        prompt_parts.append(f"你是 {agent_name}，一个多功能智能助手。")

    prompt_parts.append("\n你具备以下技能：")
    for skill in skills:
        prompt_parts.append(f"- {skill.name}：{skill.description}")
        if skill.prompt_extension:
            prompt_parts.append(f"  {skill.prompt_extension}")

    prompt_parts.append("\n请根据用户需求，选择合适的技能来处理。完成当前任务后，请输出: TASK_COMPLETE")

    system_prompt = "\n".join(prompt_parts)

    # 合并工具列表（去重）
    all_mcp_servers: list[str] = []
    for skill in skills:
        for tool_name in skill.required_tools:
            resource_prefix = tool_name.split(":")[0] if ":" in tool_name else tool_name
            if resource_prefix not in all_mcp_servers:
                all_mcp_servers.append(resource_prefix)

    # 加载工具
    tools = await load_mcp_tools(all_mcp_servers)

    # 创建 Agent
    agent = AssistantAgent(
        name=agent_name,
        model_client=get_model_client(model_tier.value),
        tools=tools,
        system_message=system_prompt,
    )

    # 注册到运行时
    from agent.agents.domain import AGENT_PROMPTS, AGENT_CREATORS, AGENT_SKILL_BINDINGS

    AGENT_PROMPTS[agent_name] = system_prompt
    AGENT_SKILL_BINDINGS[agent_name] = skill_ids

    async def _create() -> Any:
        return AssistantAgent(
            name=agent_name,
            model_client=get_model_client(model_tier.value),
            tools=await load_mcp_tools(all_mcp_servers),
            system_message=system_prompt,
        )

    AGENT_CREATORS[agent_name] = _create

    logger.info("技能组合 Agent 已创建: name=%s skills=%s", agent_name, skill_ids)
    return agent
