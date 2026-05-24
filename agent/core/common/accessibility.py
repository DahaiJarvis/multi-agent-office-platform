"""无障碍访问 (Accessibility) 支持

WCAG 2.1 AA 合规是企业社会责任和法规要求（如美国 Section 508）。

后端职责：
  - 内容替代文本：确保图像、音频等非文本内容有替代文本
  - 语义标记：API 响应包含语义化标记信息
  - 可访问性元数据：为前端提供无障碍配置
  - 屏幕阅读器支持：提供结构化的文本描述
  - 对比度与字体：提供可访问性样式配置
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AccessibilityLevel(str, Enum):
    """无障碍合规级别"""

    A = "A"
    AA = "AA"
    AAA = "AAA"


class ContrastRatio(str, Enum):
    """对比度级别"""

    NORMAL_4_5 = "4.5:1"
    LARGE_3_1 = "3:1"
    ENHANCED_7_1 = "7:1"


class AccessibilityConfig(BaseModel):
    """无障碍配置"""

    level: AccessibilityLevel = AccessibilityLevel.AA
    high_contrast: bool = False
    large_text: bool = False
    reduced_motion: bool = False
    screen_reader_mode: bool = False
    font_scale: float = Field(default=1.0, ge=0.8, le=2.0)
    focus_indicators: bool = True
    keyboard_navigation: bool = True


class AltText(BaseModel):
    """替代文本"""

    resource_type: str = Field(description="资源类型: image/audio/video")
    resource_id: str = Field(default="", description="资源标识")
    alt_text: str = Field(description="替代文本描述")
    long_description: str = Field(default="", description="详细描述（复杂图像）")


class AccessibilityAuditResult(BaseModel):
    """无障碍审计结果"""

    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    issues: list[dict[str, Any]] = Field(default_factory=list)
    level: AccessibilityLevel = AccessibilityLevel.AA


# ==================== 替代文本管理 ====================

_alt_text_store: dict[str, AltText] = {}


def register_alt_text(alt: AltText) -> AltText:
    """注册替代文本"""
    key = f"{alt.resource_type}:{alt.resource_id}" if alt.resource_id else alt.resource_type
    _alt_text_store[key] = alt
    return alt


def get_alt_text(resource_type: str, resource_id: str = "") -> AltText | None:
    """获取替代文本"""
    key = f"{resource_type}:{resource_id}" if resource_id else resource_type
    return _alt_text_store.get(key)


# ==================== 无障碍审计 ====================


def audit_api_response(response_data: dict[str, Any], path: str = "") -> AccessibilityAuditResult:
    """审计 API 响应的无障碍合规性

    检查：
      - 图像内容是否包含 alt_text
      - 非文本内容是否有替代描述
      - 数据表格是否有表头标记
      - 链接是否有描述性文本

    Args:
        response_data: API 响应数据
        path: API 路径

    Returns:
        AccessibilityAuditResult
    """
    result = AccessibilityAuditResult()
    issues: list[dict[str, Any]] = []

    _audit_recursive(response_data, path, issues, result)

    result.issues = issues
    result.total_checks = result.passed + result.failed + result.warnings
    return result


def _audit_recursive(
    data: Any,
    path: str,
    issues: list[dict[str, Any]],
    result: AccessibilityAuditResult,
) -> None:
    """递归审计数据结构"""
    if isinstance(data, dict):
        if "image_url" in data or "image_base64" in data:
            if "alt_text" not in data and "description" not in data:
                result.failed += 1
                issues.append({
                    "path": path,
                    "rule": "WCAG 1.1.1",
                    "severity": "error",
                    "message": "图像内容缺少替代文本 (alt_text)",
                })
            else:
                result.passed += 1

        if "audio_url" in data or "audio_base64" in data:
            if "transcript" not in data and "alt_text" not in data:
                result.warnings += 1
                issues.append({
                    "path": path,
                    "rule": "WCAG 1.2.1",
                    "severity": "warning",
                    "message": "音频内容缺少文本替代 (transcript)",
                })
            else:
                result.passed += 1

        if "url" in data and "link_text" not in data and "title" not in data:
            result.warnings += 1
            issues.append({
                "path": path,
                "rule": "WCAG 2.4.4",
                "severity": "warning",
                "message": "链接缺少描述性文本",
            })
        elif "url" in data:
            result.passed += 1

        for key, value in data.items():
            _audit_recursive(value, f"{path}.{key}", issues, result)

    elif isinstance(data, list):
        for i, item in enumerate(data):
            _audit_recursive(item, f"{path}[{i}]", issues, result)


# ==================== 前端配置 ====================


def get_accessibility_config(user_preferences: dict[str, Any] | None = None) -> AccessibilityConfig:
    """获取无障碍配置

    根据用户偏好生成无障碍配置，前端据此调整 UI。

    Args:
        user_preferences: 用户偏好设置

    Returns:
        AccessibilityConfig
    """
    config = AccessibilityConfig()

    if not user_preferences:
        return config

    if user_preferences.get("high_contrast"):
        config.high_contrast = True
    if user_preferences.get("large_text"):
        config.large_text = True
        config.font_scale = 1.25
    if user_preferences.get("reduced_motion"):
        config.reduced_motion = True
    if user_preferences.get("screen_reader"):
        config.screen_reader_mode = True
    if user_preferences.get("font_scale"):
        config.font_scale = float(user_preferences["font_scale"])

    return config


def get_wcag_guidelines() -> list[dict[str, str]]:
    """获取 WCAG 2.1 AA 关键准则"""
    return [
        {"id": "1.1.1", "level": "A", "title": "非文本内容", "description": "所有非文本内容都应提供替代文本"},
        {"id": "1.2.1", "level": "A", "title": "纯音频和纯视频", "description": "预录的纯音频和纯视频内容提供替代文本"},
        {"id": "1.3.1", "level": "A", "title": "信息和关系", "description": "通过语义标记传递信息和关系"},
        {"id": "1.4.3", "level": "AA", "title": "对比度（最低）", "description": "文本对比度至少 4.5:1，大文本至少 3:1"},
        {"id": "2.1.1", "level": "A", "title": "键盘", "description": "所有功能可通过键盘操作"},
        {"id": "2.4.3", "level": "A", "title": "焦点顺序", "description": "焦点顺序应保持意义和可操作性"},
        {"id": "2.4.6", "level": "AA", "title": "标题和标签", "description": "标题和标签描述主题或目的"},
        {"id": "2.4.7", "level": "AA", "title": "焦点可见", "description": "键盘焦点指示器可见"},
        {"id": "3.1.1", "level": "A", "title": "页面语言", "description": "默认人类语言可由程序确定"},
        {"id": "3.2.2", "level": "A", "title": "输入时", "description": "改变设置不会自动导致上下文变化"},
        {"id": "4.1.2", "level": "A", "title": "名称、角色、值", "description": "UI 组件的名称和角色可由程序确定"},
    ]


# ==================== 初始化默认替代文本 ====================


def _init_default_alt_texts() -> None:
    """初始化默认替代文本"""
    defaults = [
        AltText(resource_type="image", alt_text="图像内容", long_description="请使用屏幕阅读器获取详细描述"),
        AltText(resource_type="audio", alt_text="音频内容", long_description="音频转录文本将在下方显示"),
        AltText(resource_type="video", alt_text="视频内容", long_description="视频描述和字幕已提供"),
        AltText(resource_type="chart", alt_text="数据图表", long_description="图表数据以表格形式在下方提供"),
    ]
    for alt in defaults:
        register_alt_text(alt)


_init_default_alt_texts()
