"""业务分析服务

从 Redis 聚合缓存读取业务指标数据，提供业务概览、意图分布、
Agent 性能、工具使用、安全拦截、业务趋势等分析能力。

数据来源：
  - Redis 聚合缓存（由 scheduler 定时从 Prometheus 聚合写入）
  - Prometheus 直接查询（缓存未命中时的降级方案）

Redis Key 设计：
  - analytics:daily:{date}:overview -> JSON（每日概览数据）
  - analytics:daily:{date}:intent_dist -> JSON（每日意图分布）
  - analytics:daily:{date}:agent_perf -> JSON（每日 Agent 性能）
  - analytics:daily:{date}:tool_usage -> JSON（每日工具使用）
  - analytics:daily:{date}:guardrail -> JSON（每日安全拦截）
  TTL: 30 天
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Redis Key 前缀
_DAILY_KEY_PREFIX = "analytics:daily:"
_CACHE_TTL = 86400 * 30

_redis_client: Any = None


async def _get_redis() -> Any:
    """获取 Redis 客户端"""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        from agent.core.redis_manager import get_redis_client
        _redis_client = await get_redis_client()
        return _redis_client
    except Exception as e:
        logger.debug("Redis 获取失败: %s", e)
        return None


def _today_key(suffix: str) -> str:
    """生成今日 Redis Key"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    return f"{_DAILY_KEY_PREFIX}{date_str}:{suffix}"


def _date_key(date: str, suffix: str) -> str:
    """生成指定日期的 Redis Key"""
    return f"{_DAILY_KEY_PREFIX}{date}:{suffix}"


async def _read_cache(key: str) -> dict[str, Any] | None:
    """从 Redis 读取聚合缓存"""
    redis = await _get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("读取 Redis 缓存失败: key=%s error=%s", key, e)
        return None


async def _write_cache(key: str, data: dict[str, Any]) -> None:
    """写入 Redis 聚合缓存"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(data, ensure_ascii=False), ex=_CACHE_TTL)
    except Exception as e:
        logger.debug("写入 Redis 缓存失败: key=%s error=%s", key, e)


# ==================== 数据模型 ====================


class BusinessOverview(BaseModel):
    """业务概览"""

    date: str = Field(default="", description="日期")
    total_tasks: int = Field(default=0, description="今日任务总数")
    success_rate: float = Field(default=0.0, description="任务成功率(%)")
    avg_duration_ms: float = Field(default=0.0, description="平均耗时(ms)")
    active_users: int = Field(default=0, description="活跃用户数")
    total_errors: int = Field(default=0, description="错误总数")
    clarification_count: int = Field(default=0, description="澄清请求数")


class IntentDistribution(BaseModel):
    """意图分布"""

    date: str = Field(default="", description="日期")
    intents: list[dict[str, Any]] = Field(default_factory=list, description="意图分布列表")
    confidence_levels: dict[str, int] = Field(default_factory=dict, description="置信度分布")


class AgentPerformance(BaseModel):
    """Agent 性能"""

    date: str = Field(default="", description="日期")
    agents: list[dict[str, Any]] = Field(default_factory=list, description="Agent 性能列表")


class ToolUsageStats(BaseModel):
    """工具使用统计"""

    date: str = Field(default="", description="日期")
    tools: list[dict[str, Any]] = Field(default_factory=list, description="工具使用列表")


class GuardrailStats(BaseModel):
    """安全拦截统计"""

    date: str = Field(default="", description="日期")
    total_blocks: int = Field(default=0, description="拦截总数")
    by_check_type: dict[str, int] = Field(default_factory=dict, description="按检查类型分布")
    by_action: dict[str, int] = Field(default_factory=dict, description="按动作类型分布")


class BusinessTrend(BaseModel):
    """业务趋势"""

    period: str = Field(default="daily", description="聚合周期")
    data_points: list[dict[str, Any]] = Field(default_factory=list, description="趋势数据点")


# ==================== 分析查询 ====================


async def get_business_overview(date: str | None = None) -> BusinessOverview:
    """获取业务概览

    从 Redis 聚合缓存读取今日概览数据，缓存未命中时返回空概览。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今日

    Returns:
        BusinessOverview
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = _date_key(target_date, "overview")

    cached = await _read_cache(cache_key)
    if cached:
        return BusinessOverview(**cached)

    return BusinessOverview(date=target_date)


