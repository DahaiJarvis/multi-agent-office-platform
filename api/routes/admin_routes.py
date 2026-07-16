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
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.models.response import HealthResponse
from agent.core.infrastructure.config import get_settings
from agent.core.mcp.mcp_integration import MCP_SERVER_REGISTRY

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
    from agent.core.model.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_user_daily_usage(user_id)


@router.get("/token/budget/{user_id}", summary="查询用户Token预算")
async def check_token_budget(user_id: str, session_id: str = "") -> dict:
    """检查用户 Token 预算"""
    from agent.core.model.token_budget import get_token_budget_manager

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
    from agent.core.observability.audit import get_audit_logger

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
    from agent.core.observability.audit import get_audit_logger

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
    from agent.core.model.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_cost_report(date)


@router.get("/cost/tenant/{tenant_id}", summary="查询租户成本")
async def get_tenant_cost(tenant_id: str) -> dict:
    """获取租户当日成本统计"""
    from agent.core.model.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_tenant_daily_usage(tenant_id)


@router.get("/cost/agent/{agent_name}", summary="查询Agent成本")
async def get_agent_cost(agent_name: str) -> dict:
    """获取 Agent 当日成本统计"""
    from agent.core.model.token_budget import get_token_budget_manager

    budget_mgr = get_token_budget_manager()
    return await budget_mgr.get_agent_daily_usage(agent_name)


# ==================== SLA 预算管理 ====================

@router.get("/sla/definitions", summary="获取SLA定义")
async def list_sla_with_budget() -> dict:
    """列出所有 SLA 层级及其预算额度"""
    from agent.core.observability.sla import list_sla_definitions

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
    from agent.core.observability.sla import SLATier, get_budget_for_tier

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


# ==================== 检查点时间旅行与回放（spec 03） ====================


def _require_admin(request: Request) -> None:
    """校验管理员权限

    Args:
        request: FastAPI 请求对象

    Raises:
        AppException: 权限不足
    """
    user_roles = getattr(request.state, "user_roles", [])
    if "admin" not in user_roles:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.PERMISSION_DENIED, message="需要管理员权限")


class ReplayRequest(BaseModel):
    """回放请求模型"""

    from_step: int = Field(..., ge=1, description="回放起始步骤索引（从1开始）")
    from_version: int | None = Field(None, ge=0, description="回放起始步骤的版本号，None表示最新版本")
    overrides: dict[str, Any] | None = Field(None, description="覆盖配置")
    use_mock_tools: bool = Field(True, description="是否使用Mock工具，默认True")


class ReplayResponse(BaseModel):
    """回放响应模型"""

    new_execution_id: str = Field(..., description="回放生成的新执行记录ID")
    source_execution_id: str = Field(..., description="原执行记录ID")
    from_step: int = Field(..., description="回放起始步骤")
    from_version: int | None = Field(None, description="回放起始版本")
    status: str = Field(..., description="回放状态：running/completed/failed")
    started_at: str = Field(..., description="回放开始时间ISO格式")


@router.post(
    "/executions/{execution_id}/replay",
    response_model=ReplayResponse,
    summary="从指定步骤回放执行任务",
)
async def replay_execution(
    execution_id: str,
    request: Request,
    replay_request: ReplayRequest,
) -> ReplayResponse:
    """从指定步骤/版本回放执行任务

    需要管理员权限。回放使用 Mock 工具客户端，生成新 execution_id，
    不污染原执行记录。

    Args:
        execution_id: 原执行记录ID
        request: FastAPI 请求对象（用于权限校验）
        replay_request: 回放请求参数
    """
    _require_admin(request)

    from datetime import datetime as _dt

    from agent.core.workflow.task_checkpoint import get_task_checkpoint_store

    store = get_task_checkpoint_store()
    started_at = _dt.now().isoformat()

    # spec 中 from_step 从 1 开始，内部 step_index 从 0 开始
    from_step_index = replay_request.from_step - 1

    # 合并 use_mock_tools 到 overrides
    overrides = replay_request.overrides or {}
    overrides["use_mock_tools"] = replay_request.use_mock_tools

    try:
        new_exec_id = await store.replay_from_step(
            execution_id=execution_id,
            from_step=from_step_index,
            from_version=replay_request.from_version,
            overrides=overrides,
        )

        # 查询回放任务状态
        new_exec = await store.get_execution(new_exec_id)
        status = "running"
        if new_exec is not None:
            status = new_exec.status.value

        return ReplayResponse(
            new_execution_id=new_exec_id,
            source_execution_id=execution_id,
            from_step=replay_request.from_step,
            from_version=replay_request.from_version,
            status=status,
            started_at=started_at,
        )
    except ValueError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.INVALID_PARAMETER, message=str(e))
    except RuntimeError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.CONFLICT, message=str(e))


