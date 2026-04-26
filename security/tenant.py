"""多租户架构

实现 SaaS 化多租户架构，支持租户隔离、上下文传播和租户管理。
满足企业级部署的基础架构要求。

架构设计：
  - Tenant: 租户实体
  - TenantContext: 租户上下文（请求级传播）
  - TenantIsolation: 租户隔离策略（数据库级/行级/混合）
  - TenantManager: 租户管理器（CRUD + 配置管理）

隔离策略：
  - database: 每个租户独立数据库（最高隔离级别）
  - schema: 共享数据库，独立 Schema（中等隔离）
  - row: 共享数据库，行级隔离（最低成本，通过 tenant_id 列实现）

上下文传播：
  通过 contextvars 在请求生命周期内自动传播租户信息，
  确保所有数据操作自动附加租户过滤，防止数据泄露。
"""

import logging
import uuid
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TenantStatus(str, Enum):
    """租户状态"""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    MIGRATING = "migrating"
    DELETED = "deleted"


class IsolationLevel(str, Enum):
    """隔离级别"""

    DATABASE = "database"
    SCHEMA = "schema"
    ROW = "row"


class TenantPlan(str, Enum):
    """租户套餐"""

    FREE = "free"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantQuota(BaseModel):
    """租户配额"""

    max_users: int = Field(default=10, description="最大用户数")
    max_sessions_per_day: int = Field(default=100, description="每日最大会话数")
    max_agents: int = Field(default=5, description="最大自定义 Agent 数")
    max_storage_gb: int = Field(default=5, description="最大存储空间(GB)")
    max_api_calls_per_day: int = Field(default=1000, description="每日最大 API 调用数")
    max_concurrent_sessions: int = Field(default=5, description="最大并发会话数")
    sso_enabled: bool = Field(default=False, description="是否启用 SSO")
    custom_agents_enabled: bool = Field(default=False, description="是否启用自定义 Agent")
    data_residency_control: bool = Field(default=False, description="是否启用数据驻留控制")
    audit_log_retention_days: int = Field(default=30, description="审计日志保留天数")
    encryption_at_rest: bool = Field(default=False, description="是否启用静态加密")


PLAN_QUOTAS: dict[TenantPlan, TenantQuota] = {
    TenantPlan.FREE: TenantQuota(
        max_users=5,
        max_sessions_per_day=50,
        max_agents=2,
        max_storage_gb=1,
        max_api_calls_per_day=500,
        max_concurrent_sessions=2,
        sso_enabled=False,
        custom_agents_enabled=False,
        data_residency_control=False,
        audit_log_retention_days=7,
        encryption_at_rest=False,
    ),
    TenantPlan.PROFESSIONAL: TenantQuota(
        max_users=50,
        max_sessions_per_day=1000,
        max_agents=20,
        max_storage_gb=50,
        max_api_calls_per_day=10000,
        max_concurrent_sessions=20,
        sso_enabled=True,
        custom_agents_enabled=True,
        data_residency_control=True,
        audit_log_retention_days=90,
        encryption_at_rest=True,
    ),
    TenantPlan.ENTERPRISE: TenantQuota(
        max_users=1000,
        max_sessions_per_day=100000,
        max_agents=100,
        max_storage_gb=500,
        max_api_calls_per_day=1000000,
        max_concurrent_sessions=200,
        sso_enabled=True,
        custom_agents_enabled=True,
        data_residency_control=True,
        audit_log_retention_days=365,
        encryption_at_rest=True,
    ),
}


class Tenant(BaseModel):
    """租户实体"""

    tenant_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(description="租户名称")
    display_name: str = Field(default="", description="显示名称")
    status: TenantStatus = TenantStatus.ACTIVE
    plan: TenantPlan = TenantPlan.FREE
    isolation_level: IsolationLevel = IsolationLevel.ROW
    quota: TenantQuota = Field(default_factory=lambda: PLAN_QUOTAS[TenantPlan.FREE])

    database_name: str = Field(default="", description="独立数据库名（database 隔离模式）")
    schema_name: str = Field(default="", description="独立 Schema 名（schema 隔离模式）")

    default_region: str = Field(default="cn-north", description="默认数据驻留区域")
    allowed_regions: list[str] = Field(default_factory=lambda: ["cn-north"])

    sso_provider: str = Field(default="", description="SSO 提供者")
    sso_config: dict[str, Any] = Field(default_factory=dict, description="SSO 配置")

    admin_user_ids: list[str] = Field(default_factory=list, description="租户管理员用户ID列表")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    custom_settings: dict[str, Any] = Field(default_factory=dict, description="租户自定义设置")


# ==================== 租户上下文传播 ====================

_current_tenant: ContextVar[Tenant | None] = ContextVar("current_tenant", default=None)
_current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)


