"""文本处理原生工具

提供文本格式转换、关键信息提取和翻译功能。

工具列表：
  -------------------------------------------------------------------------
  native_text_format: 文本格式转换（Markdown/HTML/纯文本）
    - 延迟分层: instant
    - 权限级别: read_only
    - 注册方式: 立即注册（无外部依赖）

  native_text_extract: 从文本中提取关键信息
    - 延迟分层: slow
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖 LLM）

  native_text_translate: 文本翻译
    - 延迟分层: slow
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖 LLM）
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


def _markdown_to_html(text: str) -> str:
    """将 Markdown 文本转换为 HTML

    支持标题、粗体、斜体、链接、列表、代码块等常见 Markdown 语法。

    Args:
        text: Markdown 文本

    Returns:
        HTML 文本
    """
    lines = text.split("\n")
    html_lines: list[str] = []
    in_code_block = False
    in_list = False

    for line in lines:
        if line.strip().startswith("```"):
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                html_lines.append("<pre><code>")
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(line)
            continue

        stripped = line.strip()

        if stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{_inline_md_to_html(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{_inline_md_to_html(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{_inline_md_to_html(stripped[4:])}</h3>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_md_to_html(stripped[2:])}</li>")
        elif stripped.startswith("> "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<blockquote>{_inline_md_to_html(stripped[2:])}</blockquote>")
        elif stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{_inline_md_to_html(stripped)}</p>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False

    if in_list:
        html_lines.append("</ul>")
    if in_code_block:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


def _inline_md_to_html(text: str) -> str:
    """转换行内 Markdown 语法为 HTML

    Args:
        text: 包含行内 Markdown 语法的文本

    Returns:
        HTML 文本
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text


def _html_to_plain(text: str) -> str:
    """将 HTML 文本转换为纯文本

    移除所有 HTML 标签，保留文本内容。

    Args:
        text: HTML 文本

    Returns:
        纯文本
    """
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _markdown_to_plain(text: str) -> str:
    """将 Markdown 文本转换为纯文本

    移除 Markdown 语法标记，保留文本内容。

    Args:
        text: Markdown 文本

    Returns:
        纯文本
    """
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).strip("`").strip(), text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    return text.strip()