@router.get(
    "/executions/{execution_id}/versions",
    summary="查询指定步骤的历史版本列表",
)
async def list_step_versions(
    execution_id: str,
    request: Request,
    step_index: int = 0,
) -> dict:
    """查询指定步骤的全部历史版本

    需要管理员权限。返回该步骤的全部检查点版本，按版本号升序排列。

    Args:
        execution_id: 执行记录ID
        request: FastAPI 请求对象（用于权限校验）
        step_index: 步骤索引（从0开始）
    """
    _require_admin(request)

    from agent.core.workflow.task_checkpoint import get_task_checkpoint_store

    store = get_task_checkpoint_store()
    versions = await store.list_step_versions(execution_id, step_index)
    return {
        "execution_id": execution_id,
        "step_index": step_index,
        "total": len(versions),
        "versions": [v.model_dump() for v in versions],
    }


@router.get(
    "/executions/{execution_id}/state",
    summary="重建指定步骤/版本的任务状态快照",
)
async def get_state_at_step(
    execution_id: str,
    request: Request,
    step_index: int = 0,
    version: int | None = None,
) -> dict:
    """重建任务在指定步骤/版本时的完整状态快照

    需要管理员权限。用于时间旅行查看任务历史状态。

    Args:
        execution_id: 执行记录ID
        request: FastAPI 请求对象（用于权限校验）
        step_index: 目标步骤索引（从0开始）
        version: 指定版本号，None 表示最新版本
    """
    _require_admin(request)

    from agent.core.workflow.task_checkpoint import get_task_checkpoint_store

    store = get_task_checkpoint_store()
    try:
        snapshot = await store.get_state_at_step(execution_id, step_index, version)
        return {
            "execution_id": execution_id,
            "step_index": step_index,
            "version": version,
            "snapshot": snapshot.model_dump(),
        }
    except ValueError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.INVALID_PARAMETER, message=str(e))


# ==================== Trace-Eval-Improve 闭环管理（spec 04） ====================


class RuleApproveRequest(BaseModel):
    """规则审核请求"""

    approved_by: str = Field(..., description="审核人")
    reason: str = Field(default="", description="审核说明")


class RuleRejectRequest(BaseModel):
    """规则拒绝请求"""

    reason: str = Field(default="", description="拒绝原因")


class RegressionTriggerRequest(BaseModel):
    """回归测试触发请求"""

    fixture_ids: list[str] = Field(..., description="要回归测试的 Fixture ID 列表")
    baseline_scores: dict[str, float] = Field(
        default_factory=dict,
        description="各 fixture 的 baseline 评分 {fixture_id: score}",
    )
    k: int = Field(default=1, ge=1, description="pass^k 的 k 值")


class ScanTriggerRequest(BaseModel):
    """闭环扫描触发请求"""

    since_hours: int = Field(default=24, ge=1, description="扫描最近 N 小时")
    agent_name: str | None = Field(default=None, description="限定 Agent")
    include_thumbs_down: bool = Field(default=True, description="是否纳入用户负反馈")
    max_batch_size: int = Field(default=50, ge=1, le=500, description="单批最大处理量")


def _get_failure_archive():
    """获取 FailureArchive 单例（延迟初始化）

    使用模块级全局变量缓存 FailureArchive 实例，
    避免每次请求都创建新实例（归档记录保存在内存中）。
    """
    global _failure_archive_instance
    if _failure_archive_instance is None:
        from agent.evaluation.improvement.failure_archive import FailureArchive
        _failure_archive_instance = FailureArchive()
    return _failure_archive_instance


# FailureArchive 全局单例
_failure_archive_instance: Any | None = None


