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


@router.get("/health", response_model=HealthResponse)
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


@router.get("/health/detail")
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


@router.get("/mcp/status")
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


@router.get("/failover/status")
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


@router.get("/canary/flags")
async def list_feature_flags() -> dict:
    """查看所有功能开关状态"""
    from deploy.canary import get_all_flags

    return {"flags": get_all_flags()}


@router.post("/canary/rollout")
async def update_rollout(request: RolloutUpdateRequest) -> dict:
    """更新灰度比例"""
    from deploy.canary import update_rollout as _update_rollout

    success = _update_rollout(request.feature_name, request.percentage)
    if not success:
        return {"success": False, "error": f"功能开关 {request.feature_name} 未注册"}
    return {"success": True, "feature_name": request.feature_name, "rollout_percentage": request.percentage}


@router.post("/canary/whitelist")
async def update_whitelist(request: WhitelistRequest) -> dict:
    """更新白名单"""
    from deploy.canary import add_to_whitelist

    success = add_to_whitelist(request.feature_name, request.user_ids)
    if not success:
        return {"success": False, "error": f"功能开关 {request.feature_name} 未注册"}
    return {"success": True, "feature_name": request.feature_name, "added_count": len(request.user_ids)}


@router.post("/canary/toggle")
async def toggle_feature(request: FeatureToggleRequest) -> dict:
    """启用/禁用功能开关"""
    from deploy.canary import set_feature_enabled

    success = set_feature_enabled(request.feature_name, request.enabled)
    if not success:
        return {"success": False, "error": f"功能开关 {request.feature_name} 未注册"}
    return {"success": True, "feature_name": request.feature_name, "enabled": request.enabled}


# ==================== 运营指标 ====================

@router.get("/metrics/summary")
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


# ==================== Token 预算管理 ====================

@router.get("/token/usage/{user_id}")
async def get_user_token_usage(user_id: str) -> dict:
    """查询用户当日 Token 用量"""
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_user_daily_usage(user_id)


@router.get("/token/budget/{user_id}")
async def check_token_budget(user_id: str, session_id: str = "") -> dict:
    """检查用户 Token 预算"""
    from agent.core.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.check_budget(user_id, session_id or "default")


# ==================== 审计日志 ====================

@router.get("/audit/logs")
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


@router.post("/audit/flush")
async def flush_audit_buffer() -> dict:
    """手动刷新审计日志缓冲区，将 Redis 中的日志持久化到 PostgreSQL"""
    from agent.core.audit import get_audit_logger

    audit = get_audit_logger()
    count = await audit.flush_buffer()
    return {"flushed": count, "message": f"已持久化 {count} 条审计日志"}
