"""管理路由

提供系统管理接口:
  - 基础健康检查
  - 深度健康检查（含各组件状态）
  - MCP 服务状态
  - 降级状态
  - 故障转移状态
  - 灰度发布管理
  - 运营指标
"""

import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.models.response import HealthResponse
from agent.core.config import get_settings
from agent.core.mcp_integration import MCP_SERVER_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/health", response_model=HealthResponse, summary="基础健康检查")
async def health_check() -> HealthResponse:
    """基础健康检查接口"""
    settings = get_settings()

    components = {
        "api": "healthy",
        "environment": settings.environment,
    }

    mcp_status = {}
    for name, config in MCP_SERVER_REGISTRY.items():
        mcp_status[name] = "registered" if config.enabled else "disabled"
    components["mcp_servers"] = str(len([s for s in mcp_status.values() if s == "registered"]))

    return HealthResponse(
        status="healthy",
        version="0.1.0",
        timestamp=datetime.now(),
        components=components,
    )


@router.get("/health/detail", summary="深度健康检查")
async def detailed_health_check() -> dict:
    """深度健康检查

    检查各组件（Redis/PostgreSQL/MCP Registry/LLM）的连通性和响应时间，
    并根据结果评估降级级别。
    """
    from deploy.ha_manager import get_health_checker, get_degradation_manager

    checker = get_health_checker()
    health_result = await checker.full_check()

    degradation_mgr = get_degradation_manager()
    degradation_mgr.evaluate(health_result)
    degradation_status = degradation_mgr.get_status()

    return {
        "health": health_result,
        "degradation": degradation_status,
    }


@router.get("/mcp/status", summary="MCP服务状态")
async def mcp_status() -> dict:
    """查看 MCP 服务状态"""
    servers = {}
    for name, config in MCP_SERVER_REGISTRY.items():
        servers[name] = {
            "name": config.name,
            "description": config.description,
            "transport": config.transport,
            "url": config.url,
            "enabled": config.enabled,
        }
    return {"servers": servers, "total": len(servers)}


@router.get("/failover/status", summary="故障转移状态")
async def failover_status() -> dict:
    """查看故障转移状态"""
    from deploy.ha_manager import get_failover_manager

    manager = get_failover_manager()
    return manager.get_status()


# ==================== 灰度发布管理 ====================

class RolloutUpdateRequest(BaseModel):
    """灰度比例更新请求"""

    feature_name: str = Field(..., description="功能名称")
    percentage: float = Field(..., ge=0.0, le=100.0, description="灰度比例 0-100")


class WhitelistRequest(BaseModel):
    """白名单更新请求"""

    feature_name: str = Field(..., description="功能名称")
    user_ids: list[str] = Field(..., description="用户ID列表")


class FeatureToggleRequest(BaseModel):
    """功能开关请求"""

    feature_name: str = Field(..., description="功能名称")
    enabled: bool = Field(..., description="是否启用")


@router.get("/canary/flags", summary="获取灰度标志")
async def list_feature_flags() -> dict:
    """查看所有功能开关状态"""
    from deploy.canary import get_all_flags

    return {"flags": get_all_flags()}


@router.post("/canary/rollout", summary="设置灰度比例")
async def update_rollout(request: RolloutUpdateRequest) -> dict:
    """更新灰度比例"""
    from deploy.canary import update_rollout as _update_rollout

    success = _update_rollout(request.feature_name, request.percentage)
    if not success:
        return {"success": False, "error": f"功能开关 {request.feature_name} 未注册"}
    return {"success": True, "feature_name": request.feature_name, "rollout_percentage": request.percentage}


@router.post("/canary/whitelist", summary="设置灰度白名单")
async def update_whitelist(request: WhitelistRequest) -> dict:
    """更新白名单"""
    from deploy.canary import add_to_whitelist

    success = add_to_whitelist(request.feature_name, request.user_ids)
    if not success:
        return {"success": False, "error": f"功能开关 {request.feature_name} 未注册"}
    return {"success": True, "feature_name": request.feature_name, "added_count": len(request.user_ids)}


@router.post("/canary/toggle", summary="切换灰度开关")
async def toggle_feature(request: FeatureToggleRequest) -> dict:
    """启用/禁用功能开关"""
    from deploy.canary import set_feature_enabled

    success = set_feature_enabled(request.feature_name, request.enabled)
    if not success:
        return {"success": False, "error": f"功能开关 {request.feature_name} 未注册"}
    return {"success": True, "feature_name": request.feature_name, "enabled": request.enabled}


# ==================== 运营指标 ====================

