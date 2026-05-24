"""多模态处理原生工具

提供图像分析和图片文字识别功能，复用 multimodal 模块的核心能力。

工具列表：
  -------------------------------------------------------------------------
  native_image_analyze: 分析图片内容
    - 延迟分层: slow
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖多模态模型客户端）

  native_image_ocr: 图片文字识别
    - 延迟分层: fast
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖多模态模型客户端）
  -------------------------------------------------------------------------

数据来源：复用 agent/core/multimodal.py 的 analyze_image
"""

import json
import logging
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)


async def _image_analyze(image_url: str, question: str = "") -> str:
    """分析图片内容

    使用多模态 LLM 分析图片内容，可针对图片提出问题。

    Args:
        image_url: 图片 URL 地址
        question: 关于图片的问题（可选），为空时返回图片描述

    Returns:
        JSON 格式的分析结果
    """
    if not image_url or not image_url.strip():
        return json.dumps({"error": "图片 URL 不能为空", "description": ""}, ensure_ascii=False)

    try:
        from agent.core.common.multimodal import analyze_image

        prompt = question.strip() if question and question.strip() else "请详细描述这张图片的内容"
        result = await analyze_image(
            image_source=image_url.strip(),
            prompt=prompt,
            is_base64=False,
        )

        analysis = {
            "image_url": image_url.strip(),
            "question": question.strip() if question else "图片描述",
            "description": result.description,
            "objects": result.objects,
            "confidence": result.confidence,
        }

        if result.text_content:
            analysis["text_content"] = result.text_content

        if result.chart_data:
            analysis["chart_data"] = result.chart_data

        return json.dumps(analysis, ensure_ascii=False)
    except Exception as e:
        logger.error("图片分析失败: image_url=%s, error=%s", image_url[:100], e)
        return json.dumps({"error": f"图片分析失败: {str(e)}", "description": ""}, ensure_ascii=False)


async def _image_ocr(image_url: str, language: str = "zh") -> str:
    """图片文字识别

    识别图片中的文字内容。

    Args:
        image_url: 图片 URL 地址
        language: 文字语言，zh(中文) / en(英文) / auto(自动检测)

    Returns:
        JSON 格式的识别结果
    """
    if not image_url or not image_url.strip():
        return json.dumps({"error": "图片 URL 不能为空", "text": ""}, ensure_ascii=False)

    try:
        from agent.core.common.multimodal import analyze_image

        language_instructions = {
            "zh": "请识别并提取图片中的所有中文文字内容，按原文顺序输出",
            "en": "请识别并提取图片中的所有英文文字内容，按原文顺序输出",
            "auto": "请识别并提取图片中的所有文字内容，按原文顺序输出",
        }

        prompt = language_instructions.get(language, language_instructions["auto"])

        result = await analyze_image(
            image_source=image_url.strip(),
            prompt=prompt,
            is_base64=False,
        )

        text_content = result.text_content or result.description

        ocr_result = {
            "image_url": image_url.strip(),
            "language": language,
            "text": text_content,
            "confidence": result.confidence,
        }

        return json.dumps(ocr_result, ensure_ascii=False)
    except Exception as e:
        logger.error("图片 OCR 失败: image_url=%s, error=%s", image_url[:100], e)
        return json.dumps({"error": f"图片 OCR 失败: {str(e)}", "text": ""}, ensure_ascii=False)


_IMAGE_ANALYZE_META = NativeToolMeta(
    name="native_image_analyze",
    display_name="图片分析",
    description="使用 AI 分析图片内容，可针对图片提出问题。不提供问题时返回图片描述。",
    category="multimodal",
    parameters={
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "图片 URL 地址",
            },
            "question": {
                "type": "string",
                "description": "关于图片的问题（可选），为空时返回图片描述",
                "default": "",
            },
        },
        "required": ["image_url"],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=60,
    requires_llm=True,
    tags=["image", "analyze", "multimodal", "llm"],
)

_IMAGE_OCR_META = NativeToolMeta(
    name="native_image_ocr",
    display_name="图片文字识别",
    description="识别图片中的文字内容，支持中文、英文和自动检测。",
    category="multimodal",
    parameters={
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "图片 URL 地址",
            },
            "language": {
                "type": "string",
                "description": "文字语言: zh(中文) / en(英文) / auto(自动检测)",
                "enum": ["zh", "en", "auto"],
                "default": "zh",
            },
        },
        "required": ["image_url"],
    },
    latency_tier=LatencyTier.FAST,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=30,
    requires_llm=True,
    tags=["image", "ocr", "multimodal"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有多模态处理工具

    多模态工具依赖多模态模型客户端，使用懒注册模式。

    Args:
        registry: 工具注册中心实例
    """

    def _create_image_analyze_tool() -> FunctionTool:
        return FunctionTool(
            func=_image_analyze,
            name="native_image_analyze",
            description=_IMAGE_ANALYZE_META.description,
        )

    def _create_image_ocr_tool() -> FunctionTool:
        return FunctionTool(
            func=_image_ocr,
            name="native_image_ocr",
            description=_IMAGE_OCR_META.description,
        )

    registry.register_lazy("native_image_analyze", _create_image_analyze_tool, _IMAGE_ANALYZE_META)
    registry.register_lazy("native_image_ocr", _create_image_ocr_tool, _IMAGE_OCR_META)

    logger.debug("多模态处理工具注册完成: native_image_analyze, native_image_ocr(均懒注册)")
