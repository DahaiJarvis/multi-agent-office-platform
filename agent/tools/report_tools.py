"""报告生成原生工具

提供结构化报告生成和报告导出功能。

工具列表：
  -------------------------------------------------------------------------
  native_report_generate: 生成结构化报告
    - 延迟分层: slow
    - 权限级别: read_write
    - 注册方式: 懒注册（依赖 LLM + data_analysis）

  native_report_export: 导出报告为指定格式
    - 延迟分层: instant
    - 权限级别: read_write
    - 注册方式: 立即注册
  -------------------------------------------------------------------------
"""

import json
import logging
import re
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)


async def _report_generate(topic: str, data_query: str = "", report_type: str = "analysis") -> str:
    """生成结构化报告

    使用 LLM 生成结构化分析报告，可结合数据查询结果。

    Args:
        topic: 报告主题
        data_query: 数据查询语句（可选），用于获取报告数据支撑
        report_type: 报告类型，analysis(分析报告) / summary(总结报告) / comparison(对比报告)

    Returns:
        JSON 格式的报告内容（Markdown 格式）
    """
    if not topic or not topic.strip():
        return json.dumps({"error": "报告主题不能为空", "report": ""}, ensure_ascii=False)

    try:
        data_context = ""
        if data_query and data_query.strip():
            try:
                from agent.core.data.data_analysis import NLQueryRequest, analyze_data

                request = NLQueryRequest(
                    query=data_query.strip(),
                    include_visualization=True,
                    include_insights=True,
                )
                report_data = await analyze_data(request)

                data_context = "\n\n## 数据支撑\n"
                if report_data.data and report_data.data.rows:
                    data_context += f"- 查询结果共 {report_data.data.total_rows} 条记录\n"
                    for insight in report_data.insights[:3]:
                        data_context += f"- {insight}\n"
                    if report_data.visualization:
                        data_context += f"- 建议图表类型: {report_data.visualization.chart_type.value}\n"
                else:
                    data_context += "- 数据查询未返回结果\n"
            except Exception as e:
                logger.warning("报告数据查询失败: %s", e)
                data_context = "\n\n## 数据支撑\n- 数据查询失败，报告基于通用知识生成\n"

        from agent.core.model.model_client import get_lightweight_client
        from autogen_core.models import UserMessage

        client = get_lightweight_client()

        type_instructions = {
            "analysis": (
                "请生成一份详细的分析报告，包含以下结构：\n"
                "1. 概述 - 简要说明分析背景和目的\n"
                "2. 现状分析 - 分析当前情况\n"
                "3. 关键发现 - 列出主要发现和洞察\n"
                "4. 建议与措施 - 给出具体建议\n"
                "5. 结论 - 总结要点"
            ),
            "summary": (
                "请生成一份总结报告，包含以下结构：\n"
                "1. 核心要点 - 3-5 个关键要点\n"
                "2. 详细说明 - 每个要点的展开说明\n"
                "3. 结论与展望"
            ),
            "comparison": (
                "请生成一份对比分析报告，包含以下结构：\n"
                "1. 对比维度 - 列出对比的维度\n"
                "2. 对比分析 - 逐维度对比\n"
                "3. 优劣分析 - 各方优劣势\n"
                "4. 结论与建议"
            ),
        }

        instruction = type_instructions.get(report_type, type_instructions["analysis"])

        prompt = (
            f"{instruction}\n\n"
            f"报告主题: {topic.strip()}\n"
            f"{data_context}\n\n"
            "请使用 Markdown 格式输出报告内容。"
        )

        response = await client.create(
            messages=[UserMessage(source="user", content=prompt)],
            extra_create_args={"temperature": 0.5, "max_tokens": 3000},
        )

        report_content = response.content if isinstance(response.content, str) else str(response.content)

        result = {
            "topic": topic.strip(),
            "report_type": report_type,
            "report": report_content.strip(),
            "has_data": bool(data_context),
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("报告生成失败: topic=%s, error=%s", topic[:100], e)
        return json.dumps({"error": f"报告生成失败: {str(e)}", "report": ""}, ensure_ascii=False)


async def _report_export(report_content: str, title: str = "报告", format: str = "markdown") -> str:
    """导出报告为指定格式

    将 Markdown 格式的报告内容导出为指定格式。

    Args:
        report_content: 报告内容（Markdown 格式）
        title: 报告标题
        format: 导出格式，markdown / html / pdf

    Returns:
        JSON 格式的导出结果
    """
    if not report_content or not report_content.strip():
        return json.dumps({"error": "报告内容不能为空", "exported": ""}, ensure_ascii=False)

    if format not in ("markdown", "html", "pdf"):
        return json.dumps({"error": f"不支持的导出格式: {format}，仅允许 markdown、html、pdf", "exported": ""}, ensure_ascii=False)

    try:
        if format == "markdown":
            result = {
                "title": title,
                "format": "markdown",
                "exported": report_content.strip(),
                "content_length": len(report_content.strip()),
            }
        elif format == "html":
            from agent.tools.text_tools import _markdown_to_html

            html_body = _markdown_to_html(report_content.strip())
            html_content = (
                "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
                f"<meta charset=\"UTF-8\">\n<title>{title}</title>\n"
                "<style>\nbody { font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }\n"
                "h1, h2, h3 { color: #333; }\ncode { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }\n"
                "pre { background: #f4f4f4; padding: 12px; border-radius: 6px; overflow-x: auto; }\n"
                "</style>\n</head>\n<body>\n"
                f"{html_body}\n</body>\n</html>"
            )
            result = {
                "title": title,
                "format": "html",
                "exported": html_content,
                "content_length": len(html_content),
            }
        elif format == "pdf":
            result = {
                "title": title,
                "format": "pdf",
                "exported": report_content.strip(),
                "message": "PDF 导出需要前端渲染，已提供 Markdown 原文供前端转换",
                "content_length": len(report_content.strip()),
            }

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("报告导出失败: error=%s", e)
        return json.dumps({"error": f"报告导出失败: {str(e)}", "exported": ""}, ensure_ascii=False)


_REPORT_GENERATE_META = NativeToolMeta(
    name="native_report_generate",
    display_name="报告生成",
    description="使用 AI 生成结构化分析报告，可结合数据查询结果。支持分析报告、总结报告和对比报告三种类型。",
    category="report",
    parameters={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "报告主题",
            },
            "data_query": {
                "type": "string",
                "description": "数据查询语句（可选），用于获取报告数据支撑",
                "default": "",
            },
            "report_type": {
                "type": "string",
                "description": "报告类型: analysis(分析报告) / summary(总结报告) / comparison(对比报告)",
                "enum": ["analysis", "summary", "comparison"],
                "default": "analysis",
            },
        },
        "required": ["topic"],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_WRITE,
    timeout_seconds=90,
    requires_llm=True,
    tags=["report", "generate", "llm"],
)

