"""多区域部署路由"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from deploy.multi_region import (
    register_region,
    get_region,
    list_regions,
    update_region_status,
    update_health_check,
    route_request,
    DeployRegion,
    RegionStatus,
    RoutingDecision,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/regions", tags=["多区域部署"])


class RouteRequestModel(BaseModel):
    user_country: str = ""
    user_continent: str = ""
    data_residency_region: str = ""
    required_latency_ms: float = 0


class HealthCheckRequest(BaseModel):
    success_rate: float = Field(ge=0.0, le=1.0)
    replication_lag_ms: float = Field(default=0, ge=0)


@router.get("", response_model=list[DeployRegion])
async def api_list_regions(status: RegionStatus | None = None) -> list[DeployRegion]:
    """列出部署区域"""
    return list_regions(status)


@router.get("/{region_id}", response_model=DeployRegion)
async def api_get_region(region_id: str) -> DeployRegion:
    """获取区域详情"""
    region = get_region(region_id)
    if not region:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="区域不存在")
    return region


@router.post("", response_model=DeployRegion)
async def api_register_region(region: DeployRegion) -> DeployRegion:
    """注册部署区域"""
    return register_region(region)


@router.put("/{region_id}/status", response_model=DeployRegion)
async def api_update_region_status(region_id: str, status: RegionStatus) -> DeployRegion:
    """更新区域状态"""
    result = update_region_status(region_id, status)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="区域不存在")
    return result


@router.put("/{region_id}/health", response_model=DeployRegion)
async def api_update_health_check(region_id: str, request: HealthCheckRequest) -> DeployRegion:
    """更新健康检查结果"""
    result = update_health_check(region_id, request.success_rate, request.replication_lag_ms)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="区域不存在")
    return result


@router.post("/route", response_model=RoutingDecision)
async def api_route_request(request: RouteRequestModel) -> RoutingDecision:
    """路由请求到最优区域"""
    return route_request(
        user_country=request.user_country,
        user_continent=request.user_continent,
        data_residency_region=request.data_residency_region,
        required_latency_ms=request.required_latency_ms,
    )
