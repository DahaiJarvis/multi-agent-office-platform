"""数据驻留控制

满足 GDPR、中国数据安全法等法规对数据主权的要求。
确保数据在指定区域内存储和处理，防止数据跨境传输。

架构设计：
  - DataRegion: 数据区域定义（含地理坐标、法规约束）
  - DataResidencyPolicy: 数据驻留策略（定义哪些数据必须在哪些区域）
  - DataResidencyManager: 驻留管理器（策略执行、路由决策、合规验证）

使用方式：
  from security.data_residency import get_data_residency_manager

  mgr = get_data_residency_manager()
  region = mgr.resolve_region(tenant_id="tenant_001", data_category="user_data")
  is_compliant = mgr.validate_data_placement(tenant_id="tenant_001", data_category="user_data", region="eu-west")
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DataRegion(str, Enum):
    """数据区域

    每个区域对应一个地理部署位置和法规管辖区。
    """

    CN_NORTH = "cn-north"
    CN_EAST = "cn-east"
    CN_SOUTH = "cn-south"
    EU_WEST = "eu-west"
    EU_CENTRAL = "eu-central"
    US_EAST = "us-east"
    US_WEST = "us-west"
    AP_SOUTHEAST = "ap-southeast"


class RegulationFramework(str, Enum):
    """法规框架"""

    GDPR = "gdpr"
    CHINA_DSL = "china_dsl"
    CHINA_PIPL = "china_pipl"
    CCPA = "ccpa"
    LGPD = "lgpd"


class RegionInfo(BaseModel):
    """区域信息"""

    region: DataRegion
    display_name: str
    country: str
    regulations: list[RegulationFramework] = Field(default_factory=list)
    postgres_host: str = ""
    redis_host: str = ""
    endpoint_url: str = ""
    available: bool = True


REGION_REGISTRY: dict[DataRegion, RegionInfo] = {
    DataRegion.CN_NORTH: RegionInfo(
        region=DataRegion.CN_NORTH,
        display_name="中国-华北",
        country="CN",
        regulations=[RegulationFramework.CHINA_DSL, RegulationFramework.CHINA_PIPL],
        postgres_host="pg-cn-north.internal",
        redis_host="redis-cn-north.internal",
        endpoint_url="https://api-cn-north.example.com",
    ),
    DataRegion.CN_EAST: RegionInfo(
        region=DataRegion.CN_EAST,
        display_name="中国-华东",
        country="CN",
        regulations=[RegulationFramework.CHINA_DSL, RegulationFramework.CHINA_PIPL],
        postgres_host="pg-cn-east.internal",
        redis_host="redis-cn-east.internal",
        endpoint_url="https://api-cn-east.example.com",
    ),
    DataRegion.CN_SOUTH: RegionInfo(
        region=DataRegion.CN_SOUTH,
        display_name="中国-华南",
        country="CN",
        regulations=[RegulationFramework.CHINA_DSL, RegulationFramework.CHINA_PIPL],
        postgres_host="pg-cn-south.internal",
        redis_host="redis-cn-south.internal",
        endpoint_url="https://api-cn-south.example.com",
    ),
    DataRegion.EU_WEST: RegionInfo(
        region=DataRegion.EU_WEST,
        display_name="欧洲-西欧",
        country="EU",
        regulations=[RegulationFramework.GDPR],
        postgres_host="pg-eu-west.internal",
        redis_host="redis-eu-west.internal",
        endpoint_url="https://api-eu-west.example.com",
    ),
    DataRegion.EU_CENTRAL: RegionInfo(
        region=DataRegion.EU_CENTRAL,
        display_name="欧洲-中欧",
        country="EU",
        regulations=[RegulationFramework.GDPR],
        postgres_host="pg-eu-central.internal",
        redis_host="redis-eu-central.internal",
        endpoint_url="https://api-eu-central.example.com",
    ),
    DataRegion.US_EAST: RegionInfo(
        region=DataRegion.US_EAST,
        display_name="美国-东部",
        country="US",
        regulations=[RegulationFramework.CCPA],
        postgres_host="pg-us-east.internal",
        redis_host="redis-us-east.internal",
        endpoint_url="https://api-us-east.example.com",
    ),
    DataRegion.US_WEST: RegionInfo(
        region=DataRegion.US_WEST,
        display_name="美国-西部",
        country="US",
        regulations=[RegulationFramework.CCPA],
        postgres_host="pg-us-west.internal",
        redis_host="redis-us-west.internal",
        endpoint_url="https://api-us-west.example.com",
    ),
    DataRegion.AP_SOUTHEAST: RegionInfo(
        region=DataRegion.AP_SOUTHEAST,
        display_name="亚太-东南亚",
        country="AP",
        regulations=[RegulationFramework.LGPD],
        postgres_host="pg-ap-southeast.internal",
        redis_host="redis-ap-southeast.internal",
        endpoint_url="https://api-ap-southeast.example.com",
    ),
}


class DataCategory(str, Enum):
    """数据分类（驻留控制视角）"""

    USER_PROFILE = "user_profile"
    CHAT_CONTENT = "chat_content"
    AUDIT_LOG = "audit_log"
    SESSION_DATA = "session_data"
    DOCUMENT = "document"
    KNOWLEDGE_BASE = "knowledge_base"
    SYSTEM_CONFIG = "system_config"
    METRIC_DATA = "metric_data"


class ResidencyRule(BaseModel):
    """驻留规则

    定义特定数据分类在特定租户下的驻留要求。
    """

    data_category: DataCategory
    allowed_regions: list[DataRegion] = Field(description="允许存储的区域列表")
    primary_region: DataRegion = Field(description="主存储区域")
    cross_region_replication: bool = Field(default=False, description="是否允许跨区域复制")
    cross_border_restricted: bool = Field(default=True, description="是否限制跨境传输")


# 默认驻留规则：中国数据必须留在中国区域
DEFAULT_RESIDENCY_RULES: dict[DataCategory, ResidencyRule] = {
    DataCategory.USER_PROFILE: ResidencyRule(
        data_category=DataCategory.USER_PROFILE,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=False,
        cross_border_restricted=True,
    ),
    DataCategory.CHAT_CONTENT: ResidencyRule(
        data_category=DataCategory.CHAT_CONTENT,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=False,
        cross_border_restricted=True,
    ),
    DataCategory.AUDIT_LOG: ResidencyRule(
        data_category=DataCategory.AUDIT_LOG,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=True,
        cross_border_restricted=True,
    ),
    DataCategory.SESSION_DATA: ResidencyRule(
        data_category=DataCategory.SESSION_DATA,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=False,
        cross_border_restricted=True,
    ),
    DataCategory.DOCUMENT: ResidencyRule(
        data_category=DataCategory.DOCUMENT,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=False,
        cross_border_restricted=True,
    ),
    DataCategory.KNOWLEDGE_BASE: ResidencyRule(
        data_category=DataCategory.KNOWLEDGE_BASE,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=True,
        cross_border_restricted=True,
    ),
    DataCategory.SYSTEM_CONFIG: ResidencyRule(
        data_category=DataCategory.SYSTEM_CONFIG,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=True,
        cross_border_restricted=False,
    ),
    DataCategory.METRIC_DATA: ResidencyRule(
        data_category=DataCategory.METRIC_DATA,
        allowed_regions=[DataRegion.CN_NORTH, DataRegion.CN_EAST, DataRegion.CN_SOUTH],
        primary_region=DataRegion.CN_NORTH,
        cross_region_replication=True,
        cross_border_restricted=False,
    ),
}


class TenantResidencyConfig(BaseModel):
    """租户级驻留配置

    每个租户可以覆盖默认的驻留规则。
    """

    tenant_id: str
    default_region: DataRegion = DataRegion.CN_NORTH
    allowed_regions: list[DataRegion] = Field(default_factory=lambda: [DataRegion.CN_NORTH])
    custom_rules: dict[str, ResidencyRule] = Field(default_factory=dict)
    data_sovereignty_declaration: str = Field(default="", description="数据主权声明")
    cross_border_transfer_approved: bool = Field(default=False, description="是否批准跨境传输")


class ResidencyValidationResult(BaseModel):
    """驻留验证结果"""

    compliant: bool
    data_category: str
    requested_region: str
    allowed_regions: list[str]
    primary_region: str
    violation: str = ""
    recommendation: str = ""


class DataResidencyManager:
    """数据驻留管理器

    核心职责：
    1. 根据租户和数据分类确定数据存储区域
    2. 验证数据放置是否合规
    3. 提供区域路由决策
    4. 管理租户级驻留配置
    """

    def __init__(self):
        self._default_rules: dict[DataCategory, ResidencyRule] = dict(DEFAULT_RESIDENCY_RULES)
        self._tenant_configs: dict[str, TenantResidencyConfig] = {}
        self._current_region: DataRegion = DataRegion.CN_NORTH

    def set_current_region(self, region: DataRegion) -> None:
        """设置当前部署区域

        Args:
            region: 当前区域
        """
        self._current_region = region
        logger.info("数据驻留当前区域设置为: %s", region.value)

    def get_current_region(self) -> DataRegion:
        """获取当前部署区域"""
        return self._current_region

    def register_tenant_config(self, config: TenantResidencyConfig) -> None:
        """注册租户驻留配置

        Args:
            config: 租户驻留配置
        """
        self._tenant_configs[config.tenant_id] = config
        logger.info("租户驻留配置已注册: tenant=%s region=%s", config.tenant_id, config.default_region.value)

    def get_tenant_config(self, tenant_id: str) -> TenantResidencyConfig | None:
        """获取租户驻留配置"""
        return self._tenant_configs.get(tenant_id)

    def resolve_region(self, tenant_id: str, data_category: str) -> DataRegion:
        """解析数据应存储的区域

        优先级：
        1. 租户自定义规则
        2. 默认驻留规则
        3. 当前部署区域

        Args:
            tenant_id: 租户ID
            data_category: 数据分类

        Returns:
            推荐的存储区域
        """
        category = DataCategory(data_category) if data_category in [e.value for e in DataCategory] else None

        tenant_config = self._tenant_configs.get(tenant_id)
        if tenant_config:
            if category and category.value in tenant_config.custom_rules:
                return tenant_config.custom_rules[category.value].primary_region
            return tenant_config.default_region

        if category and category in self._default_rules:
            return self._default_rules[category].primary_region

        return self._current_region

    def validate_data_placement(
        self,
        tenant_id: str,
        data_category: str,
        region: str,
    ) -> ResidencyValidationResult:
        """验证数据放置是否合规

        Args:
            tenant_id: 租户ID
            data_category: 数据分类
            region: 目标存储区域

        Returns:
            验证结果
        """
        category = DataCategory(data_category) if data_category in [e.value for e in DataCategory] else None
        target_region = DataRegion(region) if region in [e.value for e in DataRegion] else None

        if category is None or target_region is None:
            return ResidencyValidationResult(
                compliant=False,
                data_category=data_category,
                requested_region=region,
                allowed_regions=[],
                primary_region="",
                violation="未知的数据分类或区域",
            )

        tenant_config = self._tenant_configs.get(tenant_id)

        if tenant_config:
            if category.value in tenant_config.custom_rules:
                rule = tenant_config.custom_rules[category.value]
            else:
                default_rule = self._default_rules.get(category)
                if default_rule is None:
                    return ResidencyValidationResult(
                        compliant=True,
                        data_category=data_category,
                        requested_region=region,
                        allowed_regions=[r.value for r in tenant_config.allowed_regions],
                        primary_region=tenant_config.default_region.value,
                    )
                rule = default_rule

            allowed = [r.value for r in tenant_config.allowed_regions]
            primary = tenant_config.default_region.value
        else:
            rule = self._default_rules.get(category)
            if rule is None:
                return ResidencyValidationResult(
                    compliant=True,
                    data_category=data_category,
                    requested_region=region,
                    allowed_regions=[self._current_region.value],
                    primary_region=self._current_region.value,
                )
            allowed = [r.value for r in rule.allowed_regions]
            primary = rule.primary_region.value

        is_compliant = region in allowed

        violation = ""
        recommendation = ""
        if not is_compliant:
            current_country = REGION_REGISTRY.get(self._current_region, REGION_REGISTRY[DataRegion.CN_NORTH]).country
            target_country = REGION_REGISTRY.get(target_region, REGION_REGISTRY[DataRegion.CN_NORTH]).country
            if current_country != target_country:
                violation = f"跨境传输受限: {current_country} -> {target_country}，违反数据主权要求"
                recommendation = f"请将数据存储在以下合规区域: {', '.join(allowed)}"
            else:
                violation = f"区域 {region} 不在允许列表中"
                recommendation = f"请使用以下区域之一: {', '.join(allowed)}"

        return ResidencyValidationResult(
            compliant=is_compliant,
            data_category=data_category,
            requested_region=region,
            allowed_regions=allowed,
            primary_region=primary,
            violation=violation,
            recommendation=recommendation,
        )

    def get_region_info(self, region: DataRegion) -> RegionInfo | None:
        """获取区域信息"""
        return REGION_REGISTRY.get(region)

    def get_all_regions(self) -> list[RegionInfo]:
        """获取所有可用区域"""
        return [r for r in REGION_REGISTRY.values() if r.available]

    def get_data_routing_config(self, tenant_id: str, data_category: str) -> dict[str, Any]:
        """获取数据路由配置

        用于数据库连接和存储路由决策。

        Args:
            tenant_id: 租户ID
            data_category: 数据分类

        Returns:
            路由配置字典
        """
        region = self.resolve_region(tenant_id, data_category)
        region_info = REGION_REGISTRY.get(region)

        if region_info is None:
            region_info = REGION_REGISTRY[DataRegion.CN_NORTH]

        return {
            "region": region.value,
            "postgres_host": region_info.postgres_host,
            "redis_host": region_info.redis_host,
            "endpoint_url": region_info.endpoint_url,
            "regulations": [r.value for r in region_info.regulations],
        }


_residency_manager: DataResidencyManager | None = None


def get_data_residency_manager() -> DataResidencyManager:
    """获取全局数据驻留管理器实例"""
    global _residency_manager
    if _residency_manager is None:
        _residency_manager = DataResidencyManager()
    return _residency_manager