@router.get(
    "/eval/archives",
    summary="查询失败归档记录",
)
async def list_eval_archives(
    request: Request,
    improvement_status: str | None = None,
    failure_pattern: str | None = None,
    limit: int = 50,
) -> dict:
    """查询失败案例归档记录

    需要管理员权限。支持按改进状态和失败模式过滤。

    Args:
        request: FastAPI 请求对象（用于权限校验）
        improvement_status: 按改进状态过滤（pending/improving/resolved）
        failure_pattern: 按失败模式过滤（injection_attack/pii_leakage/...）
        limit: 返回记录数量上限
    """
    _require_admin(request)

    archive = _get_failure_archive()
    records = archive.list_archives(
        improvement_status=improvement_status,
        failure_pattern=failure_pattern,
    )
    records = records[:limit]
    return {
        "items": [r.model_dump() for r in records],
        "total": len(records),
    }


@router.get(
    "/eval/archives/{archive_id}",
    summary="查询单个失败归档记录",
)
async def get_eval_archive(
    archive_id: str,
    request: Request,
) -> dict:
    """查询单个失败案例归档记录详情

    需要管理员权限。

    Args:
        archive_id: 归档记录 ID
        request: FastAPI 请求对象（用于权限校验）
    """
    _require_admin(request)

    archive = _get_failure_archive()
    record = archive.get_archive(archive_id)
    if record is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND, message=f"归档记录不存在: {archive_id}")
    return record.model_dump()


@router.get(
    "/eval/rules",
    summary="查询护栏规则候选列表",
)
async def list_eval_rules(
    request: Request,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """查询护栏规则候选列表

    需要管理员权限。支持按状态过滤。

    Args:
        request: FastAPI 请求对象（用于权限校验）
        status: 按状态过滤（candidate/sandboxed/approved/rejected/online）
        limit: 返回记录数量上限
    """
    _require_admin(request)

    archive = _get_failure_archive()
    records = archive.list_rule_candidates(status=status)
    records = records[:limit]
    return {
        "items": [r.model_dump() for r in records],
        "total": len(records),
    }


@router.post(
    "/eval/rules/{rule_id}/approve",
    summary="审核通过规则候选",
)
async def approve_eval_rule(
    rule_id: str,
    request: Request,
    approve_request: RuleApproveRequest,
) -> dict:
    """审核通过护栏规则候选

    需要管理员权限。规则候选需已通过沙箱验证才能审核通过。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象（用于权限校验）
        approve_request: 审核请求
    """
    _require_admin(request)

    archive = _get_failure_archive()
    success = archive.approve_rule(rule_id, approved_by=approve_request.approved_by)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(
            ErrorCode.CONFLICT,
            message=f"规则审核失败: rule_id={rule_id}（可能未通过沙箱验证或不存在）",
        )
    return {
        "success": True,
        "rule_id": rule_id,
        "approved_by": approve_request.approved_by,
        "status": "approved",
    }


@router.post(
    "/eval/rules/{rule_id}/reject",
    summary="拒绝规则候选",
)
async def reject_eval_rule(
    rule_id: str,
    request: Request,
    reject_request: RuleRejectRequest,
) -> dict:
    """拒绝护栏规则候选

    需要管理员权限。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象（用于权限校验）
        reject_request: 拒绝请求
    """
    _require_admin(request)

    archive = _get_failure_archive()
    success = archive.reject_rule(rule_id, reason=reject_request.reason)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND, message=f"规则候选不存在: {rule_id}")
    return {
        "success": True,
        "rule_id": rule_id,
        "reason": reject_request.reason,
        "status": "rejected",
    }


@router.post(
    "/eval/rules/{rule_id}/online",
    summary="规则上线",
)
async def online_eval_rule(
    rule_id: str,
    request: Request,
) -> dict:
    """将已审核通过的规则标记为上线

    需要管理员权限。规则需已审核通过才能上线。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象（用于权限校验）
    """
    _require_admin(request)

    archive = _get_failure_archive()
    success = archive.mark_online(rule_id)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(
            ErrorCode.CONFLICT,
            message=f"规则上线失败: rule_id={rule_id}（可能未审核通过或不存在）",
        )
    return {
        "success": True,
        "rule_id": rule_id,
        "status": "online",
    }


