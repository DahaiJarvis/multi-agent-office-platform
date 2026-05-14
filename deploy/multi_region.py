"""多区域部署管理

================================================================================
模块职责
================================================================================
提供服务跨国企业和满足数据驻留要求的多区域基础设施管理，包括：
  - 区域注册：管理多个部署区域
  - 流量路由：根据用户位置和策略路由到最近区域
  - 灾备切换：主区域故障时自动切换到备用区域
  - 数据同步：跨区域数据复制状态管理
  - 健康检查：各区域服务健康状态监控

================================================================================
区域角色
================================================================================
PRIMARY（主区域）：
  - 承载主要流量
  - 数据写入入口
  - 优先路由目标

SECONDARY（次区域）：
  - 承载部分流量
  - 数据读取副本
  - 主区域故障时的切换目标

STANDBY（备用区域）：
  - 不承载流量
  - 仅用于灾备
  - 主次区域都故障时启用

================================================================================
区域状态
================================================================================
ACTIVE：正常服务
DEGRADED：降级服务（部分功能不可用）
FAILOVER：灾备切换中
OFFLINE：离线
MAINTENANCE：维护中

================================================================================
路由策略
================================================================================
优先级顺序：
  1. 数据驻留合规（如果指定了数据驻留区域）
  2. 低延迟（根据用户位置选择最近区域）
  3. 健康状态（排除不健康区域）
  4. 灾备切换（主区域不可用时切换）

================================================================================
与其他模块的关系
================================================================================
- ha_manager.py: 区域健康检查结果
- data_residency.py: 数据驻留合规检查
- session_manager.py: 会话数据跨区域同步

================================================================================
使用示例
================================================================================
    # 注册区域
    register_region(DeployRegion(
        region_id="cn-north-1",
        name="华北区域",
        country="CN",
        role=RegionRole.PRIMARY,
        api_endpoint="https://api-cn-north.example.com",
    ))

    # 路由请求
    decision = route_request(
        user_country="CN",
        data_residency_region="cn-north-1",
    )
    print(f"目标区域: {decision.target_region}, 延迟: {decision.estimated_latency_ms}ms")

    # 更新区域状态
    update_region_status("cn-north-1", RegionStatus.OFFLINE)
"""

