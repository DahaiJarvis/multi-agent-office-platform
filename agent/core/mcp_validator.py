"""MCP 响应校验层

对 MCP 工具调用的返回结果进行结构校验、安全检查和质量评估，
确保下游系统返回的数据符合预期格式和安全要求。

核心能力：
  - 结构校验：验证响应是否包含必要字段、数据类型是否正确
  - 安全检查：检测响应中的敏感信息泄露、注入攻击
  - 质量评估：评估响应的完整性和可信度
  - 降级处理：校验失败时提供安全的默认值

使用方式：
    from agent.core.mcp_validator import validate_mcp_response

    result = await validate_mcp_response("knowledge", "search", raw_response)
    if result.is_valid:
        data = result.sanitized_data
    else:
        logger.warning("MCP 响应校验失败: %s", result.errors)
"""

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """校验结果"""

    is_valid: bool = Field(default=True, description="是否通过校验")
    sanitized_data: Any = Field(default=None, description="清洗后的安全数据")
    errors: list[str] = Field(default_factory=list, description="校验错误列表")
    warnings: list[str] = Field(default_factory=list, description="警告列表")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="响应可信度")
    server_name: str = Field(default="", description="MCP 服务名")
    tool_name: str = Field(default="", description="工具名")
    duration_ms: float = Field(default=0, description="校验耗时")


# 敏感信息检测模式（从公共脱敏模块导入，降级到内联定义）
try:
    from agent.core.sanitize_utils import SENSITIVE_PATTERNS as SENSITIVE_PATTERNS
except ImportError:
    SENSITIVE_PATTERNS: list[tuple[str, str, str]] = [
        (r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "身份证号", "[身份证已脱敏]"),
        (r"\b1[3-9]\d{9}\b", "手机号", "[手机号已脱敏]"),
        (r"\b\d{16,19}\b", "银行卡号", "[银行卡已脱敏]"),
        (r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "邮箱地址", "[邮箱已脱敏]"),
        (r"(?:password|passwd|pwd|secret|token|key)\s*[:=]\s*\S+", "敏感凭证", "[凭证已脱敏]"),
        (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "IP地址", "[IP已脱敏]"),
    ]

# SQL 注入检测模式
SQL_INJECTION_PATTERNS: list[str] = [
    r"(?i)(\bunion\b.*\bselect\b)",
    r"(?i)(\bdrop\b.*\btable\b)",
    r"(?i)(\bdelete\b.*\bfrom\b)",
    r"(?i)(\binsert\b.*\binto\b)",
    r"(?i)(\bupdate\b.*\bset\b)",
    r"(?i)(--.*$)",
    r"(?i)(;\s*(?:drop|delete|insert|update))",
]

# XSS 检测模式
XSS_PATTERNS: list[str] = [
    r"<script[^>]*>.*?</script>",
    r"javascript\s*:",
    r"on\w+\s*=",
    r"<iframe[^>]*>",
    r"<img[^>]+onerror\s*=",
]

# 各 MCP 服务的响应结构校验规则
RESPONSE_SCHEMAS: dict[str, dict[str, Any]] = {
    "knowledge": {
        "required_fields": ["content"],
        "max_content_length": 50000,
        "allowed_types": [str, list, dict],
    },
    "oa": {
        "required_fields": [],
        "max_content_length": 20000,
        "allowed_types": [str, list, dict],
    },
    "email": {
        "required_fields": [],
        "max_content_length": 30000,
        "allowed_types": [str, list, dict],
    },
    "calendar": {
        "required_fields": [],
        "max_content_length": 10000,
        "allowed_types": [str, list, dict],
    },
    "crm": {
        "required_fields": [],
        "max_content_length": 20000,
        "allowed_types": [str, list, dict],
    },
    "hr": {
        "required_fields": [],
        "max_content_length": 15000,
        "allowed_types": [str, list, dict],
    },
    "finance": {
        "required_fields": [],
        "max_content_length": 15000,
        "allowed_types": [str, list, dict],
    },
}

# 校验统计
_validation_stats: dict[str, dict[str, int]] = {}


def _get_stats(server_name: str) -> dict[str, int]:
    """获取校验统计"""
    if server_name not in _validation_stats:
        _validation_stats[server_name] = {
            "total": 0, "valid": 0, "invalid": 0, "sanitized": 0,
        }
    return _validation_stats[server_name]