@router.post(
    "/eval/regression",
    summary="触发回归测试",
)
async def trigger_regression(
    request: Request,
    regression_request: RegressionTriggerRequest,
) -> dict:
    """触发改进效果回归测试

    需要管理员权限。对指定 Fixture 列表执行回归测试，对比 baseline 评分。

    Args:
        request: FastAPI 请求对象（用于权限校验）
        regression_request: 回归测试请求
    """
    _require_admin(request)

    from agent.evaluation.replay.regression_test_runner import RegressionTestRunner

    runner = RegressionTestRunner()
    report = await runner.run_regression(
        fixture_ids=regression_request.fixture_ids,
        baseline_scores=regression_request.baseline_scores,
        k=regression_request.k,
    )
    return report.model_dump()


@router.post(
    "/eval/scan",
    summary="手动触发闭环扫描",
)
async def trigger_eval_scan(
    request: Request,
    scan_request: ScanTriggerRequest,
) -> dict:
    """手动触发 Trace-Eval-Improve 闭环扫描

    需要管理员权限。扫描失败 session 并执行完整闭环流程。

    Args:
        request: FastAPI 请求对象（用于权限校验）
        scan_request: 扫描请求参数
    """
    _require_admin(request)

    from agent.evaluation.replay.eval_scheduler import (
        EvalScheduler,
        FailureFilter,
    )
    from observability.tracing import span_cache
    from agent.core.observability.feedback import get_feedback_service

    scheduler = EvalScheduler(
        span_cache=span_cache,
        feedback_service=get_feedback_service(),
    )

    filt = FailureFilter(
        since_hours=scan_request.since_hours,
        agent_name=scan_request.agent_name,
        include_thumbs_down=scan_request.include_thumbs_down,
        max_batch_size=scan_request.max_batch_size,
    )

    failed_sessions = await scheduler.scan_failed_sessions(filt)

    processed_reports: list[str] = []
    for failed in failed_sessions:
        report_id = await scheduler.process_failed_session(failed)
        if report_id:
            processed_reports.append(report_id)

    return {
        "scanned_failed_sessions": len(failed_sessions),
        "processed_reports": processed_reports,
        "processed_count": len(processed_reports),
        "failed_sessions": [f.model_dump() for f in failed_sessions],
    }


@router.get(
    "/eval/replay-records",
    summary="查询回放记录列表",
)
async def list_replay_records(
    request: Request,
    original_session_id: str | None = None,
) -> dict:
    """查询 Trace 回放记录列表

    需要管理员权限。可按原始 session ID 过滤。

    Args:
        request: FastAPI 请求对象（用于权限校验）
        original_session_id: 按原始 session ID 过滤
    """
    _require_admin(request)

    from agent.evaluation.replay.trace_replayer import TraceReplayer
    from observability.tracing import span_cache

    replayer = TraceReplayer(span_cache=span_cache)
    records = replayer.list_replay_records(
        original_session_id=original_session_id,
    )
    return {
        "items": [
            r.model_dump() if hasattr(r, "model_dump") else r
            for r in records
        ],
        "total": len(records),
    }


# ==================== Harness 自改进闭环管理（spec 05） ====================


class GuardrailRuleCreateRequest(BaseModel):
    """规则候选创建请求（spec 05）"""

    pattern: str = Field(..., description="失败模式: injection_attack/pii_leakage/tool_misuse/hallucination/policy_violation")
    rule_type: str = Field(..., description="规则类型: regex/keyword/function/schema")
    rule_spec: dict[str, Any] = Field(..., description="规则定义")
    layer: str = Field(default="input", description="护栏层: input/tool/output")
    action: str = Field(default="block", description="命中动作: block/redact/warn")
    description: str = Field(default="", description="规则说明")
    source_trace_id: str = Field(default="", description="来源 Trace ID")
    tenant_id: str = Field(default="", description="租户ID")
    created_by: str = Field(default="admin", description="创建者")


class GuardrailRuleApproveRequest(BaseModel):
    """规则审核请求（spec 05）"""

    approved_by: str = Field(..., description="审核人")
    reason: str = Field(default="", description="审核说明")