async def _text_format(text: str, target_format: str = "markdown") -> str:
    """文本格式转换

    将文本在 Markdown、HTML 和纯文本格式之间转换。

    Args:
        text: 待转换的文本内容
        target_format: 目标格式，markdown / html / plain

    Returns:
        JSON 格式的转换结果
    """
    if not text or not text.strip():
        return json.dumps({"error": "文本内容不能为空", "result": ""}, ensure_ascii=False)

    if target_format not in ("markdown", "html", "plain"):
        return json.dumps({"error": f"不支持的目标格式: {target_format}，仅允许 markdown、html、plain", "result": ""}, ensure_ascii=False)

    try:
        original_text = text.strip()
        result_text = original_text

        has_html_tags = bool(re.search(r"<[a-zA-Z][^>]*>", original_text))
        has_md_syntax = bool(re.search(r"[#*`\[\]>]", original_text))

        if target_format == "html":
            if has_md_syntax and not has_html_tags:
                result_text = _markdown_to_html(original_text)
            elif has_html_tags:
                result_text = original_text
            else:
                paragraphs = original_text.split("\n\n")
                result_text = "\n".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())

        elif target_format == "plain":
            if has_html_tags:
                result_text = _html_to_plain(original_text)
            elif has_md_syntax:
                result_text = _markdown_to_plain(original_text)
            else:
                result_text = original_text

        elif target_format == "markdown":
            if has_html_tags:
                plain = _html_to_plain(original_text)
                result_text = plain
            else:
                result_text = original_text

        result = {
            "original_length": len(original_text),
            "result_length": len(result_text),
            "target_format": target_format,
            "result": result_text,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("文本格式转换失败: error=%s", e)
        return json.dumps({"error": f"文本格式转换失败: {str(e)}", "result": ""}, ensure_ascii=False)


async def _text_extract(text: str, extract_type: str = "auto") -> str:
    """从文本中提取关键信息

    使用 LLM 从文本中提取关键信息，如人名、日期、金额、地址等。

    Args:
        text: 待提取的文本内容
        extract_type: 提取类型，auto(自动) / person(人名) / date(日期) / amount(金额) / address(地址) / key_points(要点)

    Returns:
        JSON 格式的提取结果
    """
    if not text or not text.strip():
        return json.dumps({"error": "文本内容不能为空", "extractions": []}, ensure_ascii=False)

    try:
        from agent.core.model_client import get_lightweight_client
        from autogen_core.models import UserMessage

        client = get_lightweight_client()

        extract_instructions = {
            "auto": "请从以下文本中提取所有关键信息，包括人名、日期、金额、地址、组织名称等，按类型分类输出",
            "person": "请从以下文本中提取所有人物姓名",
            "date": "请从以下文本中提取所有日期和时间信息",
            "amount": "请从以下文本中提取所有金额和数值信息",
            "address": "请从以下文本中提取所有地址和位置信息",
            "key_points": "请从以下文本中提取所有关键要点和核心观点",
        }

        instruction = extract_instructions.get(extract_type, extract_instructions["auto"])

        max_len = 6000
        truncated = text.strip()[:max_len]

        prompt = (
            f"{instruction}。\n\n"
            "请以 JSON 格式输出，格式如下：\n"
            '{"extractions": [{"type": "类型", "value": "值", "context": "上下文"}]}\n\n'
            f"文本内容：\n{truncated}"
        )

        response = await client.create(
            messages=[UserMessage(source="user", content=prompt)],
            extra_create_args={"temperature": 0.1, "max_tokens": 1500},
        )

        content = response.content if isinstance(response.content, str) else str(response.content)

        extractions = []
        try:
            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                parsed = json.loads(json_match.group())
                extractions = parsed.get("extractions", [])
        except json.JSONDecodeError:
            for line in content.strip().split("\n"):
                line = line.strip()
                if line.startswith("- ") or line.startswith("* "):
                    extractions.append({"type": extract_type, "value": line[2:].strip(), "context": ""})

        result = {
            "extract_type": extract_type,
            "extractions": extractions,
            "text_length": len(text.strip()),
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("文本信息提取失败: error=%s", e)
        return json.dumps({"error": f"文本信息提取失败: {str(e)}", "extractions": []}, ensure_ascii=False)


async def _text_translate(text: str, target_lang: str = "en", source_lang: str = "auto") -> str:
    """文本翻译

    使用 LLM 将文本翻译为指定语言。

    Args:
        text: 待翻译的文本内容
        target_lang: 目标语言，en(英语) / zh(中文) / ja(日语) / ko(韩语)
        source_lang: 源语言，auto(自动检测) / en / zh / ja / ko

    Returns:
        JSON 格式的翻译结果
    """
    if not text or not text.strip():
        return json.dumps({"error": "文本内容不能为空", "translation": ""}, ensure_ascii=False)

    allowed_langs = {"en", "zh", "ja", "ko"}
    if target_lang not in allowed_langs:
        return json.dumps({"error": f"不支持的目标语言: {target_lang}，支持 en/zh/ja/ko", "translation": ""}, ensure_ascii=False)

    try:
        from agent.core.model_client import get_lightweight_client
        from autogen_core.models import UserMessage

        client = get_lightweight_client()

        lang_names = {"en": "英语", "zh": "中文", "ja": "日语", "ko": "韩语"}
        target_name = lang_names.get(target_lang, target_lang)

        source_instruction = ""
        if source_lang != "auto" and source_lang in lang_names:
            source_instruction = f"（原文为{lang_names[source_lang]}）"

        max_len = 6000
        truncated = text.strip()[:max_len]

        prompt = (
            f"请将以下文本翻译为{target_name}{source_instruction}。"
            "只输出翻译结果，不要添加解释或注释。\n\n"
            f"{truncated}"
        )

        response = await client.create(
            messages=[UserMessage(source="user", content=prompt)],
            extra_create_args={"temperature": 0.3, "max_tokens": 2000},
        )

        translation = response.content if isinstance(response.content, str) else str(response.content)

        result = {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "original_length": len(text.strip()),
            "translation": translation.strip(),
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("文本翻译失败: error=%s", e)
        return json.dumps({"error": f"文本翻译失败: {str(e)}", "translation": ""}, ensure_ascii=False)


_TEXT_FORMAT_META = NativeToolMeta(
    name="native_text_format",
    display_name="文本格式转换",
    description="将文本在 Markdown、HTML 和纯文本格式之间转换。支持标题、粗体、斜体、链接、列表、代码块等常见语法。",
    category="text",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待转换的文本内容",
            },
            "target_format": {
                "type": "string",
                "description": "目标格式: markdown / html / plain",
                "enum": ["markdown", "html", "plain"],
                "default": "markdown",
            },
        },
        "required": ["text"],
    },
    latency_tier=LatencyTier.INSTANT,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=5,
    requires_llm=False,
    tags=["text", "format", "markdown", "html"],
)