async def validate_mcp_response(
    server_name: str,
    tool_name: str,
    response: Any,
    strict: bool = False,
) -> ValidationResult:
    """校验 MCP 工具调用响应

    执行三层校验：
    1. 结构校验：验证响应格式和必要字段
    2. 安全检查：检测敏感信息和注入攻击
    3. 质量评估：评估响应完整性和可信度

    Args:
        server_name: MCP 服务名
        tool_name: 工具名
        response: 原始响应数据
        strict: 严格模式，校验失败时拒绝响应

    Returns:
        ValidationResult 校验结果
    """
    start_time = time.time()
    errors: list[str] = []
    warnings: list[str] = []
    confidence = 1.0
    sanitized = response

    stats = _get_stats(server_name)
    stats["total"] += 1

    # 1. 结构校验
    struct_errors = _validate_structure(server_name, response)
    errors.extend(struct_errors)
    if struct_errors:
        confidence -= 0.2

    # 2. 安全检查
    security_errors, security_warnings = _validate_security(response)
    errors.extend(security_errors)
    warnings.extend(security_warnings)
    if security_errors:
        confidence -= 0.4
    if security_warnings:
        confidence -= 0.1

    # 3. 质量评估
    quality_warnings = _validate_quality(server_name, response)
    warnings.extend(quality_warnings)
    if quality_warnings:
        confidence -= 0.1

    # 清洗数据
    if security_warnings or security_errors:
        sanitized = _sanitize_response(response)
        stats["sanitized"] += 1

    confidence = max(0.0, min(1.0, confidence))
    is_valid = len(errors) == 0 if strict else confidence >= 0.3

    if is_valid:
        stats["valid"] += 1
    else:
        stats["invalid"] += 1

    duration_ms = (time.time() - start_time) * 1000

    if not is_valid:
        logger.warning(
            "MCP 响应校验失败: server=%s tool=%s errors=%s confidence=%.2f",
            server_name, tool_name, errors, confidence,
        )

    return ValidationResult(
        is_valid=is_valid,
        sanitized_data=sanitized,
        errors=errors,
        warnings=warnings,
        confidence=confidence,
        server_name=server_name,
        tool_name=tool_name,
        duration_ms=duration_ms,
    )


def _validate_structure(server_name: str, response: Any) -> list[str]:
    """结构校验"""
    errors: list[str] = []
    schema = RESPONSE_SCHEMAS.get(server_name)

    if schema is None:
        return errors

    # 空响应检查
    if response is None:
        errors.append("响应为空（None）")
        return errors

    # 类型校验
    allowed_types = schema.get("allowed_types", [str, list, dict])
    if not isinstance(response, tuple(allowed_types)):
        errors.append(
            f"响应类型错误: 期望 {allowed_types}，实际 {type(response).__name__}"
        )
        return errors

    # 空内容检查（对字符串和集合类型）
    if isinstance(response, str) and not response.strip():
        errors.append("响应内容为空字符串")
    elif isinstance(response, (list, dict)) and not response:
        errors.append("响应数据为空集合")

    # 长度校验
    max_length = schema.get("max_content_length", 50000)
    content_str = json.dumps(response, ensure_ascii=False, default=str) if not isinstance(response, str) else response
    if len(content_str) > max_length:
        errors.append(f"响应过长: {len(content_str)} > {max_length}")

    # 必要字段校验
    required_fields = schema.get("required_fields", [])
    if required_fields:
        if isinstance(response, dict):
            for field_name in required_fields:
                if field_name not in response:
                    errors.append(f"缺少必要字段: {field_name}")
        elif isinstance(response, str):
            pass
        elif isinstance(response, list):
            pass
        else:
            errors.append(
                f"响应类型 {type(response).__name__} 无法包含必要字段 {required_fields}"
            )

    return errors


def _validate_security(response: Any) -> tuple[list[str], list[str]]:
    """安全检查"""
    errors: list[str] = []
    warnings: list[str] = []

    content = json.dumps(response, ensure_ascii=False, default=str) if not isinstance(response, str) else response

    # 敏感信息检测
    for pattern, name, _ in SENSITIVE_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            warnings.append(f"检测到可能的{name}: {len(matches)}处")

    # SQL 注入检测
    for pattern in SQL_INJECTION_PATTERNS:
        if re.search(pattern, content):
            errors.append(f"检测到可能的SQL注入攻击")
            break

    # XSS 检测
    for pattern in XSS_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE | re.DOTALL):
            errors.append(f"检测到可能的XSS攻击")
            break

    return errors, warnings


def _validate_quality(server_name: str, response: Any) -> list[str]:
    """质量评估"""
    warnings: list[str] = []

    # 空响应检查
    if response is None:
        warnings.append("响应为空")
    elif isinstance(response, str) and not response.strip():
        warnings.append("响应内容为空字符串")
    elif isinstance(response, (list, dict)) and not response:
        warnings.append("响应数据为空集合")

    # 截断检查（响应末尾不完整）
    if isinstance(response, str):
        if response.endswith("...") or response.endswith("...（内容过长，已截断）"):
            warnings.append("响应可能被截断")

    return warnings


def _sanitize_response(response: Any) -> Any:
    """清洗响应中的敏感信息"""
    try:
        from agent.core.sanitize_utils import sanitize_data
        return sanitize_data(response)
    except ImportError:
        if isinstance(response, str):
            sanitized = response
            for pattern, name, replacement in SENSITIVE_PATTERNS:
                sanitized = re.sub(pattern, replacement, sanitized)
            return sanitized
        if isinstance(response, dict):
            return {k: _sanitize_response(v) for k, v in response.items()}
        if isinstance(response, list):
            return [_sanitize_response(item) for item in response]
        return response


def get_validation_stats() -> dict[str, dict[str, int]]:
    """获取校验统计信息"""
    return dict(_validation_stats)
