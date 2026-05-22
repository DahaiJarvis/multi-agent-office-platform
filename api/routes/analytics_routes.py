"""数据分析路由

提供数据分析查询和业务指标分析两类能力：
  - 数据分析：自然语言查询、意图识别、图表类型
  - 业务指标：概览、意图分布、Agent 性能、工具使用、安全拦截、业务趋势
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from agent.core.data_analysis import (
    analyze_data,
    NLQueryRequest,
    AnalysisReport,
    detect_query_intent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["数据分析"])


@router.post("/query", response_model=AnalysisReport, summary="执行数据分析查询")
async def api_nl_query(request: NLQueryRequest) -> AnalysisReport:
    """自然语言数据查询

    将自然语言查询转换为数据查询，返回分析报告和可视化建议。
    """
    return await analyze_data(request)


@router.post("/intent", summary="识别查询意图")
async def api_detect_intent(query: str) -> dict:
    """检测查询意图"""
    intent = detect_query_intent(query)
    return {"query": query, "intent": intent.value}


@router.get("/chart-types", summary="获取支持的图表类型")
async def list_chart_types() -> dict:
    """列出支持的图表类型"""
    return {
        "chart_types": [
            {"id": "bar", "name": "柱状图", "description": "适合对比和排名场景"},
            {"id": "line", "name": "折线图", "description": "适合趋势分析场景"},
            {"id": "pie", "name": "饼图", "description": "适合占比分布场景"},
            {"id": "scatter", "name": "散点图", "description": "适合相关性分析场景"},
            {"id": "table", "name": "表格", "description": "适合明细数据展示"},
            {"id": "area", "name": "面积图", "description": "适合累计趋势场景"},
            {"id": "heatmap", "name": "热力图", "description": "适合密度分布场景"},
            {"id": "funnel", "name": "漏斗图", "description": "适合转化分析场景"},
        ]
    }


# ==================== 业务指标分析端点 ====================


@router.get("/overview", summary="获取业务概览")
async def api_business_overview(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取业务概览

    返回今日任务总数、成功率、平均耗时、活跃用户数等关键指标。
    数据来源：Redis 聚合缓存。
    """
    from observability.business_analytics import get_business_overview

    overview = await get_business_overview(date)
    return overview.model_dump()


@router.get("/intent-distribution", summary="获取意图分布统计")
async def api_intent_distribution(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取意图分布统计

    返回各意图的调用次数和置信度分布（high/medium/low）。
    """
    from observability.business_analytics import get_intent_distribution

    result = await get_intent_distribution(date)
    return result.model_dump()


@router.get("/agent-performance", summary="获取Agent性能排行")
async def api_agent_performance(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取 Agent 性能排行

    返回各 Agent 的调用次数、成功率、错误数等性能指标。
    """
    from observability.business_analytics import get_agent_performance

    result = await get_agent_performance(date)
    return result.model_dump()


@router.get("/tool-usage", summary="获取工具使用排行")
async def api_tool_usage(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取工具使用排行

    返回各工具的调用次数排行。
    """
    from observability.business_analytics import get_tool_usage

    result = await get_tool_usage(date)
    return result.model_dump()


@router.get("/guardrail-stats", summary="获取安全拦截统计")
async def api_guardrail_stats(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取安全拦截统计

    返回安全拦截总数、按检查类型和动作类型的分布。
    """
    from observability.business_analytics import get_guardrail_stats

    result = await get_guardrail_stats(date)
    return result.model_dump()


@router.get("/trend", summary="获取业务趋势")
async def api_business_trend(
    period: str = Query(default="daily", description="聚合周期(daily)"),
    days: int = Query(default=7, ge=1, le=30, description="查询天数"),
) -> dict:
    """获取业务趋势

    返回最近 N 天的业务指标趋势数据，用于绘制趋势图。
    """
    from observability.business_analytics import get_business_trend

    result = await get_business_trend(period=period, days=days)
    return result.model_dump()


@router.get("/skill-usage", summary="获取技能使用统计")
async def api_skill_usage(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取技能使用统计

    返回各技能的调用次数和按 Agent 的使用分布。
    """
    from observability.business_analytics import get_skill_usage_stats

    result = await get_skill_usage_stats(date)
    return result.model_dump()


@router.get("/workflow-execution", summary="获取工作流执行统计")
async def api_workflow_execution(
    date: Optional[str] = Query(default=None, description="日期(YYYY-MM-DD)，默认今日"),
) -> dict:
    """获取工作流执行统计

    返回各工作流的执行次数、成功率和错误数。
    """
    from observability.business_analytics import get_workflow_execution_stats

    result = await get_workflow_execution_stats(date)
    return result.model_dump()