_TEXT_EXTRACT_META = NativeToolMeta(
    name="native_text_extract",
    display_name="关键信息提取",
    description="使用 AI 从文本中提取关键信息，如人名、日期、金额、地址、核心要点等。",
    category="text",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待提取的文本内容",
            },
            "extract_type": {
                "type": "string",
                "description": "提取类型: auto(自动) / person(人名) / date(日期) / amount(金额) / address(地址) / key_points(要点)",
                "enum": ["auto", "person", "date", "amount", "address", "key_points"],
                "default": "auto",
            },
        },
        "required": ["text"],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=30,
    requires_llm=True,
    tags=["text", "extract", "llm"],
)

_TEXT_TRANSLATE_META = NativeToolMeta(
    name="native_text_translate",
    display_name="文本翻译",
    description="使用 AI 将文本翻译为指定语言，支持英语、中文、日语、韩语。",
    category="text",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待翻译的文本内容",
            },
            "target_lang": {
                "type": "string",
                "description": "目标语言: en(英语) / zh(中文) / ja(日语) / ko(韩语)",
                "enum": ["en", "zh", "ja", "ko"],
                "default": "en",
            },
            "source_lang": {
                "type": "string",
                "description": "源语言: auto(自动检测) / en / zh / ja / ko",
                "default": "auto",
            },
        },
        "required": ["text"],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=30,
    requires_llm=True,
    tags=["text", "translate", "llm"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有文本处理工具

    native_text_format 无外部依赖，使用立即注册。
    native_text_extract 和 native_text_translate 依赖 LLM，使用懒注册。

    Args:
        registry: 工具注册中心实例
    """
    format_tool = FunctionTool(
        func=_text_format,
        name="native_text_format",
        description=_TEXT_FORMAT_META.description,
    )
    registry.register(format_tool, _TEXT_FORMAT_META)

    def _create_extract_tool() -> FunctionTool:
        return FunctionTool(
            func=_text_extract,
            name="native_text_extract",
            description=_TEXT_EXTRACT_META.description,
        )

    def _create_translate_tool() -> FunctionTool:
        return FunctionTool(
            func=_text_translate,
            name="native_text_translate",
            description=_TEXT_TRANSLATE_META.description,
        )

    registry.register_lazy("native_text_extract", _create_extract_tool, _TEXT_EXTRACT_META)
    registry.register_lazy("native_text_translate", _create_translate_tool, _TEXT_TRANSLATE_META)

    logger.debug("文本处理工具注册完成: native_text_format(立即), native_text_extract, native_text_translate(懒注册)")
