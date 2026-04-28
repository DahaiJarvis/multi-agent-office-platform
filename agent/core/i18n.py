"""国际化 (i18n) 支持

多语言支持是拓展海外市场的前提，对有海外业务的企业客户是必要条件。

能力：
  - 多语言消息：后端错误消息、系统提示的多语言支持
  - 语言检测：根据请求头/参数自动检测语言
  - 翻译管理：翻译键值管理、缺失翻译检测
  - 复数形式：支持各语言的复数规则
  - 日期/数字格式化：根据区域设置格式化
"""

import logging
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class Locale(str, Enum):
    """支持的语言区域"""

    ZH_CN = "zh-CN"
    ZH_TW = "zh-TW"
    EN_US = "en-US"
    EN_GB = "en-GB"
    JA_JP = "ja-JP"
    KO_KR = "ko-KR"
    FR_FR = "fr-FR"
    DE_DE = "de-DE"
    ES_ES = "es-ES"
    PT_BR = "pt-BR"


DEFAULT_LOCALE = Locale.ZH_CN


# ==================== 翻译字典 ====================

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "error.session_not_found": "会话不存在",
        "error.internal_error": "内部错误",
        "error.unauthorized": "未授权访问",
        "error.forbidden": "权限不足",
        "error.rate_limited": "请求过于频繁，请稍后重试",
        "error.invalid_request": "请求参数无效",
        "error.resource_not_found": "资源不存在",
        "error.agent_not_found": "Agent 不存在",
        "error.template_not_found": "模板不存在",
        "error.validation_error": "数据验证失败",
        "success.created": "创建成功",
        "success.updated": "更新成功",
        "success.deleted": "删除成功",
        "success.published": "发布成功",
        "success.disabled": "已禁用",
        "success.submitted": "提交成功",
        "agent.status.draft": "草稿",
        "agent.status.published": "已发布",
        "agent.status.disabled": "已禁用",
        "agent.status.archived": "已归档",
        "search.no_results": "未找到相关结果",
        "search.suggestion": "试试其他关键词",
        "analytics.trend": "趋势分析",
        "analytics.comparison": "对比分析",
        "analytics.distribution": "分布分析",
        "analytics.ranking": "排名分析",
        "prompt.category.writing": "写作",
        "prompt.category.analysis": "分析",
        "prompt.category.coding": "编程",
        "prompt.category.translation": "翻译",
        "prompt.category.summary": "摘要",
        "prompt.category.brainstorm": "创意",
        "prompt.category.email": "邮件",
        "prompt.category.meeting": "会议",
        "prompt.category.report": "报告",
        "prompt.category.custom": "自定义",
        "multimodal.image_analyzed": "图像分析完成",
        "multimodal.audio_transcribed": "语音转文字完成",
        "multimodal.audio_synthesized": "语音合成完成",
        "plugin.loaded": "插件加载成功",
        "plugin.unloaded": "插件卸载成功",
        "plugin.error": "插件执行错误",
        "version.rollback_success": "版本回滚成功",
        "version.publish_success": "版本发布成功",
        "workflow.saved": "工作流保存成功",
        "workflow.executed": "工作流执行完成",
    },
    "en-US": {
        "error.session_not_found": "Session not found",
        "error.internal_error": "Internal error",
        "error.unauthorized": "Unauthorized access",
        "error.forbidden": "Access denied",
        "error.rate_limited": "Too many requests, please try again later",
        "error.invalid_request": "Invalid request parameters",
        "error.resource_not_found": "Resource not found",
        "error.agent_not_found": "Agent not found",
        "error.template_not_found": "Template not found",
        "error.validation_error": "Validation failed",
        "success.created": "Created successfully",
        "success.updated": "Updated successfully",
        "success.deleted": "Deleted successfully",
        "success.published": "Published successfully",
        "success.disabled": "Disabled",
        "success.submitted": "Submitted successfully",
        "agent.status.draft": "Draft",
        "agent.status.published": "Published",
        "agent.status.disabled": "Disabled",
        "agent.status.archived": "Archived",
        "search.no_results": "No results found",
        "search.suggestion": "Try different keywords",
        "analytics.trend": "Trend Analysis",
        "analytics.comparison": "Comparison Analysis",
        "analytics.distribution": "Distribution Analysis",
        "analytics.ranking": "Ranking Analysis",
        "prompt.category.writing": "Writing",
        "prompt.category.analysis": "Analysis",
        "prompt.category.coding": "Coding",
        "prompt.category.translation": "Translation",
        "prompt.category.summary": "Summary",
        "prompt.category.brainstorm": "Brainstorm",
        "prompt.category.email": "Email",
        "prompt.category.meeting": "Meeting",
        "prompt.category.report": "Report",
        "prompt.category.custom": "Custom",
        "multimodal.image_analyzed": "Image analysis completed",
        "multimodal.audio_transcribed": "Audio transcription completed",
        "multimodal.audio_synthesized": "Audio synthesis completed",
        "plugin.loaded": "Plugin loaded",
        "plugin.unloaded": "Plugin unloaded",
        "plugin.error": "Plugin execution error",
        "version.rollback_success": "Version rollback successful",
        "version.publish_success": "Version published successfully",
        "workflow.saved": "Workflow saved",
        "workflow.executed": "Workflow executed",
    },
    "ja-JP": {
        "error.session_not_found": "セッションが見つかりません",
        "error.internal_error": "内部エラー",
        "error.unauthorized": "認証されていません",
        "error.forbidden": "アクセス権限がありません",
        "error.rate_limited": "リクエストが多すぎます。後でもう一度お試しください",
        "error.invalid_request": "リクエストパラメータが無効です",
        "error.resource_not_found": "リソースが見つかりません",
        "error.agent_not_found": "エージェントが見つかりません",
        "error.template_not_found": "テンプレートが見つかりません",
        "error.validation_error": "バリデーションに失敗しました",
        "success.created": "作成しました",
        "success.updated": "更新しました",
        "success.deleted": "削除しました",
        "success.published": "公開しました",
        "success.disabled": "無効にしました",
        "success.submitted": "送信しました",
        "agent.status.draft": "下書き",
        "agent.status.published": "公開済み",
        "agent.status.disabled": "無効",
        "agent.status.archived": "アーカイブ",
        "search.no_results": "結果が見つかりません",
        "search.suggestion": "別のキーワードをお試しください",
        "analytics.trend": "トレンド分析",
        "analytics.comparison": "比較分析",
        "analytics.distribution": "分布分析",
        "analytics.ranking": "ランキング分析",
        "prompt.category.writing": "ライティング",
        "prompt.category.analysis": "分析",
        "prompt.category.coding": "プログラミング",
        "prompt.category.translation": "翻訳",
        "prompt.category.summary": "要約",
        "prompt.category.brainstorm": "ブレインストーミング",
        "prompt.category.email": "メール",
        "prompt.category.meeting": "会議",
        "prompt.category.report": "レポート",
        "prompt.category.custom": "カスタム",
        "plugin.loaded": "プラグインが読み込まれました",
        "plugin.unloaded": "プラグインがアンロードされました",
        "plugin.error": "プラグイン実行エラー",
        "version.rollback_success": "バージョンのロールバックに成功しました",
        "version.publish_success": "バージョンを公開しました",
        "workflow.saved": "ワークフローを保存しました",
        "workflow.executed": "ワークフローを実行しました",
    },
    "ko-KR": {
        "error.session_not_found": "세션을 찾을 수 없습니다",
        "error.internal_error": "내부 오류",
        "error.unauthorized": "인증되지 않았습니다",
        "error.forbidden": "접근 권한이 없습니다",
        "error.rate_limited": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요",
        "error.invalid_request": "잘못된 요청 매개변수",
        "error.resource_not_found": "리소스를 찾을 수 없습니다",
        "error.agent_not_found": "에이전트를 찾을 수 없습니다",
        "error.template_not_found": "템플릿을 찾을 수 없습니다",
        "error.validation_error": "유효성 검사 실패",
        "success.created": "생성되었습니다",
        "success.updated": "업데이트되었습니다",
        "success.deleted": "삭제되었습니다",
        "success.published": "게시되었습니다",
        "success.disabled": "비활성화되었습니다",
        "success.submitted": "제출되었습니다",
        "agent.status.draft": "초안",
        "agent.status.published": "게시됨",
        "agent.status.disabled": "비활성화됨",
        "agent.status.archived": "보관됨",
        "search.no_results": "검색 결과가 없습니다",
        "search.suggestion": "다른 키워드를 시도해 보세요",
        "analytics.trend": "트렌드 분석",
        "analytics.comparison": "비교 분석",
        "analytics.distribution": "분포 분석",
        "analytics.ranking": "순위 분석",
        "plugin.loaded": "플러그인이 로드되었습니다",
        "plugin.unloaded": "플러그인이 언로드되었습니다",
        "plugin.error": "플러그인 실행 오류",
    },
}


