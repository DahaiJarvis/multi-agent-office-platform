"""数据脱敏

PII（个人可识别信息）识别与脱敏处理，与架构文档 7.3.2 节对齐。
支持手机号、身份证号、邮箱、银行卡号等敏感数据的自动检测与脱敏。
"""

import logging
import re
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class PIIDetection(BaseModel):
    """PII 检测结果"""

    pii_type: str
    original: str
    desensitized: str
    position: int


# PII 正则模式与脱敏规则（处理顺序：先处理长模式，避免短模式误匹配）
PII_PATTERNS: dict[str, tuple[str, str]] = {
    "email": (
        r"[a-zA-Z0-9][a-zA-Z0-9._-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        r"",
    ),
    "id_card": (
        r"(?<!\d)(\d{6})\d{8}(\d{4}[\dXx])(?!\d)",
        r"\1********\2",
    ),
    "bank_card": (
        r"(?<!\d)(\d{4})\d{8,15}(\d{4})(?!\d)",
        r"\1****\2",
    ),
    "phone": (
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"",
    ),
}

# 预编译正则
_COMPILED_PATTERNS: dict[str, re.Pattern] = {
    name: re.compile(pattern) for name, (pattern, _) in PII_PATTERNS.items()
}

# 免脱敏角色（可查看原始数据）
DESENSITIZE_EXEMPT_ROLES = {"admin", "hr_specialist"}


def detect_pii(content: str) -> list[PIIDetection]:
    """检测文本中的 PII 信息

    Args:
        content: 待检测文本

    Returns:
        PII 检测结果列表
    """
    detections: list[PIIDetection] = []

    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = _COMPILED_PATTERNS[pii_type]
        for match in compiled.finditer(content):
            original = match.group()
            desensitized = _apply_desensitize(pii_type, original)
            detections.append(PIIDetection(
                pii_type=pii_type,
                original=original,
                desensitized=desensitized,
                position=match.start(),
            ))

    return detections


def desensitize_content(content: str, user_roles: list[str] | None = None) -> str:
    """对文本内容进行 PII 脱敏

    特定角色（admin、hr_specialist）可查看原始数据，不进行脱敏。

    Args:
        content: 原始文本
        user_roles: 用户角色列表

    Returns:
        脱敏后的文本
    """
    if user_roles:
        if any(role in DESENSITIZE_EXEMPT_ROLES for role in user_roles):
            return content

    result = content
    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = _COMPILED_PATTERNS[pii_type]
        result = compiled.sub(lambda m: _apply_desensitize(pii_type, m.group()), result)

    return result


def _apply_desensitize(pii_type: str, original: str) -> str:
    """对单个 PII 匹配项应用脱敏

    Args:
        pii_type: PII 类型
        original: 原始文本

    Returns:
        脱敏后的文本
    """
    if pii_type == "phone":
        if len(original) == 11:
            return original[:3] + "****" + original[-4:]
        return original[:3] + "****"

    if pii_type == "id_card":
        if len(original) >= 14:
            return original[:6] + "********" + original[-4:]
        return original[:6] + "****"

    if pii_type == "email":
        at_index = original.find("@")
        if at_index > 0:
            return original[0] + "***@" + original[at_index + 1:]
        return original

    if pii_type == "bank_card":
        if len(original) >= 8:
            return original[:4] + "****" + original[-4:]
        return "****"

    return original


def has_pii(content: str) -> bool:
    """快速判断文本是否包含 PII

    Args:
        content: 待检测文本

    Returns:
        是否包含 PII
    """
    for compiled in _COMPILED_PATTERNS.values():
        if compiled.search(content):
            return True
    return False