def set_tenant_context(tenant: Tenant) -> None:
    """设置当前请求的租户上下文

    在认证中间件中调用，将租户信息注入请求上下文。
    后续所有数据操作自动获取租户信息。

    Args:
        tenant: 租户实体
    """
    _current_tenant.set(tenant)
    _current_tenant_id.set(tenant.tenant_id)


def get_current_tenant() -> Tenant | None:
    """获取当前请求的租户实体"""
    return _current_tenant.get()


def get_current_tenant_id() -> str | None:
    """获取当前请求的租户ID

    在数据查询时使用，自动附加租户过滤条件。
    """
    return _current_tenant_id.get()


def clear_tenant_context() -> None:
    """清除租户上下文

    请求结束时调用，防止上下文泄漏。
    """
    _current_tenant.set(None)
    _current_tenant_id.set(None)


class TenantContextMiddleware:
    """租户上下文中间件

    从请求中提取租户信息并设置上下文。
    在 FastAPI 中间件链中注册使用。
    """

    def __init__(self, tenant_manager: "TenantManager"):
        self._manager = tenant_manager

    async def __call__(self, request, call_next):
        tenant_id = self._extract_tenant_id(request)
        if tenant_id:
            tenant = self._manager.get_tenant(tenant_id)
            if tenant and tenant.status == TenantStatus.ACTIVE:
                set_tenant_context(tenant)
            else:
                logger.warning("无效的租户ID或租户未激活: %s", tenant_id)

        try:
            response = await call_next(request)
        finally:
            clear_tenant_context()

        return response

    def _extract_tenant_id(self, request) -> str | None:
        """从请求中提取租户ID

        优先级：
        1. 请求头 X-Tenant-ID
        2. JWT Token 中的 tenant_id 字段
        3. 查询参数 tenant_id
        """
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            return tenant_id

        auth_payload = getattr(request.state, "auth_payload", None)
        if auth_payload and hasattr(auth_payload, "tenant_id"):
            return auth_payload.tenant_id

        tenant_id = request.query_params.get("tenant_id")
        if tenant_id:
            return tenant_id

        return None


# ==================== 租户隔离策略 ====================

class TenantIsolationPolicy:
    """租户隔离策略

    根据隔离级别提供不同的数据隔离方案。
    """

    @staticmethod
    def get_row_filter(tenant_id: str, table_alias: str = "") -> str:
        """获取行级隔离的 SQL 过滤条件

        Args:
            tenant_id: 租户ID
            table_alias: 表别名

        Returns:
            SQL WHERE 条件
        """
        prefix = f"{table_alias}." if table_alias else ""
        return f"{prefix}tenant_id = '{tenant_id}'"

    @staticmethod
    def get_schema_name(tenant: Tenant) -> str:
        """获取 Schema 隔离的 Schema 名称

        Args:
            tenant: 租户实体

        Returns:
            Schema 名称
        """
        if tenant.schema_name:
            return tenant.schema_name
        return f"tenant_{tenant.tenant_id.replace('-', '_')}"

    @staticmethod
    def get_database_name(tenant: Tenant) -> str:
        """获取 Database 隔离的数据库名称

        Args:
            tenant: 租户实体

        Returns:
            数据库名称
        """
        if tenant.database_name:
            return tenant.database_name
        return f"tenant_{tenant.tenant_id.replace('-', '_')}"

    @staticmethod
    def get_redis_key_prefix(tenant_id: str) -> str:
        """获取 Redis Key 前缀

        所有租户共享 Redis 实例时，通过前缀实现隔离。

        Args:
            tenant_id: 租户ID

        Returns:
            Redis Key 前缀
        """
        return f"t:{tenant_id}:"


# ==================== 租户管理器 ====================