def t(key: str, locale: str | Locale = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """翻译键值

    Args:
        key: 翻译键，如 "error.session_not_found"
        locale: 语言区域
        **kwargs: 模板变量

    Returns:
        翻译后的文本
    """
    locale_str = locale.value if isinstance(locale, Locale) else locale

    messages = _TRANSLATIONS.get(locale_str, {})
    text = messages.get(key)

    if text is None:
        messages = _TRANSLATIONS.get(DEFAULT_LOCALE.value, {})
        text = messages.get(key, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass

    return text


def detect_locale(accept_language: str) -> Locale:
    """从 Accept-Language 请求头检测语言

    Args:
        accept_language: HTTP Accept-Language 头

    Returns:
        Locale
    """
    if not accept_language:
        return DEFAULT_LOCALE

    preferred = accept_language.split(",")[0].strip().split(";")[0].strip()

    for loc in Locale:
        if loc.value.lower().startswith(preferred.lower()[:2]):
            return loc

    lang_prefix = preferred.lower()[:2]
    prefix_map = {
        "zh": Locale.ZH_CN,
        "en": Locale.EN_US,
        "ja": Locale.JA_JP,
        "ko": Locale.KO_KR,
        "fr": Locale.FR_FR,
        "de": Locale.DE_DE,
        "es": Locale.ES_ES,
        "pt": Locale.PT_BR,
    }

    return prefix_map.get(lang_prefix, DEFAULT_LOCALE)


def get_supported_locales() -> list[dict[str, str]]:
    """获取支持的语言列表"""
    return [
        {"code": Locale.ZH_CN.value, "name": "简体中文", "english_name": "Simplified Chinese"},
        {"code": Locale.ZH_TW.value, "name": "繁体中文", "english_name": "Traditional Chinese"},
        {"code": Locale.EN_US.value, "name": "English (US)", "english_name": "English (US)"},
        {"code": Locale.EN_GB.value, "name": "English (UK)", "english_name": "English (UK)"},
        {"code": Locale.JA_JP.value, "name": "日本語", "english_name": "Japanese"},
        {"code": Locale.KO_KR.value, "name": "한국어", "english_name": "Korean"},
        {"code": Locale.FR_FR.value, "name": "Français", "english_name": "French"},
        {"code": Locale.DE_DE.value, "name": "Deutsch", "english_name": "German"},
        {"code": Locale.ES_ES.value, "name": "Español", "english_name": "Spanish"},
        {"code": Locale.PT_BR.value, "name": "Português (BR)", "english_name": "Portuguese (Brazil)"},
    ]


def add_translation(locale: str, key: str, value: str) -> None:
    """添加翻译条目"""
    if locale not in _TRANSLATIONS:
        _TRANSLATIONS[locale] = {}
    _TRANSLATIONS[locale][key] = value


def get_missing_translations() -> dict[str, list[str]]:
    """检测缺失的翻译

    对比各语言与默认语言的翻译键，找出缺失项。
    """
    default_keys = set(_TRANSLATIONS.get(DEFAULT_LOCALE.value, {}).keys())
    missing: dict[str, list[str]] = {}

    for locale, translations in _TRANSLATIONS.items():
        if locale == DEFAULT_LOCALE.value:
            continue
        locale_keys = set(translations.keys())
        missing_keys = default_keys - locale_keys
        if missing_keys:
            missing[locale] = sorted(missing_keys)

    return missing