async def get_intent_distribution(date: str | None = None) -> IntentDistribution:
    """获取意图分布统计

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今日

    Returns:
        IntentDistribution
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = _date_key(target_date, "intent_dist")

    cached = await _read_cache(cache_key)
    if cached:
        return IntentDistribution(**cached)

    return IntentDistribution(date=target_date)


async def get_agent_performance(date: str | None = None) -> AgentPerformance:
    """获取 Agent 性能排行

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今日

    Returns:
        AgentPerformance
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = _date_key(target_date, "agent_perf")

    cached = await _read_cache(cache_key)
    if cached:
        return AgentPerformance(**cached)

    return AgentPerformance(date=target_date)


async def get_tool_usage(date: str | None = None) -> ToolUsageStats:
    """获取工具使用排行

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今日

    Returns:
        ToolUsageStats
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = _date_key(target_date, "tool_usage")

    cached = await _read_cache(cache_key)
    if cached:
        return ToolUsageStats(**cached)

    return ToolUsageStats(date=target_date)


async def get_guardrail_stats(date: str | None = None) -> GuardrailStats:
    """获取安全拦截统计

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今日

    Returns:
        GuardrailStats
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    cache_key = _date_key(target_date, "guardrail")

    cached = await _read_cache(cache_key)
    if cached:
        return GuardrailStats(**cached)

    return GuardrailStats(date=target_date)


async def get_business_trend(
    period: str = "daily",
    days: int = 7,
) -> BusinessTrend:
    """获取业务趋势

    从 Redis 读取最近 N 天的概览数据，聚合为趋势图数据。

    Args:
        period: 聚合周期（daily、hourly）
        days: 查询天数

    Returns:
        BusinessTrend
    """
    data_points: list[dict[str, Any]] = []

    for i in range(days - 1, -1, -1):
        target_date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        cache_key = _date_key(target_date, "overview")

        cached = await _read_cache(cache_key)
        if cached:
            data_points.append({
                "date": target_date,
                **cached,
            })
        else:
            data_points.append({
                "date": target_date,
                "total_tasks": 0,
                "success_rate": 0,
                "avg_duration_ms": 0,
                "active_users": 0,
            })

    return BusinessTrend(period=period, data_points=data_points)


# ==================== 聚合写入（由 scheduler 调用） ====================


async def aggregate_daily_metrics(target_date: str | None = None) -> None:
    """聚合每日业务指标到 Redis 缓存

    从 Prometheus 读取指标数据，聚合后写入 Redis。
    由 scheduler 定时任务每小时调用一次。

    Args:
        target_date: 目标日期，默认今日
    """
    date_str = target_date or datetime.now().strftime("%Y-%m-%d")

    try:
        overview = await _aggregate_overview(date_str)
        if overview:
            await _write_cache(_date_key(date_str, "overview"), overview)

        intent_dist = await _aggregate_intent_distribution(date_str)
        if intent_dist:
            await _write_cache(_date_key(date_str, "intent_dist"), intent_dist)

        agent_perf = await _aggregate_agent_performance(date_str)
        if agent_perf:
            await _write_cache(_date_key(date_str, "agent_perf"), agent_perf)

        tool_usage = await _aggregate_tool_usage(date_str)
        if tool_usage:
            await _write_cache(_date_key(date_str, "tool_usage"), tool_usage)

        guardrail = await _aggregate_guardrail_stats(date_str)
        if guardrail:
            await _write_cache(_date_key(date_str, "guardrail"), guardrail)

        logger.info("每日业务指标聚合完成: date=%s", date_str)
    except Exception as e:
        logger.error("每日业务指标聚合失败: date=%s error=%s", date_str, e)


async def _aggregate_overview(date: str) -> dict[str, Any] | None:
    """聚合业务概览数据"""
    try:
        from prometheus_client import REGISTRY
        from prometheus_client.metrics_core import CounterMetricFamily

        total_tasks = 0
        success_tasks = 0
        error_tasks = 0
        total_duration_ms = 0.0
        clarification_count = 0

        for metric in REGISTRY.collect():
            if metric.name == "business_task_total":
                for sample in metric.samples:
                    count = int(sample.value)
                    total_tasks += count
                    if sample.labels.get("status") == "success":
                        success_tasks += count
                    elif sample.labels.get("status") == "error":
                        error_tasks += count
            elif metric.name == "business_clarification_total":
                for sample in metric.samples:
                    clarification_count += int(sample.value)

        success_rate = (success_tasks / total_tasks * 100) if total_tasks > 0 else 0

        return {
            "date": date,
            "total_tasks": total_tasks,
            "success_rate": round(success_rate, 2),
            "avg_duration_ms": 0,
            "active_users": 0,
            "total_errors": error_tasks,
            "clarification_count": clarification_count,
        }
    except Exception as e:
        logger.debug("聚合概览数据失败: %s", e)
        return None