class GuardrailRuleRejectRequest(BaseModel):
    """规则拒绝请求（spec 05）"""

    reason: str = Field(default="", description="拒绝原因")
    operator: str = Field(default="", description="操作人")


class GuardrailRuleRollbackRequest(BaseModel):
    """规则回滚请求（spec 05）"""

    target_version: int | None = Field(None, description="目标版本号，None表示回滚到上一活跃版本")
    operator: str = Field(default="admin", description="操作人")
    reason: str = Field(default="", description="回滚原因")


def _get_rule_store():
    """获取规则存储实例（spec 05）"""
    from agent.evaluation.improvement.rule_store import GuardrailRuleStore
    return GuardrailRuleStore()


def _get_rule_rollback():
    """获取规则回滚器实例（spec 05）"""
    from agent.evaluation.improvement.rule_rollback import RuleRollback
    return RuleRollback(_get_rule_store())


def _get_rule_metrics_collector():
    """获取规则效果监控器实例（spec 05）"""
    from agent.evaluation.improvement.rule_metrics import RuleMetricsCollector
    return RuleMetricsCollector()


def _get_dynamic_rule_loader():
    """获取动态规则加载器实例（spec 05）"""
    from agent.evaluation.improvement.dynamic_loader import DynamicRuleLoader
    return DynamicRuleLoader(_get_rule_store())


@router.get(
    "/guardrail/rules",
    summary="查询护栏规则列表（spec 05）",
)
async def list_guardrail_rules(
    request: Request,
    status: str | None = None,
    pattern: str | None = None,
    layer: str | None = None,
    tenant_id: str = "",
    limit: int = 50,
) -> dict:
    """查询护栏规则列表

    需要管理员权限。支持按状态、失败模式、护栏层、租户过滤。

    Args:
        request: FastAPI 请求对象
        status: 按规则状态过滤（candidate/sandbox_passed/approved/active/disabled/...）
        pattern: 按失败模式过滤
        layer: 按护栏层过滤（input/tool/output）
        tenant_id: 按租户过滤
        limit: 返回上限
    """
    _require_admin(request)

    store = _get_rule_store()
    rules = await store.list_rules(
        status=status,
        pattern=pattern,
        layer=layer,
        tenant_id=tenant_id,
        limit=limit,
    )
    return {
        "items": rules,
        "total": len(rules),
        "filters": {
            "status": status,
            "pattern": pattern,
            "layer": layer,
            "tenant_id": tenant_id,
        },
    }


@router.get(
    "/guardrail/rules/{rule_id}",
    summary="查询护栏规则详情（spec 05）",
)
async def get_guardrail_rule(
    rule_id: str,
    request: Request,
) -> dict:
    """查询护栏规则详情

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
    """
    _require_admin(request)

    store = _get_rule_store()
    rule = await store.get_rule(rule_id)
    if not rule:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND, message=f"规则 {rule_id} 不存在")
    return rule


@router.post(
    "/guardrail/rules",
    summary="创建护栏规则候选（spec 05）",
)
async def create_guardrail_rule(
    request: Request,
    create_request: GuardrailRuleCreateRequest,
) -> dict:
    """手动创建护栏规则候选

    需要管理员权限。创建后规则状态为 candidate，需经沙箱验证和人工审核后上线。

    Args:
        request: FastAPI 请求对象
        create_request: 创建请求
    """
    _require_admin(request)

    from agent.evaluation.improvement.models import (
        GuardrailRuleCandidate,
        RuleType,
        RuleStatus,
        GuardrailLayer,
    )

    try:
        rule_type = RuleType(create_request.rule_type)
    except ValueError:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.INVALID_PARAMETER, message=f"无效的规则类型: {create_request.rule_type}")

    try:
        layer = GuardrailLayer(create_request.layer)
    except ValueError:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.INVALID_PARAMETER, message=f"无效的护栏层: {create_request.layer}")

    candidate = GuardrailRuleCandidate(
        pattern=create_request.pattern,
        rule_type=rule_type,
        rule_spec=create_request.rule_spec,
        layer=layer,
        action=create_request.action,
        description=create_request.description,
        source_trace_id=create_request.source_trace_id,
        tenant_id=create_request.tenant_id,
        created_by=create_request.created_by,
        status=RuleStatus.CANDIDATE,
    )

    store = _get_rule_store()
    rule_id = await store.save_candidate(candidate)
    return {"rule_id": rule_id, "status": "candidate"}


