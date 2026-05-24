"""数据分析原生工具

提供自然语言数据查询、可视化建议和数据导出功能，复用 data_analysis 模块的核心能力。

工具列表：
  -------------------------------------------------------------------------
  native_data_query: 自然语言查询平台数据
    - 延迟分层: fast
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖数据库连接）

  native_data_visualize: 生成数据可视化建议
    - 延迟分层: fast
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖数据库连接）

  native_data_export: 导出数据为 CSV/JSON
    - 延迟分层: fast
    - 权限级别: read_write
    - 注册方式: 懒注册（依赖数据库连接）
  -------------------------------------------------------------------------

数据来源：复用 agent/core/data_analysis.py 的 analyze_data / nl_to_sql / suggest_visualization
"""

import csv
import io
import json
import logging
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)


async def _data_query(query: str, data_source: str = "default") -> str:
    """自然语言查询平台数据

    将自然语言转换为 SQL 查询并执行，返回结构化 JSON 结果。

    Args:
        query: 自然语言查询语句
        data_source: 数据源标识，默认为 default

    Returns:
        JSON 格式的查询结果，包含 columns 和 rows
    """
    if not query or not query.strip():
        return json.dumps({"error": "查询语句不能为空", "columns": [], "rows": []}, ensure_ascii=False)

    try:
        from agent.core.data.data_analysis import NLQueryRequest, analyze_data

        request = NLQueryRequest(
            query=query.strip(),
            data_source=data_source,
            include_visualization=False,
            include_insights=True,
        )
        report = await analyze_data(request)

        if report.data is None:
            return json.dumps({
                "query": query,
                "sql": "",
                "columns": [],
                "rows": [],
                "insights": report.insights,
            }, ensure_ascii=False)

        columns = [{"name": col.name, "display_name": col.display_name, "data_type": col.data_type} for col in report.data.columns]
        result = {
            "query": query,
            "sql": report.sections[1]["content"] if len(report.sections) > 1 else "",
            "columns": columns,
            "rows": report.data.rows,
            "total_rows": report.data.total_rows,
            "execution_time_ms": report.data.execution_time_ms,
            "insights": report.insights,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("数据查询失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"数据查询失败: {str(e)}", "columns": [], "rows": []}, ensure_ascii=False)


async def _data_visualize(query: str, chart_type: str = "") -> str:
    """生成数据可视化建议

    根据查询意图和数据特征推荐图表类型和可视化配置。

    Args:
        query: 自然语言查询语句
        chart_type: 指定图表类型（可选），支持 bar/line/pie/scatter/table/area/heatmap/funnel

    Returns:
        JSON 格式的可视化建议
    """
    if not query or not query.strip():
        return json.dumps({"error": "查询语句不能为空", "visualization": None}, ensure_ascii=False)

    try:
        from agent.core.data.data_analysis import (
            NLQueryRequest,
            analyze_data,
            ChartType,
            suggest_visualization,
            detect_query_intent,
        )

        request = NLQueryRequest(
            query=query.strip(),
            include_visualization=True,
            include_insights=False,
        )
        report = await analyze_data(request)

        if report.visualization is None:
            return json.dumps({
                "query": query,
                "visualization": None,
                "message": "无法生成可视化建议",
            }, ensure_ascii=False)

        viz = report.visualization
        result_chart_type = viz.chart_type.value

        if chart_type:
            try:
                ChartType(chart_type)
                result_chart_type = chart_type
            except ValueError:
                pass

        result = {
            "query": query,
            "visualization": {
                "chart_type": result_chart_type,
                "title": viz.title,
                "x_field": viz.x_field,
                "y_fields": viz.y_fields,
                "color_field": viz.color_field,
                "options": viz.options,
            },
            "data": {
                "columns": [{"name": col.name, "display_name": col.display_name, "data_type": col.data_type} for col in report.data.columns] if report.data else [],
                "rows": report.data.rows if report.data else [],
                "total_rows": report.data.total_rows if report.data else 0,
            } if report.data else None,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("可视化建议失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"可视化建议失败: {str(e)}", "visualization": None}, ensure_ascii=False)


async def _data_export(query: str, format: str = "json", data_source: str = "default") -> str:
    """导出数据为 CSV 或 JSON 格式

    执行自然语言查询并将结果导出为指定格式。

    Args:
        query: 自然语言查询语句
        format: 导出格式，仅允许 json 和 csv
        data_source: 数据源标识，默认为 default

    Returns:
        JSON 格式的导出结果，包含数据和格式信息
    """
    if not query or not query.strip():
        return json.dumps({"error": "查询语句不能为空", "data": None}, ensure_ascii=False)

    if format not in ("json", "csv"):
        return json.dumps({"error": f"不支持的导出格式: {format}，仅允许 json 和 csv", "data": None}, ensure_ascii=False)

    try:
        from agent.core.data.data_analysis import NLQueryRequest, analyze_data

        request = NLQueryRequest(
            query=query.strip(),
            data_source=data_source,
            include_visualization=False,
            include_insights=False,
        )
        report = await analyze_data(request)

        if report.data is None or not report.data.rows:
            return json.dumps({
                "query": query,
                "format": format,
                "data": None,
                "message": "查询结果为空，无数据可导出",
            }, ensure_ascii=False)

        if format == "csv":
            columns = [col.name for col in report.data.columns]
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for row in report.data.rows:
                writer.writerow(row)
            csv_content = output.getvalue()
            result = {
                "query": query,
                "format": "csv",
                "data": csv_content,
                "total_rows": report.data.total_rows,
                "columns": columns,
            }
        else:
            result = {
                "query": query,
                "format": "json",
                "data": report.data.rows,
                "total_rows": report.data.total_rows,
                "columns": [{"name": col.name, "display_name": col.display_name, "data_type": col.data_type} for col in report.data.columns],
            }

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("数据导出失败: query=%s, error=%s", query[:100], e)
        return json.dumps({"error": f"数据导出失败: {str(e)}", "data": None}, ensure_ascii=False)


_DATA_QUERY_META = NativeToolMeta(
    name="native_data_query",
    display_name="自然语言数据查询",
    description="使用自然语言查询平台数据，自动转换为 SQL 并执行。返回结构化 JSON 结果，包含列定义、数据行和智能洞察。",
    category="data",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "自然语言查询语句，如'本月销售额趋势'、'各部门费用对比'",
            },
            "data_source": {
                "type": "string",
                "description": "数据源标识，默认为 default",
                "default": "default",
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=30,
    requires_llm=False,
    tags=["data", "query", "sql"],
)

_DATA_VISUALIZE_META = NativeToolMeta(
    name="native_data_visualize",
    display_name="数据可视化建议",
    description="根据自然语言查询生成数据可视化建议，推荐图表类型和可视化配置。支持柱状图、折线图、饼图、散点图等。",
    category="data",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "自然语言查询语句",
            },
            "chart_type": {
                "type": "string",
                "description": "指定图表类型（可选），支持 bar/line/pie/scatter/table/area/heatmap/funnel",
                "default": "",
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=30,
    requires_llm=False,
    tags=["data", "visualization", "chart"],
)

_DATA_EXPORT_META = NativeToolMeta(
    name="native_data_export",
    display_name="数据导出",
    description="执行自然语言查询并将结果导出为 JSON 或 CSV 格式。",
    category="data",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "自然语言查询语句",
            },
            "format": {
                "type": "string",
                "description": "导出格式: json 或 csv",
                "enum": ["json", "csv"],
                "default": "json",
            },
            "data_source": {
                "type": "string",
                "description": "数据源标识，默认为 default",
                "default": "default",
            },
        },
        "required": ["query"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_WRITE,
    timeout_seconds=30,
    requires_llm=False,
    tags=["data", "export", "csv", "json"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有数据分析工具

    数据分析工具依赖数据库连接，使用懒注册模式。

    Args:
        registry: 工具注册中心实例
    """

    def _create_data_query_tool() -> FunctionTool:
        return FunctionTool(
            func=_data_query,
            name="native_data_query",
            description=_DATA_QUERY_META.description,
        )

    def _create_data_visualize_tool() -> FunctionTool:
        return FunctionTool(
            func=_data_visualize,
            name="native_data_visualize",
            description=_DATA_VISUALIZE_META.description,
        )

    def _create_data_export_tool() -> FunctionTool:
        return FunctionTool(
            func=_data_export,
            name="native_data_export",
            description=_DATA_EXPORT_META.description,
        )

    registry.register_lazy("native_data_query", _create_data_query_tool, _DATA_QUERY_META)
    registry.register_lazy("native_data_visualize", _create_data_visualize_tool, _DATA_VISUALIZE_META)
    registry.register_lazy("native_data_export", _create_data_export_tool, _DATA_EXPORT_META)

    logger.debug("数据分析工具注册完成: native_data_query, native_data_visualize, native_data_export(均懒注册)")