async def _aggregate_intent_distribution(date: str) -> dict[str, Any] | None:
    """聚合意图分布数据"""
    try:
        from prometheus_client import REGISTRY

        intent_counts: dict[str, int] = {}
        confidence_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

        for metric in REGISTRY.collect():
            if metric.name == "business_intent_distribution":
                for sample in metric.samples:
                    intent = sample.labels.get("intent", "unknown")
                    level = sample.labels.get("confidence_level", "medium")
                    count = int(sample.value)
                    intent_counts[intent] = intent_counts.get(intent, 0) + count
                    if level in confidence_counts:
                        confidence_counts[level] += count

        intents = [
            {"intent": k, "count": v}
            for k, v in sorted(intent_counts.items(), key=lambda x: -x[1])
        ]

        return {
            "date": date,
            "intents": intents,
            "confidence_levels": confidence_counts,
        }
    except Exception as e:
        logger.debug("聚合意图分布数据失败: %s", e)
        return None


async def _aggregate_agent_performance(date: str) -> dict[str, Any] | None:
    """聚合 Agent 性能数据"""
    try:
        from prometheus_client import REGISTRY

        agent_data: dict[str, dict[str, Any]] = {}

        for metric in REGISTRY.collect():
            if metric.name == "business_task_total":
                for sample in metric.samples:
                    agent = sample.labels.get("agent", "unknown")
                    status = sample.labels.get("status", "unknown")
                    count = int(sample.value)
                    if agent not in agent_data:
                        agent_data[agent] = {"agent": agent, "total": 0, "success": 0, "error": 0}
                    agent_data[agent]["total"] += count
                    if status == "success":
                        agent_data[agent]["success"] += count
                    elif status == "error":
                        agent_data[agent]["error"] += count

        agents = []
        for data in agent_data.values():
            total = data["total"]
            success = data["success"]
            data["success_rate"] = round((success / total * 100) if total > 0 else 0, 2)
            agents.append(data)

        agents.sort(key=lambda x: -x.get("total", 0))

        return {
            "date": date,
            "agents": agents,
        }
    except Exception as e:
        logger.debug("聚合 Agent 性能数据失败: %s", e)
        return None


async def _aggregate_tool_usage(date: str) -> dict[str, Any] | None:
    """聚合工具使用数据"""
    try:
        from prometheus_client import REGISTRY

        tool_counts: dict[str, int] = {}

        for metric in REGISTRY.collect():
            if metric.name == "business_tool_usage_total":
                for sample in metric.samples:
                    tool_name = sample.labels.get("tool_name", "unknown")
                    count = int(sample.value)
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + count

        tools = [
            {"tool_name": k, "count": v}
            for k, v in sorted(tool_counts.items(), key=lambda x: -x[1])
        ]

        return {
            "date": date,
            "tools": tools,
        }
    except Exception as e:
        logger.debug("聚合工具使用数据失败: %s", e)
        return None


async def _aggregate_guardrail_stats(date: str) -> dict[str, Any] | None:
    """聚合安全拦截数据"""
    try:
        from prometheus_client import REGISTRY

        total_blocks = 0
        by_check_type: dict[str, int] = {}
        by_action: dict[str, int] = {}

        for metric in REGISTRY.collect():
            if metric.name == "business_guardrail_block_total":
                for sample in metric.samples:
                    check_type = sample.labels.get("check_type", "unknown")
                    action = sample.labels.get("action", "unknown")
                    count = int(sample.value)
                    total_blocks += count
                    by_check_type[check_type] = by_check_type.get(check_type, 0) + count
                    by_action[action] = by_action.get(action, 0) + count

        return {
            "date": date,
            "total_blocks": total_blocks,
            "by_check_type": by_check_type,
            "by_action": by_action,
        }
    except Exception as e:
        logger.debug("聚合安全拦截数据失败: %s", e)
        return None
