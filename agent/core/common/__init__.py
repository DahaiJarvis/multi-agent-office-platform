"""通用工具模块

提供无障碍访问、国际化、多模态处理、脱敏工具等通用能力。
"""

from agent.core.common.accessibility import (
    AccessibilityLevel,
    ContrastRatio,
    AccessibilityConfig,
    AltText,
    AccessibilityAuditResult,
    register_alt_text,
    get_alt_text,
    audit_api_response,
    get_accessibility_config,
    get_wcag_guidelines,
)
from agent.core.common.i18n import (
    Locale,
    t,
    detect_locale,
    get_supported_locales,
    add_translation,
    get_missing_translations,
)
from agent.core.common.multimodal import (
    ModalityType,
    ImageFormat,
    AudioFormat,
    MultimodalContent,
    MultimodalMessage,
    ImageAnalysisResult,
    ASRResult,
    TTSRequest,
    analyze_image,
    speech_to_text,
    text_to_speech,
)
from agent.core.common.sanitize_utils import (
    detect_sensitive_info,
    sanitize_text,
    sanitize_data,
)

__all__ = [
    "AccessibilityLevel",
    "ContrastRatio",
    "AccessibilityConfig",
    "AltText",
    "AccessibilityAuditResult",
    "register_alt_text",
    "get_alt_text",
    "audit_api_response",
    "get_accessibility_config",
    "get_wcag_guidelines",
    "Locale",
    "t",
    "detect_locale",
    "get_supported_locales",
    "add_translation",
    "get_missing_translations",
    "ModalityType",
    "ImageFormat",
    "AudioFormat",
    "MultimodalContent",
    "MultimodalMessage",
    "ImageAnalysisResult",
    "ASRResult",
    "TTSRequest",
    "analyze_image",
    "speech_to_text",
    "text_to_speech",
    "detect_sensitive_info",
    "sanitize_text",
    "sanitize_data",
]
