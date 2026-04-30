"""租户管理路由

提供多租户 CRUD、配额查询、套餐管理等 API。
仅限管理员访问。
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from security.auth import require_roles
from security.tenant import (
    Tenant,
    TenantStatus,
    TenantPlan,
    IsolationLevel,
    get_tenant_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants", tags=["租户管理"])


class CreateTenantRequest(BaseModel):
    """创建租户请求"""

    name: str = Field(..., min_length=2, max_length=64, description="租户名称（唯一标识）")
    display_name: str = Field(default="", max_length=128, description="显示名称")
    plan: TenantPlan = Field(default=TenantPlan.FREE, description="套餐")
    isolation_level: IsolationLevel = Field(default=IsolationLevel.ROW, description="隔离级别")
    default_region: str = Field(default="cn-north", description="默认数据区域")
    sso_provider: str = Field(default="", description="SSO 提供者")
    sso_config: dict = Field(default_factory=dict, description="SSO 配置")


class UpdateTenantRequest(BaseModel):
    """更新租户请求"""

    display_name: str | None = None
    plan: TenantPlan | None = None
    default_region: str | None = None
    sso_provider: str | None = None
    sso_config: dict | None = None
    admin_user_ids: list[str] | None = None
    custom_settings: dict | None = None


class TenantResponse(BaseModel):
    """租户响应"""

    tenant_id: str
    name: str
    display_name: str
    status: str
    plan: str
    isolation_level: str
    default_region: str
    sso_provider: str
    created_at: str
    updated_at: str


class TenantListResponse(BaseModel):
    """租户列表响应"""

    tenants: list[TenantResponse]
    total: int


class QuotaResponse(BaseModel):
    """配额响应"""

    max_users: int
    max_sessions_per_day: int
    max_agents: int
    max_storage_gb: int
    max_api_calls_per_day: int
    max_concurrent_sessions: int
    sso_enabled: bool
    custom_agents_enabled: bool
    data_residency_control: bool
    audit_log_retention_days: int
    encryption_at_rest: bool


class QuotaCheckRequest(BaseModel):
    """配额检查请求"""

    resource: str = Field(..., description="资源类型")
    current_usage: int = Field(..., ge=0, description="当前使用量")


class QuotaCheckResponse(BaseModel):
    """配额检查响应"""

    allowed: bool
    reason: str = ""
    remaining: int | None = None
    limit: int | None = None


class UpgradePlanRequest(BaseModel):
    """套餐升级请求"""

    plan: TenantPlan = Field(..., description="目标套餐")


def _tenant_to_response(tenant: Tenant) -> TenantResponse:
    """将 Tenant 实体转换为响应"""
    return TenantResponse(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        display_name=tenant.display_name,
        status=tenant.status.value,
        plan=tenant.plan.value,
        isolation_level=tenant.isolation_level.value,
        default_region=tenant.default_region,
        sso_provider=tenant.sso_provider,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@router.post("", response_model=TenantResponse, status_code=201, summary="创建租户")
async def create_tenant(request: Request, body: CreateTenantRequest) -> TenantResponse:
    """创建租户

    仅限管理员操作。创建新租户并分配默认配额。
    """
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    try:
        tenant = manager.create_tenant(
            name=body.name,
            plan=body.plan,
            isolation_level=body.isolation_level,
            display_name=body.display_name,
            default_region=body.default_region,
            sso_provider=body.sso_provider,
            sso_config=body.sso_config,
        )
    except ValueError as e:
        raise AppException(ErrorCode.CONFLICT, message=str(e))

    return _tenant_to_response(tenant)


@router.get("", response_model=TenantListResponse, summary="列出租户")
async def list_tenants(
    request: Request,
    status: str | None = None,
    plan: str | None = None,
) -> TenantListResponse:
    """列出租户

    仅限管理员操作。支持按状态和套餐过滤。
    """
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    filter_status = TenantStatus(status) if status else None
    filter_plan = TenantPlan(plan) if plan else None

    tenants = manager.list_tenants(status=filter_status, plan=filter_plan)
    return TenantListResponse(
        tenants=[_tenant_to_response(t) for t in tenants],
        total=len(tenants),
    )


@router.get("/{tenant_id}", response_model=TenantResponse, summary="获取租户详情")
async def get_tenant(request: Request, tenant_id: str) -> TenantResponse:
    """获取租户详情"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    tenant = manager.get_tenant(tenant_id)
    if tenant is None:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    return _tenant_to_response(tenant)


@router.patch("/{tenant_id}", response_model=TenantResponse, summary="更新租户")
async def update_tenant(request: Request, tenant_id: str, body: UpdateTenantRequest) -> TenantResponse:
    """更新租户信息"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    updates = body.model_dump(exclude_none=True)
    tenant = manager.update_tenant(tenant_id, **updates)
    if tenant is None:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    return _tenant_to_response(tenant)


@router.post("/{tenant_id}/suspend", response_model=TenantResponse, summary="暂停租户")
async def suspend_tenant(request: Request, tenant_id: str) -> TenantResponse:
    """暂停租户"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    success = manager.suspend_tenant(tenant_id)
    if not success:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    tenant = manager.get_tenant(tenant_id)
    return _tenant_to_response(tenant)


@router.post("/{tenant_id}/activate", response_model=TenantResponse, summary="激活租户")
async def activate_tenant(request: Request, tenant_id: str) -> TenantResponse:
    """激活租户"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    success = manager.activate_tenant(tenant_id)
    if not success:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    tenant = manager.get_tenant(tenant_id)
    return _tenant_to_response(tenant)


@router.delete("/{tenant_id}", summary="删除租户")
async def delete_tenant(request: Request, tenant_id: str) -> dict:
    """删除租户（软删除）"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    success = manager.delete_tenant(tenant_id)
    if not success:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    return {"message": "租户已删除", "tenant_id": tenant_id}


@router.get("/{tenant_id}/quota", response_model=QuotaResponse, summary="查询租户配额")
async def get_tenant_quota(request: Request, tenant_id: str) -> QuotaResponse:
    """获取租户配额"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    tenant = manager.get_tenant(tenant_id)
    if tenant is None:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    return QuotaResponse(**tenant.quota.model_dump())


@router.post("/{tenant_id}/quota/check", response_model=QuotaCheckResponse, summary="检查租户配额")
async def check_tenant_quota(
    request: Request,
    tenant_id: str,
    body: QuotaCheckRequest,
) -> QuotaCheckResponse:
    """检查租户配额"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    result = manager.check_quota(tenant_id, body.resource, body.current_usage)

    return QuotaCheckResponse(
        allowed=result.get("allowed", False),
        reason=result.get("reason", ""),
        remaining=result.get("remaining"),
        limit=result.get("limit"),
    )


@router.post("/{tenant_id}/upgrade", response_model=TenantResponse, summary="升级租户套餐")
async def upgrade_tenant_plan(
    request: Request,
    tenant_id: str,
    body: UpgradePlanRequest,
) -> TenantResponse:
    """升级租户套餐"""
    require_roles(request, ["admin"])

    manager = get_tenant_manager()
    tenant = manager.upgrade_plan(tenant_id, body.plan)
    if tenant is None:
        raise AppException(ErrorCode.NOT_FOUND, message=f"租户不存在: {tenant_id}")

    return _tenant_to_response(tenant)