import logging
import time
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RegionStatus(str, Enum):
    """区域状态枚举

    定义区域的运行状态。

    Attributes:
        ACTIVE: 正常服务，接收流量
        DEGRADED: 降级服务，部分功能不可用
        FAILOVER: 灾备切换中
        OFFLINE: 离线，不接收流量
        MAINTENANCE: 维护中
    """

    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILOVER = "failover"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class RegionRole(str, Enum):
    """区域角色枚举

    定义区域在多区域架构中的角色。

    Attributes:
        PRIMARY: 主区域，承载主要流量
        SECONDARY: 次区域，承载部分流量
        STANDBY: 备用区域，仅用于灾备
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    STANDBY = "standby"


class DeployRegion(BaseModel):
    """部署区域配置

    定义一个部署区域的完整配置信息。

    Attributes:
        region_id: 区域标识，如 cn-north-1
        name: 区域名称
        country: 国家代码
        continent: 大洲
        latitude: 纬度（用于距离计算）
        longitude: 经度（用于距离计算）
        api_endpoint: API 端点
        cdn_endpoint: CDN 端点
        role: 区域角色
        status: 区域状态
        latency_from_cn_ms: 从中国访问延迟(ms)
        latency_from_us_ms: 从美国访问延迟(ms)
        latency_from_eu_ms: 从欧洲访问延迟(ms)
        failover_targets: 灾备切换目标区域列表
        data_replication_lag_ms: 数据复制延迟(ms)
        last_health_check: 最后健康检查时间
        health_check_success_rate: 健康检查成功率
    """

    region_id: str = Field(description="区域标识，如 cn-north-1")
    name: str = Field(description="区域名称")
    country: str = Field(default="", description="国家代码")
    continent: str = Field(default="", description="大洲")
    latitude: float = Field(default=0.0, description="纬度")
    longitude: float = Field(default=0.0, description="经度")

    api_endpoint: str = Field(default="", description="API 端点")
    cdn_endpoint: str = Field(default="", description="CDN 端点")

    role: RegionRole = RegionRole.PRIMARY
    status: RegionStatus = RegionStatus.ACTIVE

    latency_from_cn_ms: float = Field(default=100, description="从中国访问延迟 (ms)")
    latency_from_us_ms: float = Field(default=200, description="从美国访问延迟 (ms)")
    latency_from_eu_ms: float = Field(default=250, description="从欧洲访问延迟 (ms)")

    failover_targets: list[str] = Field(default_factory=list, description="灾备切换目标区域")
    data_replication_lag_ms: float = Field(default=0, description="数据复制延迟 (ms)")

    last_health_check: float = Field(default=0)
    health_check_success_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class RoutingPolicy(BaseModel):
    """路由策略配置

    定义流量路由的决策规则。

    Attributes:
        prefer_low_latency: 优先低延迟
        prefer_data_residency: 优先数据驻留合规
        allow_cross_region: 是否允许跨区域访问
        failover_enabled: 是否启用灾备切换
        failover_threshold: 灾备切换阈值（健康检查成功率）
    """

    prefer_low_latency: bool = Field(default=True, description="优先低延迟")
    prefer_data_residency: bool = Field(default=True, description="优先数据驻留合规")
    allow_cross_region: bool = Field(default=False, description="是否允许跨区域访问")
    failover_enabled: bool = Field(default=True, description="是否启用灾备切换")
    failover_threshold: float = Field(default=0.5, description="灾备切换阈值（健康检查成功率）")


class RoutingDecision(BaseModel):
    """路由决策结果

    包含路由决策的完整信息。

    Attributes:
        target_region: 目标区域ID
        endpoint: 目标端点URL
        reason: 路由原因
        estimated_latency_ms: 预估延迟(ms)
        is_failover: 是否为灾备切换
        data_residency_compliant: 是否符合数据驻留要求
    """

    target_region: str
    endpoint: str
    reason: str
    estimated_latency_ms: float
    is_failover: bool = False
    data_residency_compliant: bool = True


# ==================== 区域注册表 ====================

_regions: dict[str, DeployRegion] = {}
_routing_policy = RoutingPolicy()


def register_region(region: DeployRegion) -> DeployRegion:
    """注册部署区域

    将区域添加到注册表，用于后续路由决策。

    Args:
        region: 区域配置

    Returns:
        注册的区域配置
    """
    _regions[region.region_id] = region
    logger.info("部署区域已注册: id=%s name=%s role=%s", region.region_id, region.name, region.role)
    return region


def get_region(region_id: str) -> DeployRegion | None:
    """获取区域信息

    Args:
        region_id: 区域ID

    Returns:
        区域配置或 None
    """
    return _regions.get(region_id)


def list_regions(status: RegionStatus | None = None) -> list[DeployRegion]:
    """列出部署区域

    Args:
        status: 可选的状态过滤

    Returns:
        区域列表，按角色和ID排序
    """
    regions = list(_regions.values())
    if status:
        regions = [r for r in regions if r.status == status]
    regions.sort(key=lambda r: (r.role != RegionRole.PRIMARY, r.region_id))
    return regions


def update_region_status(region_id: str, status: RegionStatus) -> DeployRegion | None:
    """更新区域状态

    状态变更时可能触发灾备切换。

    Args:
        region_id: 区域ID
        status: 新状态

    Returns:
        更新后的区域配置或 None
    """
    region = _regions.get(region_id)
    if not region:
        return None

    old_status = region.status
    region.status = status
    region.last_health_check = time.time()

    logger.info("区域状态更新: id=%s %s -> %s", region_id, old_status, status)

    if status == RegionStatus.OFFLINE and _routing_policy.failover_enabled:
        _trigger_failover(region_id)

    return region


def update_health_check(region_id: str, success_rate: float, replication_lag_ms: float = 0) -> DeployRegion | None:
    """更新健康检查结果

    根据健康检查结果更新区域状态，可能触发降级或灾备切换。

    Args:
        region_id: 区域ID
        success_rate: 健康检查成功率
        replication_lag_ms: 数据复制延迟(ms)

    Returns:
        更新后的区域配置或 None
    """
    region = _regions.get(region_id)
    if not region:
        return None

    region.health_check_success_rate = success_rate
    region.data_replication_lag_ms = replication_lag_ms
    region.last_health_check = time.time()

    if success_rate < _routing_policy.failover_threshold:
        if region.status == RegionStatus.ACTIVE:
            region.status = RegionStatus.DEGRADED
            logger.warning("区域降级: id=%s success_rate=%.2f", region_id, success_rate)

            if _routing_policy.failover_enabled:
                _trigger_failover(region_id)

    return region


# ==================== 路由决策 ====================


def route_request(
    user_country: str = "",
    user_continent: str = "",
    data_residency_region: str = "",
    required_latency_ms: float = 0,
) -> RoutingDecision:
    """路由请求到最优区域

    根据路由策略选择最优的目标区域。

    策略优先级：
      1. 数据驻留合规（如果指定了数据驻留区域）
      2. 低延迟（根据用户位置选择最近区域）
      3. 健康状态（排除不健康区域）
      4. 灾备切换（主区域不可用时切换）

    Args:
        user_country: 用户所在国家
        user_continent: 用户所在大洲
        data_residency_region: 数据驻留区域要求
        required_latency_ms: 延迟要求

    Returns:
        RoutingDecision 包含目标区域和路由原因
    """
    active_regions = [r for r in _regions.values() if r.status in (RegionStatus.ACTIVE, RegionStatus.DEGRADED)]

    if not active_regions:
        return RoutingDecision(
            target_region="",
            endpoint="",
            reason="无可用区域",
            estimated_latency_ms=0,
        )

    if data_residency_region and _routing_policy.prefer_data_residency:
        target = _regions.get(data_residency_region)
        if target and target.status in (RegionStatus.ACTIVE, RegionStatus.DEGRADED):
            latency = _estimate_latency(target, user_continent)
            return RoutingDecision(
                target_region=target.region_id,
                endpoint=target.api_endpoint,
                reason="数据驻留合规",
                estimated_latency_ms=latency,
                data_residency_compliant=True,
            )

    candidates = active_regions
    if not _routing_policy.allow_cross_region and data_residency_region:
        candidates = [r for r in active_regions if r.region_id == data_residency_region]
        if not candidates:
            candidates = active_regions

    scored: list[tuple[float, DeployRegion]] = []
    for region in candidates:
        score = 0.0

        latency = _estimate_latency(region, user_continent)
        score -= latency * 0.01

        if required_latency_ms and latency > required_latency_ms:
            score -= 100

        score += region.health_check_success_rate * 50

        if region.role == RegionRole.PRIMARY:
            score += 10
        elif region.role == RegionRole.SECONDARY:
            score += 5

        if region.status == RegionStatus.DEGRADED:
            score -= 20

        scored.append((score, region))

    scored.sort(key=lambda x: -x[0])
    best_region = scored[0][1]

    latency = _estimate_latency(best_region, user_continent)
    is_failover = best_region.role != RegionRole.PRIMARY and data_residency_region != best_region.region_id

    return RoutingDecision(
        target_region=best_region.region_id,
        endpoint=best_region.api_endpoint,
        reason="最低延迟" if _routing_policy.prefer_low_latency else "最优评分",
        estimated_latency_ms=latency,
        is_failover=is_failover,
        data_residency_compliant=(not data_residency_region or best_region.region_id == data_residency_region),
    )


def _estimate_latency(region: DeployRegion, user_continent: str) -> float:
    """估算延迟"""
    continent = user_continent.lower()
    if "asia" in continent or "cn" in continent:
        return region.latency_from_cn_ms
    elif "america" in continent or "us" in continent:
        return region.latency_from_us_ms
    elif "europe" in continent or "eu" in continent:
        return region.latency_from_eu_ms
    return (region.latency_from_cn_ms + region.latency_from_us_ms + region.latency_from_eu_ms) / 3


def _trigger_failover(region_id: str) -> None:
    """触发灾备切换"""
    region = _regions.get(region_id)
    if not region:
        return

    for target_id in region.failover_targets:
        target = _regions.get(target_id)
        if target and target.status == RegionStatus.ACTIVE:
            logger.warning(
                "灾备切换: 从 %s 切换到 %s",
                region_id, target_id,
            )
            target.role = RegionRole.PRIMARY
            region.role = RegionRole.STANDBY
            return

    logger.error("灾备切换失败: 无可用目标区域, source=%s", region_id)


# ==================== 初始化默认区域 ====================


def _init_default_regions() -> None:
    """初始化默认部署区域"""
    if _regions:
        return

    defaults = [
        DeployRegion(
            region_id="cn-north-1",
            name="华北区域",
            country="CN",
            continent="Asia",
            latitude=39.9,
            longitude=116.4,
            api_endpoint="https://api-cn-north.example.com",
            cdn_endpoint="https://cdn-cn-north.example.com",
            role=RegionRole.PRIMARY,
            status=RegionStatus.ACTIVE,
            latency_from_cn_ms=20,
            latency_from_us_ms=180,
            latency_from_eu_ms=220,
            failover_targets=["cn-east-1", "ap-southeast-1"],
        ),
        DeployRegion(
            region_id="cn-east-1",
            name="华东区域",
            country="CN",
            continent="Asia",
            latitude=31.2,
            longitude=121.5,
            api_endpoint="https://api-cn-east.example.com",
            cdn_endpoint="https://cdn-cn-east.example.com",
            role=RegionRole.SECONDARY,
            status=RegionStatus.ACTIVE,
            latency_from_cn_ms=30,
            latency_from_us_ms=170,
            latency_from_eu_ms=210,
            failover_targets=["cn-north-1", "ap-southeast-1"],
        ),
        DeployRegion(
            region_id="ap-southeast-1",
            name="东南亚区域",
            country="SG",
            continent="Asia",
            latitude=1.3,
            longitude=103.8,
            api_endpoint="https://api-ap-southeast.example.com",
            cdn_endpoint="https://cdn-ap-southeast.example.com",
            role=RegionRole.SECONDARY,
            status=RegionStatus.ACTIVE,
            latency_from_cn_ms=60,
            latency_from_us_ms=150,
            latency_from_eu_ms=180,
            failover_targets=["cn-east-1"],
        ),
        DeployRegion(
            region_id="us-east-1",
            name="美东区域",
            country="US",
            continent="North America",
            latitude=39.0,
            longitude=-77.5,
            api_endpoint="https://api-us-east.example.com",
            cdn_endpoint="https://cdn-us-east.example.com",
            role=RegionRole.PRIMARY,
            status=RegionStatus.ACTIVE,
            latency_from_cn_ms=180,
            latency_from_us_ms=15,
            latency_from_eu_ms=80,
            failover_targets=["us-west-1", "eu-west-1"],
        ),
        DeployRegion(
            region_id="eu-west-1",
            name="西欧区域",
            country="DE",
            continent="Europe",
            latitude=50.1,
            longitude=8.7,
            api_endpoint="https://api-eu-west.example.com",
            cdn_endpoint="https://cdn-eu-west.example.com",
            role=RegionRole.PRIMARY,
            status=RegionStatus.ACTIVE,
            latency_from_cn_ms=220,
            latency_from_us_ms=80,
            latency_from_eu_ms=15,
            failover_targets=["eu-central-1", "us-east-1"],
        ),
    ]

    for region in defaults:
        register_region(region)


_init_default_regions()