_REPORT_EXPORT_META = NativeToolMeta(
    name="native_report_export",
    display_name="报告导出",
    description="将 Markdown 格式的报告内容导出为指定格式（Markdown/HTML/PDF）。",
    category="report",
    parameters={
        "type": "object",
        "properties": {
            "report_content": {
                "type": "string",
                "description": "报告内容（Markdown 格式）",
            },
            "title": {
                "type": "string",
                "description": "报告标题",
                "default": "报告",
            },
            "format": {
                "type": "string",
                "description": "导出格式: markdown / html / pdf",
                "enum": ["markdown", "html", "pdf"],
                "default": "markdown",
            },
        },
        "required": ["report_content"],
    },
    latency_tier=LatencyTier.INSTANT,
    permission_level=PermissionLevel.READ_WRITE,
    timeout_seconds=10,
    requires_llm=False,
    tags=["report", "export"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有报告生成工具

    native_report_generate 依赖 LLM 和 data_analysis，使用懒注册。
    native_report_export 无外部依赖，使用立即注册。

    Args:
        registry: 工具注册中心实例
    """

    def _create_report_generate_tool() -> FunctionTool:
        return FunctionTool(
            func=_report_generate,
            name="native_report_generate",
            description=_REPORT_GENERATE_META.description,
        )

    report_export_tool = FunctionTool(
        func=_report_export,
        name="native_report_export",
        description=_REPORT_EXPORT_META.description,
    )

    registry.register_lazy("native_report_generate", _create_report_generate_tool, _REPORT_GENERATE_META)
    registry.register(report_export_tool, _REPORT_EXPORT_META)

    logger.debug("报告生成工具注册完成: native_report_generate(懒注册), native_report_export(立即注册)")