@router.post(
    "/guardrail/rules/{rule_id}/approve",
    summary="审核通过护栏规则（spec 05）",
)
async def approve_guardrail_rule(
    rule_id: str,
    request: Request,
    approve_request: GuardrailRuleApproveRequest,
) -> dict:
    """审核通过护栏规则

    将规则状态从 sandbox_passed 变更为 approved，进而变更为 active。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
        approve_request: 审核请求
    """
    _require_admin(request)

    store = _get_rule_store()
    # 1. 审核通过：sandbox_passed -> approved
    success = await store.update_status(
        rule_id=rule_id,
        new_status="approved",
        operator=approve_request.approved_by,
        reason=approve_request.reason,
    )
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.CONFLICT, message=f"规则 {rule_id} 审核失败")

    # 2. 上线：approved -> active
    await store.update_status(
        rule_id=rule_id,
        new_status="active",
        operator=approve_request.approved_by,
        reason="审核通过后自动上线",
    )

    # 3. 刷新动态规则缓存
    try:
        loader = _get_dynamic_rule_loader()
        await loader.refresh()
    except Exception as e:
        logger.warning("刷新动态规则缓存失败（非致命）: %s", e)

    return {"rule_id": rule_id, "status": "active", "approved_by": approve_request.approved_by}


@router.post(
    "/guardrail/rules/{rule_id}/reject",
    summary="拒绝护栏规则（spec 05）",
)
async def reject_guardrail_rule(
    rule_id: str,
    request: Request,
    reject_request: GuardrailRuleRejectRequest,
) -> dict:
    """拒绝护栏规则

    将规则状态变更为 rejected。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
        reject_request: 拒绝请求
    """
    _require_admin(request)

    store = _get_rule_store()
    success = await store.update_status(
        rule_id=rule_id,
        new_status="rejected",
        operator=reject_request.operator,
        reason=reject_request.reason,
    )
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.CONFLICT, message=f"规则 {rule_id} 拒绝失败")

    return {"rule_id": rule_id, "status": "rejected", "reason": reject_request.reason}


@router.post(
    "/guardrail/rules/{rule_id}/disable",
    summary="禁用护栏规则（spec 05）",
)
async def disable_guardrail_rule(
    rule_id: str,
    request: Request,
    reject_request: GuardrailRuleRejectRequest,
) -> dict:
    """禁用护栏规则

    将规则状态从 active 变更为 disabled。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
        reject_request: 禁用请求
    """
    _require_admin(request)

    store = _get_rule_store()
    success = await store.update_status(
        rule_id=rule_id,
        new_status="disabled",
        operator=reject_request.operator,
        reason=reject_request.reason,
    )
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.CONFLICT, message=f"规则 {rule_id} 禁用失败")

    # 刷新动态规则缓存
    try:
        loader = _get_dynamic_rule_loader()
        await loader.refresh()
    except Exception as e:
        logger.warning("刷新动态规则缓存失败（非致命）: %s", e)

    return {"rule_id": rule_id, "status": "disabled", "reason": reject_request.reason}


@router.post(
    "/guardrail/rules/{rule_id}/rollback",
    summary="回滚护栏规则到指定版本（spec 05）",
)
async def rollback_guardrail_rule(
    rule_id: str,
    request: Request,
    rollback_request: GuardrailRuleRollbackRequest,
) -> dict:
    """回滚护栏规则

    按版本回滚已上线规则。target_version=None 时自动查找上一活跃版本。

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
        rollback_request: 回滚请求
    """
    _require_admin(request)

    rollback = _get_rule_rollback()
    success = await rollback.rollback(
        rule_id=rule_id,
        target_version=rollback_request.target_version,
        operator=rollback_request.operator,
        reason=rollback_request.reason,
    )
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.CONFLICT, message=f"规则 {rule_id} 回滚失败")

    # 刷新动态规则缓存
    try:
        loader = _get_dynamic_rule_loader()
        await loader.refresh()
    except Exception as e:
        logger.warning("刷新动态规则缓存失败（非致命）: %s", e)

    return {
        "rule_id": rule_id,
        "rolled_back_to": rollback_request.target_version,
        "operator": rollback_request.operator,
        "reason": rollback_request.reason,
    }


