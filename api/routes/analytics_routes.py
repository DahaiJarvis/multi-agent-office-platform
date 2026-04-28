"""数据分析路由"""

import logging

from fastapi import APIRouter

from agent.core.data_analysis import (
    analyze_data,
    NLQueryRequest,
    AnalysisReport,
    detect_query_intent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["数据分析"])


@router.post("/query", response_model=AnalysisReport)
async def api_nl_query(request: NLQueryRequest) -> AnalysisReport:
    """自然语言数据查询

    将自然语言查询转换为数据查询，返回分析报告和可视化建议。
    """
    return await analyze_data(request)


@router.post("/intent")
async def api_detect_intent(query: str) -> dict:
    """检测查询意图"""
    intent = detect_query_intent(query)
    return {"query": query, "intent": intent.value}


@router.get("/chart-types")
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
