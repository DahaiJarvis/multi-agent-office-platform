"""公共脱敏工具

提供敏感信息检测和脱敏的通用函数，供多个模块复用。

使用方式:
    from agent.core.sanitize_utils import detect_sensitive_info, sanitize_text, sanitize_data

    # 检测敏感信息
    warnings = detect_sensitive_info(text)

    # 脱敏处理
    safe_text = sanitize_text(text)

    # 递归脱敏
    safe_data = sanitize_data(data)
"""

import re
from typing import Any

SENSITIVE_PATTERNS: list[tuple[str, str, str]] = [
    (r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "身份证号", "[身份证已脱敏]"),
    (r"\b1[3-9]\d{9}\b", "手机号", "[手机号已脱敏]"),
    (r"\b\d{16,19}\b", "银行卡号", "[银行卡已脱敏]"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "邮箱地址", "[邮箱已脱敏]"),
    (r"(?:password|passwd|pwd|secret|token|key)\s*[:=]\s*\S+", "敏感凭证", "[凭证已脱敏]"),
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "IP地址", "[IP已脱敏]"),
]


def detect_sensitive_info(content: str) -> list[tuple[str, int]]:
    """检测文本中的敏感信息

    Args:
        content: 待检测文本

    Returns:
        [(敏感信息类型, 出现次数), ...]
    """
    results = []
    for pattern, name, _ in SENSITIVE_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            results.append((name, len(matches)))
    return results


def sanitize_text(text: str) -> str:
    """脱敏文本中的敏感信息

    Args:
        text: 待脱敏文本

    Returns:
        脱敏后的文本
    """
    sanitized = text
    for pattern, _, replacement in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized


def sanitize_data(data: Any) -> Any:
    """递归脱敏数据结构中的敏感信息

    Args:
        data: 待脱敏数据（支持 str/dict/list/其他）

    Returns:
        脱敏后的数据
    """
    if isinstance(data, str):
        return sanitize_text(data)
    if isinstance(data, dict):
        return {k: sanitize_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_data(item) for item in data]
    return data
