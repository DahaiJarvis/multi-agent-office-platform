"""性能基准与 SLA 路由"""

import logging

from fastapi import APIRouter, Query

from agent.core.observability.sla import (
    get_sla_definition,
    list_sla_definitions,
    get_current_metrics,
    check_sla_compliance,
    run_benchmark,
    SLATier,
    SLADefinition,
    SLAStatus,
    BenchmarkResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sla", tags=["性能与SLA"])


@router.get("/definitions", response_model=list[SLADefinition], summary="获取SLA定义列表")
async def api_list_sla_definitions() -> list[SLADefinition]:
    """列出 SLA 定义"""
    return list_sla_definitions()


@router.get("/definitions/{tier}", response_model=SLADefinition, summary="获取SLA层级定义")
async def api_get_sla_definition(tier: SLATier) -> SLADefinition:
    """获取指定层级的 SLA 定义"""
    return get_sla_definition(tier)


@router.get("/metrics", summary="获取SLA指标")
async def api_get_current_metrics() -> dict:
    """获取当前性能指标"""
    return await get_current_metrics()


@router.get("/compliance", response_model=SLAStatus, summary="获取SLA合规状态")
async def api_check_sla_compliance(tier: SLATier = Query(default=SLATier.PROFESSIONAL)) -> SLAStatus:
    """检查 SLA 合规性"""
    return await check_sla_compliance(tier)


@router.post("/benchmark", response_model=BenchmarkResult, summary="执行SLA基准测试")
async def api_run_benchmark(
    concurrent_users: int = Query(default=10, ge=1, le=100),
    total_requests: int = Query(default=100, ge=10, le=10000),
) -> BenchmarkResult:
    """运行性能基准测试"""
    return await run_benchmark(
        concurrent_users=concurrent_users,
        total_requests=total_requests,
    )