@router.get(
    "/guardrail/rules/{rule_id}/versions",
    summary="查询规则版本链（spec 05）",
)
async def list_rule_versions(
    rule_id: str,
    request: Request,
) -> dict:
    """查询规则的所有历史版本

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
    """
    _require_admin(request)

    store = _get_rule_store()
    versions = await store.get_rule_versions(rule_id)
    return {
        "rule_id": rule_id,
        "versions": [v.model_dump() if hasattr(v, "model_dump") else v for v in versions],
        "total": len(versions),
    }


@router.get(
    "/guardrail/rules/{rule_id}/metrics",
    summary="查询规则效果指标（spec 05）",
)
async def get_rule_metrics(
    rule_id: str,
    request: Request,
    window_days: int = 7,
) -> dict:
    """查询规则效果指标

    Args:
        rule_id: 规则 ID
        request: FastAPI 请求对象
        window_days: 统计窗口天数
    """
    _require_admin(request)

    collector = _get_rule_metrics_collector()
    metrics = await collector.get_metrics(rule_id, window_days=window_days)
    need_rollback, reason = await collector.check_rollback_needed(rule_id)

    return {
        "rule_id": rule_id,
        "window_days": window_days,
        "metrics": metrics.model_dump() if hasattr(metrics, "model_dump") else metrics,
        "rollback_needed": need_rollback,
        "rollback_reason": reason,
    }


@router.post(
    "/guardrail/rules/refresh-cache",
    summary="强制刷新动态规则缓存（spec 05）",
)
async def refresh_dynamic_rules_cache(request: Request) -> dict:
    """强制刷新动态规则缓存

    需要管理员权限。刷新后运行时护栏立即加载最新规则。

    Args:
        request: FastAPI 请求对象
    """
    _require_admin(request)

    loader = _get_dynamic_rule_loader()
    count = await loader.refresh()
    return {"refreshed": True, "active_rules_count": count}


@router.get(
    "/guardrail/rules/active/list",
    summary="查询当前活跃的动态规则（spec 05）",
)
async def list_active_dynamic_rules(
    request: Request,
    pattern: str | None = None,
    tenant_id: str = "",
) -> dict:
    """查询当前活跃的动态规则

    Args:
        request: FastAPI 请求对象
        pattern: 按失败模式过滤
        tenant_id: 按租户过滤
    """
    _require_admin(request)

    loader = _get_dynamic_rule_loader()
    rules = await loader.get_active_rules(pattern=pattern, tenant_id=tenant_id)
    return {
        "items": rules,
        "total": len(rules),
    }


@router.post(
    "/guardrail/consume/{trace_id}",
    summary="手动触发失败 Trace 消费（spec 05）",
)
async def consume_failure_trace(
    trace_id: str,
    request: Request,
    failure_reason: str = "",
) -> dict:
    """手动触发失败 Trace 消费

    需要管理员权限。将指定 Trace 作为失败案例消费，触发分类和规则生成流程。
    trace_id 同时作为 session_id 用于从 SpanCache 获取 spans。

    Args:
        trace_id: 失败 Trace ID（或 session ID）
        request: FastAPI 请求对象
        failure_reason: 失败原因摘要
    """
    _require_admin(request)

    from agent.evaluation.improvement.trace_consumer import FailureTraceConsumer
    from agent.evaluation.improvement.failure_pattern import FailurePatternClassifier

    consumer = FailureTraceConsumer(FailurePatternClassifier())

    # 先从 SpanCache 获取 spans
    spans = await consumer._get_session_spans(trace_id)

    result = await consumer.consume({
        "trace_id": trace_id,
        "session_id": trace_id,
        "failure_reason": failure_reason,
        "spans": spans,
    })

    if result is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.CONFLICT, message=f"消费失败 Trace {trace_id} 失败")

    return {
        "trace_id": trace_id,
        "classification": result.model_dump() if hasattr(result, "model_dump") else result,
        "route_to_human": consumer.should_route_to_human(result),
    }
