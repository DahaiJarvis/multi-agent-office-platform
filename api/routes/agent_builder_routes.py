"""Agent Builder 路由

提供自定义 Agent 的创建、更新、发布、版本管理和模板市场 API。
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from security.auth import require_roles
from agent.agents.agent_builder import (
    AgentStatus,
    ModelTier,
    CustomAgentConfig,
    create_custom_agent,
    get_custom_agent,
    list_custom_agents,
    update_custom_agent,
    publish_custom_agent,
    disable_custom_agent,
    archive_custom_agent,
    delete_custom_agent,
    get_agent_versions,
    get_agent_version,
    rollback_agent_version,
    list_templates,
    get_template,
    create_from_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-builder", tags=["Agent Builder"])


class CreateAgentRequest(BaseModel):
    """创建自定义 Agent 请求"""

    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=512)
    system_prompt: str = Field(min_length=10, max_length=8192)
    mcp_servers: list[str] = Field(default_factory=list)
    model_tier: ModelTier = Field(default=ModelTier.PLUS)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_rounds: int = Field(default=10, ge=1, le=50)
    review_required: bool = Field(default=False)
    allowed_roles: list[str] = Field(default_factory=lambda: ["employee"])
    tags: list[str] = Field(default_factory=list)
    icon: str = Field(default="")


class UpdateAgentRequest(BaseModel):
    """更新自定义 Agent 请求"""

    display_name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    mcp_servers: list[str] | None = None
    model_tier: ModelTier | None = None
    temperature: float | None = None
    max_rounds: int | None = None
    review_required: bool | None = None
    allowed_roles: list[str] | None = None
    tags: list[str] | None = None
    icon: str | None = None


class CreateFromTemplateRequest(BaseModel):
    """从模板创建 Agent 请求"""

    template_id: str
    name: str = Field(min_length=1, max_length=64)
    overrides: dict | None = None


class AgentResponse(BaseModel):
    """Agent 配置响应"""

    agent_id: str
    name: str
    display_name: str
    description: str
    version: int
    status: str
    system_prompt: str
    mcp_servers: list[str]
    model_tier: str
    temperature: float
    max_rounds: int
    review_required: bool
    allowed_roles: list[str]
    created_by: str
    created_at: float
    updated_at: float
    published_at: float | None
    tags: list[str]
    icon: str


class VersionResponse(BaseModel):
    """版本记录响应"""

    agent_id: str
    version: int
    diff_from_previous: list[dict]
    created_at: float
    created_by: str


class TemplateResponse(BaseModel):
    """模板响应"""

    template_id: str
    name: str
    description: str
    category: str
    is_official: bool
    usage_count: int
    config: AgentResponse


def _to_response(config: CustomAgentConfig) -> AgentResponse:
    return AgentResponse(
        agent_id=config.agent_id,
        name=config.name,
        display_name=config.display_name,
        description=config.description,
        version=config.version,
        status=config.status.value,
        system_prompt=config.system_prompt,
        mcp_servers=config.mcp_servers,
        model_tier=config.model_tier.value,
        temperature=config.temperature,
        max_rounds=config.max_rounds,
        review_required=config.review_required,
        allowed_roles=config.allowed_roles,
        created_by=config.created_by,
        created_at=config.created_at,
        updated_at=config.updated_at,
        published_at=config.published_at,
        tags=config.tags,
        icon=config.icon,
    )


@router.post("/agents", response_model=AgentResponse)
async def api_create_agent(request: Request, body: CreateAgentRequest) -> AgentResponse:
    """创建自定义 Agent"""
    require_roles(request, ["admin", "hr_specialist"])

    auth_payload = getattr(request.state, "auth_payload", None)
    created_by = auth_payload.user_id if auth_payload else "unknown"

    config = CustomAgentConfig(
        name=body.name,
        display_name=body.display_name or body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        mcp_servers=body.mcp_servers,
        model_tier=body.model_tier,
        temperature=body.temperature,
        max_rounds=body.max_rounds,
        review_required=body.review_required,
        allowed_roles=body.allowed_roles,
        tags=body.tags,
        icon=body.icon,
    )

    result = create_custom_agent(config, created_by)
    return _to_response(result)


@router.get("/agents", response_model=list[AgentResponse])
async def api_list_agents(
    request: Request,
    created_by: str = "",
    status: str = "",
) -> list[AgentResponse]:
    """列出自定义 Agent"""
    require_roles(request, ["admin", "hr_specialist"])

    agent_status = AgentStatus(status) if status else None
    agents = list_custom_agents(created_by=created_by, status=agent_status)
    return [_to_response(a) for a in agents]


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def api_get_agent(request: Request, agent_id: str) -> AgentResponse:
    """获取自定义 Agent 详情"""
    require_roles(request, ["admin", "hr_specialist"])

    agent = get_custom_agent(agent_id)
    if not agent:
        raise AppException(ErrorCode.NOT_FOUND, message=f"Agent 不存在: {agent_id}")
    return _to_response(agent)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def api_update_agent(request: Request, agent_id: str, body: UpdateAgentRequest) -> AgentResponse:
    """更新自定义 Agent 配置"""
    require_roles(request, ["admin", "hr_specialist"])

    auth_payload = getattr(request.state, "auth_payload", None)
    updated_by = auth_payload.user_id if auth_payload else "unknown"

    updates = body.model_dump(exclude_none=True)
    result = update_custom_agent(agent_id, updates, updated_by)
    if not result:
        raise AppException(ErrorCode.NOT_FOUND, message=f"Agent 不存在: {agent_id}")
    return _to_response(result)


@router.post("/agents/{agent_id}/publish", response_model=AgentResponse)
async def api_publish_agent(request: Request, agent_id: str) -> AgentResponse:
    """发布自定义 Agent"""
    require_roles(request, ["admin"])

    auth_payload = getattr(request.state, "auth_payload", None)
    published_by = auth_payload.user_id if auth_payload else "unknown"

    result = publish_custom_agent(agent_id, published_by)
    if not result:
        raise AppException(ErrorCode.NOT_FOUND, message=f"Agent 不存在: {agent_id}")
    return _to_response(result)


@router.post("/agents/{agent_id}/disable", response_model=AgentResponse)
async def api_disable_agent(request: Request, agent_id: str) -> AgentResponse:
    """禁用自定义 Agent"""
    require_roles(request, ["admin"])

    result = disable_custom_agent(agent_id)
    if not result:
        raise AppException(ErrorCode.NOT_FOUND, message=f"Agent 不存在: {agent_id}")
    return _to_response(result)


@router.delete("/agents/{agent_id}")
async def api_delete_agent(request: Request, agent_id: str) -> dict:
    """删除自定义 Agent"""
    require_roles(request, ["admin"])

    success = delete_custom_agent(agent_id)
    if not success:
        raise AppException(ErrorCode.NOT_FOUND, message=f"Agent 不存在: {agent_id}")
    return {"message": "Agent 已删除"}


@router.get("/agents/{agent_id}/versions", response_model=list[VersionResponse])
async def api_list_versions(request: Request, agent_id: str) -> list[VersionResponse]:
    """获取 Agent 版本历史"""
    require_roles(request, ["admin", "hr_specialist"])

    versions = get_agent_versions(agent_id)
    return [
        VersionResponse(
            agent_id=v.agent_id,
            version=v.version,
            diff_from_previous=[d.model_dump() for d in v.diff_from_previous],
            created_at=v.created_at,
            created_by=v.created_by,
        )
        for v in versions
    ]


@router.post("/agents/{agent_id}/rollback", response_model=AgentResponse)
async def api_rollback_agent(request: Request, agent_id: str, version: int) -> AgentResponse:
    """回滚 Agent 到指定版本"""
    require_roles(request, ["admin"])

    auth_payload = getattr(request.state, "auth_payload", None)
    rolled_back_by = auth_payload.user_id if auth_payload else "unknown"

    result = rollback_agent_version(agent_id, version, rolled_back_by)
    if not result:
        raise AppException(ErrorCode.NOT_FOUND, message=f"Agent 或版本不存在: {agent_id} v{version}")
    return _to_response(result)


@router.get("/templates", response_model=list[TemplateResponse])
async def api_list_templates(request: Request, category: str = "") -> list[TemplateResponse]:
    """列出 Agent 模板"""
    templates = list_templates(category=category)
    return [
        TemplateResponse(
            template_id=t.template_id,
            name=t.name,
            description=t.description,
            category=t.category,
            is_official=t.is_official,
            usage_count=t.usage_count,
            config=_to_response(t.config),
        )
        for t in templates
    ]


@router.post("/templates/{template_id}/instantiate", response_model=AgentResponse)
async def api_create_from_template(request: Request, template_id: str, body: CreateFromTemplateRequest) -> AgentResponse:
    """从模板创建自定义 Agent"""
    require_roles(request, ["admin", "hr_specialist"])

    auth_payload = getattr(request.state, "auth_payload", None)
    created_by = auth_payload.user_id if auth_payload else "unknown"

    result = create_from_template(template_id, body.name, created_by, body.overrides)
    if not result:
        raise AppException(ErrorCode.NOT_FOUND, message=f"模板不存在: {template_id}")
    return _to_response(result)