class TenantManager:
    """租户管理器

    提供租户 CRUD、配额管理、隔离配置能力。
    """

    def __init__(self):
        self._tenants: dict[str, Tenant] = {}
        self._isolation = TenantIsolationPolicy()

    def create_tenant(
        self,
        name: str,
        plan: TenantPlan = TenantPlan.FREE,
        isolation_level: IsolationLevel = IsolationLevel.ROW,
        display_name: str = "",
        default_region: str = "cn-north",
        sso_provider: str = "",
        sso_config: dict[str, Any] | None = None,
    ) -> Tenant:
        """创建租户

        Args:
            name: 租户名称（唯一标识）
            plan: 套餐
            isolation_level: 隔离级别
            display_name: 显示名称
            default_region: 默认数据区域
            sso_provider: SSO 提供者
            sso_config: SSO 配置

        Returns:
            创建的租户实体
        """
        for existing in self._tenants.values():
            if existing.name == name:
                raise ValueError(f"租户名称已存在: {name}")

        quota = PLAN_QUOTAS[plan].model_copy()

        tenant = Tenant(
            name=name,
            display_name=display_name or name,
            plan=plan,
            isolation_level=isolation_level,
            quota=quota,
            default_region=default_region,
            sso_provider=sso_provider,
            sso_config=sso_config or {},
        )

        if isolation_level == IsolationLevel.DATABASE:
            tenant.database_name = self._isolation.get_database_name(tenant)
        elif isolation_level == IsolationLevel.SCHEMA:
            tenant.schema_name = self._isolation.get_schema_name(tenant)

        self._tenants[tenant.tenant_id] = tenant
        logger.info(
            "租户已创建: id=%s name=%s plan=%s isolation=%s",
            tenant.tenant_id,
            name,
            plan.value,
            isolation_level.value,
        )
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        """获取租户"""
        return self._tenants.get(tenant_id)

    def get_tenant_by_name(self, name: str) -> Tenant | None:
        """按名称获取租户"""
        for tenant in self._tenants.values():
            if tenant.name == name:
                return tenant
        return None

    def list_tenants(
        self,
        status: TenantStatus | None = None,
        plan: TenantPlan | None = None,
    ) -> list[Tenant]:
        """列出租户

        Args:
            status: 按状态过滤
            plan: 按套餐过滤

        Returns:
            租户列表
        """
        tenants = list(self._tenants.values())
        if status:
            tenants = [t for t in tenants if t.status == status]
        if plan:
            tenants = [t for t in tenants if t.plan == plan]
        return tenants

    def update_tenant(self, tenant_id: str, **updates) -> Tenant | None:
        """更新租户信息

        Args:
            tenant_id: 租户ID
            **updates: 更新字段

        Returns:
            更新后的租户实体
        """
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return None

        for key, value in updates.items():
            if hasattr(tenant, key):
                setattr(tenant, key, value)

        tenant.updated_at = datetime.now().isoformat()
        logger.info("租户已更新: id=%s fields=%s", tenant_id, list(updates.keys()))
        return tenant

    def suspend_tenant(self, tenant_id: str, reason: str = "") -> bool:
        """暂停租户

        Args:
            tenant_id: 租户ID
            reason: 暂停原因

        Returns:
            是否成功
        """
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return False

        tenant.status = TenantStatus.SUSPENDED
        tenant.updated_at = datetime.now().isoformat()
        logger.info("租户已暂停: id=%s reason=%s", tenant_id, reason)
        return True

    def activate_tenant(self, tenant_id: str) -> bool:
        """激活租户"""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return False

        tenant.status = TenantStatus.ACTIVE
        tenant.updated_at = datetime.now().isoformat()
        logger.info("租户已激活: id=%s", tenant_id)
        return True

    def delete_tenant(self, tenant_id: str) -> bool:
        """删除租户（软删除）"""
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return False

        tenant.status = TenantStatus.DELETED
        tenant.updated_at = datetime.now().isoformat()
        logger.info("租户已删除: id=%s", tenant_id)
        return True

    def upgrade_plan(self, tenant_id: str, new_plan: TenantPlan) -> Tenant | None:
        """升级租户套餐

        Args:
            tenant_id: 租户ID
            new_plan: 新套餐

        Returns:
            更新后的租户实体
        """
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return None

        old_plan = tenant.plan
        tenant.plan = new_plan
        tenant.quota = PLAN_QUOTAS[new_plan].model_copy()
        tenant.updated_at = datetime.now().isoformat()

        logger.info("租户套餐已升级: id=%s %s -> %s", tenant_id, old_plan.value, new_plan.value)
        return tenant

    def check_quota(self, tenant_id: str, resource: str, current_usage: int) -> dict[str, Any]:
        """检查配额

        Args:
            tenant_id: 租户ID
            resource: 资源类型（如 max_users）
            current_usage: 当前使用量

        Returns:
            配额检查结果
        """
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return {"allowed": False, "reason": "租户不存在"}

        quota_limit = getattr(tenant.quota, resource, None)
        if quota_limit is None:
            return {"allowed": True, "reason": "无配额限制"}

        if current_usage >= quota_limit:
            return {
                "allowed": False,
                "reason": f"已达到配额上限: {resource}={quota_limit}",
                "current": current_usage,
                "limit": quota_limit,
            }

        return {
            "allowed": True,
            "remaining": quota_limit - current_usage,
            "limit": quota_limit,
        }

    def get_isolation_policy(self) -> TenantIsolationPolicy:
        """获取隔离策略"""
        return self._isolation


_tenant_manager: TenantManager | None = None


def get_tenant_manager() -> TenantManager:
    """获取全局租户管理器实例"""
    global _tenant_manager
    if _tenant_manager is None:
        _tenant_manager = TenantManager()
    return _tenant_manager
