"""安全护栏（Guardrails）

Agent 运行时的实时安全防线，在执行链路的三个关键节点嵌入检查：
  - 输入护栏: Prompt 注入检测、PII 泄露检测
  - 工具调用护栏: 权限校验、敏感操作确认
  - 输出护栏: 数据脱敏、合规检查

与架构文档 7.4 节对齐。
"""

import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel

from security.permission import check_permission
from security.desensitize import desensitize_content, has_pii, detect_pii

logger = logging.getLogger(__name__)


class GuardrailAction(str, Enum):
    """护栏动作"""

    PASS = "pass"
    BLOCK = "block"
    REDACT = "redact"
    CONFIRM = "confirm"
    WARN = "warn"


class GuardrailResult(BaseModel):
    """护栏检查结果"""

    passed: bool
    action: GuardrailAction = GuardrailAction.PASS
    reason: str = ""
    redacted_content: str | None = None
    checks: list[dict[str, Any]] = []


# ==================== Prompt 注入检测模式 ====================

PROMPT_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|above|all)\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|previous)", re.IGNORECASE),
    re.compile(r"disregard\s+(your|previous|above)", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"override\s+(previous|default|safety)", re.IGNORECASE),
]

# 非办公话题关键词
OFF_TOPIC_KEYWORDS = [
    "赌博", "博彩", "色情", "暴力", "毒品",
    "hack", "exploit", "malware", "phishing",
]


def check_input_guardrails(
    content: str,
    user_roles: list[str] | None = None,
) -> GuardrailResult:
    """输入护栏检查

    检查用户输入是否包含：
    1. Prompt 注入攻击
    2. PII 信息泄露
    3. 非办公话题

    Args:
        content: 用户输入内容
        user_roles: 用户角色列表

    Returns:
        GuardrailResult 检查结果
    """
    checks: list[dict[str, Any]] = []

    # 1. Prompt 注入检测
    injection_detected = False
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(content):
            injection_detected = True
            checks.append({
                "check": "prompt_injection",
                "result": "blocked",
                "pattern": pattern.pattern,
            })
            break

    if not injection_detected:
        checks.append({"check": "prompt_injection", "result": "pass"})

    if injection_detected:
        logger.warning("Prompt 注入攻击被拦截: content=%s", content[:100])
        return GuardrailResult(
            passed=False,
            action=GuardrailAction.BLOCK,
            reason="检测到潜在的 Prompt 注入攻击，请求已被拦截",
            checks=checks,
        )

    # 2. PII 泄露检测
    pii_detections = detect_pii(content)
    if pii_detections:
        pii_types = list({d.pii_type for d in pii_detections})
        redacted = desensitize_content(content, user_roles)
        checks.append({
            "check": "pii_leakage",
            "result": "redacted",
            "pii_types": pii_types,
        })
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.REDACT,
            reason=f"输入包含敏感信息({', '.join(pii_types)})，已自动脱敏",
            redacted_content=redacted,
            checks=checks,
        )

    checks.append({"check": "pii_leakage", "result": "pass"})

    # 3. 非办公话题检测
    content_lower = content.lower()
    off_topic_found = [kw for kw in OFF_TOPIC_KEYWORDS if kw in content_lower]
    if off_topic_found:
        checks.append({
            "check": "off_topic",
            "result": "warn",
            "keywords": off_topic_found,
        })
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.WARN,
            reason="请求可能与办公场景无关，请确认",
            checks=checks,
        )

    checks.append({"check": "off_topic", "result": "pass"})

    return GuardrailResult(passed=True, checks=checks)


def check_tool_call_guardrails(
    user_role: str,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> GuardrailResult:
    """工具调用护栏检查

    检查：
    1. 用户是否有权调用该工具
    2. 敏感操作是否需要二次确认

    Args:
        user_role: 用户角色
        tool_name: 工具名称，格式为 "资源:操作"
        tool_input: 工具输入参数

    Returns:
        GuardrailResult 检查结果
    """
    checks: list[dict[str, Any]] = []

    # 1. 权限校验
    perm_result = check_permission(user_role, tool_name)
    checks.append({
        "check": "permission",
        "result": "pass" if perm_result.allowed else "blocked",
        "action": tool_name,
        "role": user_role,
    })

    if not perm_result.allowed:
        logger.warning("工具调用权限不足: role=%s tool=%s", user_role, tool_name)
        return GuardrailResult(
            passed=False,
            action=GuardrailAction.BLOCK,
            reason=perm_result.reason,
            checks=checks,
        )

    # 2. 敏感操作确认
    if perm_result.sensitive:
        checks.append({
            "check": "sensitive_action",
            "result": "confirm_required" if perm_result.require_confirm else "pass",
            "action": tool_name,
        })

        if perm_result.require_confirm:
            return GuardrailResult(
                passed=True,
                action=GuardrailAction.CONFIRM,
                reason=f"操作 {tool_name} 为敏感操作，需要用户确认",
                checks=checks,
            )

    checks.append({"check": "sensitive_action", "result": "pass"})

    return GuardrailResult(passed=True, checks=checks)


def check_output_guardrails(
    content: str,
    user_roles: list[str] | None = None,
) -> GuardrailResult:
    """输出护栏检查

    检查 Agent 输出是否包含：
    1. 敏感数据泄露（PII 脱敏）
    2. 不合规内容

    Args:
        content: Agent 输出内容
        user_roles: 用户角色列表

    Returns:
        GuardrailResult 检查结果
    """
    checks: list[dict[str, Any]] = []

    # 1. 数据泄露检测与脱敏
    if has_pii(content):
        redacted = desensitize_content(content, user_roles)
        pii_detections = detect_pii(content)
        pii_types = list({d.pii_type for d in pii_detections})
        checks.append({
            "check": "data_leakage",
            "result": "redacted",
            "pii_types": pii_types,
        })
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.REDACT,
            reason=f"输出包含敏感信息({', '.join(pii_types)})，已自动脱敏",
            redacted_content=redacted,
            checks=checks,
        )

    checks.append({"check": "data_leakage", "result": "pass"})

    # 2. 合规检查（基础实现：检测是否包含不应暴露的内部信息标记）
    compliance_patterns = [
        re.compile(r"\[INTERNAL\]", re.IGNORECASE),
        re.compile(r"\[CONFIDENTIAL\]", re.IGNORECASE),
        re.compile(r"\[SECRET\]", re.IGNORECASE),
    ]
    for pattern in compliance_patterns:
        if pattern.search(content):
            checks.append({
                "check": "compliance",
                "result": "blocked",
                "pattern": pattern.pattern,
            })
            return GuardrailResult(
                passed=False,
                action=GuardrailAction.BLOCK,
                reason="输出包含内部机密标记，已被拦截",
                checks=checks,
            )

    checks.append({"check": "compliance", "result": "pass"})

    return GuardrailResult(passed=True, checks=checks)
