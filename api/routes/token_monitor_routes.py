"""Token 消耗监控路由

提供实时 Token 消耗监控 API，支持多维度查询和告警。
"""

import logging
import time

from fastapi import APIRouter, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/token-monitor", tags=["Token监控"])


@router.get("/dashboard", summary="Token 消耗仪表盘")
async def token_dashboard(
    request: Request,
    user_id: str = Query(default="", description="用户ID"),
) -> dict:
    """获取 Token 消耗仪表盘数据

    返回全局和用户维度的 Token 消耗概览。
    """
    from agent.core.token_budget import TokenBudgetManager, BudgetConfig

    manager = TokenBudgetManager()

    result: dict = {
        "global": {},
        "user": {},
        "agents": {},
        "timestamp": time.time(),
    }

    # 全局日用量
    redis = await manager._get_redis()
    if redis:
        today = time.strftime("%Y-%m-%d")
        try:
            global_data = await redis.hgetall(f"token_usage:global:{today}")
            if global_data:
                result["global"] = {
                    "total_tokens": int(global_data.get("total_tokens", 0)),
                    "cost": round(float(global_data.get("cost", 0)), 4),
                }
        except Exception as e:
            logger.error("获取全局用量失败: %s", e)

    # 用户日用量
    if user_id:
        try:
            user_usage = await manager.get_user_daily_usage(user_id)
            result["user"] = user_usage
        except Exception as e:
            logger.error("获取用户用量失败: %s", e)

    # Agent 日用量
    if redis:
        today = time.strftime("%Y-%m-%d")
        try:
            agent_names = [
                "Supervisor", "ApprovalAgent", "EmailAgent",
                "CalendarAgent", "CRMAgent", "HRAgent",
                "FinanceAgent", "KnowledgeAgent", "Reviewer",
            ]
            agent_stats: dict = {}
            for name in agent_names:
                agent_data = await redis.hgetall(f"token_usage:agent:{name}:{today}")
                if agent_data:
                    agent_stats[name] = {
                        "total_tokens": int(agent_data.get("total_tokens", 0)),
                        "cost": round(float(agent_data.get("cost", 0)), 4),
                        "call_count": int(agent_data.get("call_count", 0)),
                    }
            result["agents"] = agent_stats
        except Exception as e:
            logger.error("获取 Agent 用量失败: %s", e)

    return result


@router.get("/budget-check", summary="预算检查")
async def budget_check(
    request: Request,
    user_id: str = Query(default="", description="用户ID"),
    session_id: str = Query(default="", description="会话ID"),
    tenant_id: str = Query(default="", description="租户ID"),
) -> dict:
    """检查多维度预算是否充足

    返回用户、会话、全局、租户维度的预算使用情况和建议的模型级别。
    """
    from agent.core.token_budget import TokenBudgetManager

    manager = TokenBudgetManager()
    result = await manager.check_budget(user_id, session_id, tenant_id)
    return result


@router.get("/mcp-quality", summary="MCP 服务质量监控")
async def mcp_quality_monitor() -> dict:
    """获取 MCP 服务质量指标

    返回各 MCP 服务的成功率、延迟、错误率等指标。
    """
    try:
        from agent.core.mcp_tracing import get_mcp_tracer
        tracer = get_mcp_tracer()
        return {
            "services": {name: metrics.model_dump() for name, metrics in tracer.get_all_quality_metrics().items()},
            "timestamp": time.time(),
        }
    except Exception as e:
        logger.error("获取 MCP 质量指标失败: %s", e)
        return {"services": {}, "error": str(e)}


@router.get("/circuit-breakers", summary="熔断器状态")
async def circuit_breaker_status() -> dict:
    """获取所有熔断器状态"""
    try:
        from agent.core.circuit_breaker import list_circuit_breakers
        breakers = list_circuit_breakers()
        return {
            "breakers": {name: stats.model_dump() for name, stats in breakers.items()},
            "timestamp": time.time(),
        }
    except Exception as e:
        logger.error("获取熔断器状态失败: %s", e)
        return {"breakers": {}, "error": str(e)}


@router.get("/mcp-validation", summary="MCP 响应校验统计")
async def mcp_validation_stats() -> dict:
    """获取 MCP 响应校验统计"""
    try:
        from agent.core.mcp_validator import get_validation_stats
        return {
            "stats": get_validation_stats(),
            "timestamp": time.time(),
        }
    except Exception as e:
        logger.error("获取校验统计失败: %s", e)
        return {"stats": {}, "error": str(e)}


@router.get("/semantic-cache", summary="语义缓存统计")
async def semantic_cache_stats() -> dict:
    """获取语义缓存统计"""
    try:
        from agent.core.performance.semantic_cache import get_semantic_cache
        cache = get_semantic_cache()
        return {
            "stats": cache.stats(),
            "timestamp": time.time(),
        }
    except Exception as e:
        logger.error("获取语义缓存统计失败: %s", e)
        return {"stats": {}, "error": str(e)}