@router.get("/metrics/summary", summary="获取指标摘要")
async def metrics_summary() -> dict:
    """运营指标汇总

    返回系统关键运营指标，用于运营仪表盘展示。
    """
    from observability.metrics import (
        AGENT_CALL_COUNT,
        AGENT_ACTIVE_SESSIONS,
        MCP_TOOL_CALL_COUNT,
        LLM_TOKEN_USAGE,
    )

    try:
        agent_metrics = {}
        for sample in AGENT_CALL_COUNT.collect():
            for metric in sample.samples:
                agent_name = metric.labels.get("agent_name", "unknown")
                status = metric.labels.get("status", "unknown")
                key = f"{agent_name}_{status}"
                agent_metrics[key] = metric.value

        mcp_metrics = {}
        for sample in MCP_TOOL_CALL_COUNT.collect():
            for metric in sample.samples:
                server = metric.labels.get("server_name", "unknown")
                status = metric.labels.get("status", "unknown")
                key = f"{server}_{status}"
                mcp_metrics[key] = metric.value

        token_metrics = {}
        for sample in LLM_TOKEN_USAGE.collect():
            for metric in sample.samples:
                model = metric.labels.get("model", "unknown")
                token_type = metric.labels.get("token_type", "unknown")
                key = f"{model}_{token_type}"
                token_metrics[key] = metric.value

        active_sessions = 0
        for sample in AGENT_ACTIVE_SESSIONS.collect():
            for metric in sample.samples:
                active_sessions = int(metric.value)

        return {
            "active_sessions": active_sessions,
            "agent_calls": agent_metrics,
            "mcp_tool_calls": mcp_metrics,
            "llm_token_usage": token_metrics,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error("获取运营指标失败: %s", e)
        return {"error": str(e), "timestamp": datetime.now().isoformat()}


# ==================== 用户管理 ====================

@router.get("/users", summary="获取用户列表")
async def list_users(limit: int = 50, offset: int = 0) -> dict:
    """获取用户列表，用于管理员快速选择用户"""
    from security.user_store import get_user_store

    store = get_user_store()
    users = await store.list_users(limit=limit, offset=offset)
    return {"items": users, "total": len(users)}


# ==================== Token 预算管理 ====================

@router.get("/token/usage/{user_id}", summary="查询用户Token用量")
async def get_user_token_usage(user_id: str) -> dict:
    """查询用户当日 Token 用量"""
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_user_daily_usage(user_id)


@router.get("/token/budget/{user_id}", summary="查询用户Token预算")
async def check_token_budget(user_id: str, session_id: str = "") -> dict:
    """检查用户 Token 预算"""
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.check_budget(user_id, session_id or "default")


# ==================== 审计日志 ====================

@router.get("/audit/logs", summary="查询审计日志")
async def query_audit_logs(
    event_type: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """查询审计日志"""
    from agent.core.audit import get_audit_logger

    audit = get_audit_logger()
    return await audit.query_logs(
        event_type=event_type,
        user_id=user_id,
        action=action,
        limit=limit,
        offset=offset,
    )


@router.post("/audit/flush", summary="手动刷新审计日志缓冲区")
async def flush_audit_buffer() -> dict:
    """手动刷新审计日志缓冲区，将 Redis 中的日志持久化到 PostgreSQL"""
    from agent.core.audit import get_audit_logger

    audit = get_audit_logger()
    count = await audit.flush_buffer()
    return {"flushed": count, "message": f"已持久化 {count} 条审计日志"}


# ==================== 成本报表 ====================

@router.get("/cost/report", summary="获取成本报告")
async def get_cost_report(date: str = "") -> dict:
    """获取成本报表

    汇总全局和按 Agent 分类的 Token 消耗与成本数据。

    Args:
        date: 日期（格式 YYYY-MM-DD，默认今天）
    """
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_cost_report(date)


@router.get("/cost/tenant/{tenant_id}", summary="查询租户成本")
async def get_tenant_cost(tenant_id: str) -> dict:
    """获取租户当日成本统计"""
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_tenant_daily_usage(tenant_id)


@router.get("/cost/agent/{agent_name}", summary="查询Agent成本")
async def get_agent_cost(agent_name: str) -> dict:
    """获取 Agent 当日成本统计"""
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_agent_daily_usage(agent_name)


# ==================== SLA 预算管理 ====================

@router.get("/sla/definitions", summary="获取SLA定义")
async def list_sla_with_budget() -> dict:
    """列出所有 SLA 层级及其预算额度"""
    from agent.core.sla import list_sla_definitions

    definitions = list_sla_definitions()
    return {
        "definitions": [
            {
                "tier": d.tier.value,
                "availability_target": d.availability_target,
                "latency_p50_target_ms": d.latency_p50_target_ms,
                "latency_p90_target_ms": d.latency_p90_target_ms,
                "latency_p99_target_ms": d.latency_p99_target_ms,
                "error_rate_target": d.error_rate_target,
                "throughput_target_rps": d.throughput_target_rps,
                "user_daily_budget": d.user_daily_budget,
                "session_budget": d.session_budget,
                "tenant_daily_budget": d.tenant_daily_budget,
                "agent_per_call_budget": d.agent_per_call_budget,
                "max_model_tier": d.max_model_tier,
            }
            for d in definitions
        ]
    }


@router.get("/sla/budget/{tier}", summary="查询SLA层级预算")
async def get_sla_budget(tier: str) -> dict:
    """获取指定 SLA 层级的预算额度

    Args:
        tier: SLA 层级 (standard/professional/enterprise)
    """
    from agent.core.sla import SLATier, get_budget_for_tier

    try:
        sla_tier = SLATier(tier)
        return get_budget_for_tier(sla_tier)
    except ValueError:
        return {"error": f"无效的 SLA 层级: {tier}", "valid_tiers": [t.value for t in SLATier]}


# ==================== RTO/RPO 灾备监控 ====================


@router.get("/dr/status", summary="灾备指标总览")
async def disaster_recovery_status() -> dict:
    """获取 RTO/RPO 灾备指标总览

    返回当前系统的灾备恢复指标，包括：
    - RTO: 实际恢复时间 vs 目标恢复时间
    - RPO: 实际数据丢失窗口 vs 目标数据丢失窗口
    - 故障转移历史
    - 数据完整性校验结果
    """
    from deploy.ha_manager import get_dr_monitor

    monitor = get_dr_monitor()
    return monitor.get_status()


@router.get("/dr/metrics", summary="RTO/RPO 详细指标")
async def dr_detailed_metrics() -> dict:
    """获取 RTO/RPO 详细指标数据

    返回灾备指标的完整数据，适合监控仪表盘展示。
    """
    from deploy.ha_manager import get_dr_monitor

    monitor = get_dr_monitor()
    metrics = monitor.get_metrics()

    return {
        "rto": {
            "current_seconds": round(metrics.rto_seconds, 3),
            "target_seconds": metrics.target_rto_seconds,
            "violations": metrics.rto_violations,
            "last_failover_duration": round(metrics.last_failover_duration, 3),
            "failover_count": metrics.failover_count,
            "recovery_count": metrics.recovery_count,
        },
        "rpo": {
            "current_seconds": round(metrics.rpo_seconds, 3),
            "target_seconds": metrics.target_rpo_seconds,
            "violations": metrics.rpo_violations,
            "current_replication_lag_ms": round(metrics.current_replication_lag_ms, 2),
            "max_replication_lag_ms": round(metrics.max_replication_lag_ms, 2),
            "data_loss_bytes": metrics.rpo_bytes,
        },
        "integrity": {
            "verified": metrics.data_integrity_verified,
            "last_check": metrics.last_integrity_check,
        },
        "compliance": {
            "rto_compliant": metrics.rto_seconds <= metrics.target_rto_seconds,
            "rpo_compliant": metrics.rpo_seconds <= metrics.target_rpo_seconds,
        },
    }


@router.get("/dr/history", summary="故障转移历史")
async def failover_history(limit: int = 20) -> dict:
    """获取故障转移历史记录

    Args:
        limit: 返回记录数量，默认 20 条
    """
    from deploy.ha_manager import get_dr_monitor

    monitor = get_dr_monitor()
    events = monitor.get_failover_history(limit=limit)
    return {"events": events, "total": len(events)}


@router.post("/dr/verify-integrity", summary="手动触发数据完整性校验")
async def verify_data_integrity() -> dict:
    """手动触发数据完整性校验

    校验 Redis 和 PostgreSQL 的主从数据一致性。
    在故障转移完成后建议执行此操作。
    """
    from deploy.ha_manager import get_dr_monitor

    monitor = get_dr_monitor()
    result = await monitor.verify_data_integrity()
    return {
        "integrity_verified": result,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/dr/replication", summary="跨区域复制状态")
async def replication_status() -> dict:
    """获取跨区域数据复制状态

    返回各区域的数据复制延迟和整体 RPO 评估。
    """
    from deploy.multi_region import get_replication_summary

    return get_replication_summary()


@router.get("/ha/full-status", summary="高可用全量状态")
async def ha_full_status() -> dict:
    """获取高可用系统全量状态

    整合健康检查、心跳监控、故障转移、降级、灾备指标的全量状态。
    """
    from deploy.ha_manager import get_ha_orchestrator

    orchestrator = get_ha_orchestrator()
    return orchestrator.get_full_status()


@router.get("/heartbeat/status", summary="心跳监控状态")
async def heartbeat_status() -> dict:
    """获取心跳监控状态

    返回各组件的心跳检测结果，包括存活状态、延迟、连续成功/失败次数。
    """
    from deploy.ha_manager import get_heartbeat_monitor

    monitor = get_heartbeat_monitor()
    return monitor.get_status()
