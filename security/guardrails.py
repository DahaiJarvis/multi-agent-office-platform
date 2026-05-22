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

from security.permission import check_permission, SENSITIVE_ACTIONS
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
    tool_schema: dict[str, Any] | None = None,
) -> GuardrailResult:
    """工具调用护栏检查

    检查：
    1. 工具白名单校验（是否在 MCP 注册表中）
    2. 用户是否有权调用该工具
    3. 敏感操作是否需要二次确认
    4. 工具参数深度校验（基于 JSON Schema）
    5. 工具调用配额检查

    Args:
        user_role: 用户角色
        tool_name: 工具名称，格式为 "资源:操作"
        tool_input: 工具输入参数
        tool_schema: 工具参数 JSON Schema（可选，用于参数校验）

    Returns:
        GuardrailResult 检查结果
    """
    checks: list[dict[str, Any]] = []

    # 1. 工具白名单校验
    whitelist_result = _check_tool_whitelist(tool_name)
    checks.append(whitelist_result["check_entry"])
    if not whitelist_result["passed"]:
        logger.warning("工具调用白名单拦截: tool=%s", tool_name)
        return GuardrailResult(
            passed=False,
            action=GuardrailAction.BLOCK,
            reason=whitelist_result["reason"],
            checks=checks,
        )

    # 2. 权限校验
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

    # 3. 敏感操作确认（自动创建审批单）
    if perm_result.sensitive:
        checks.append({
            "check": "sensitive_action",
            "result": "confirm_required" if perm_result.require_confirm else "pass",
            "action": tool_name,
        })

        if perm_result.require_confirm:
            # 自动创建审批单
            approval_id = _create_approval_for_sensitive_action(
                tool_name=tool_name,
                tool_input=tool_input,
            )
            approval_msg = f"操作 {tool_name} 为敏感操作，需要审批"
            if approval_id:
                approval_msg += f"，审批单号: {approval_id}"

            return GuardrailResult(
                passed=True,
                action=GuardrailAction.CONFIRM,
                reason=approval_msg,
                checks=checks,
            )

    checks.append({"check": "sensitive_action", "result": "pass"})

    # 4. 工具参数深度校验
    if tool_schema and tool_input is not None:
        schema_result = _check_tool_schema(tool_name, tool_input, tool_schema)
        checks.append(schema_result["check_entry"])
        if not schema_result["passed"]:
            logger.warning("工具参数校验失败: tool=%s errors=%s", tool_name, schema_result["errors"])
            return GuardrailResult(
                passed=False,
                action=GuardrailAction.BLOCK,
                reason=f"工具参数校验失败: {'; '.join(schema_result['errors'])}",
                checks=checks,
            )

    # 5. 工具调用配额检查
    quota_result = _check_tool_quota(tool_name)
    checks.append(quota_result["check_entry"])
    if quota_result["action"] == "warn":
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.WARN,
            reason=quota_result["reason"],
            checks=checks,
        )

    return GuardrailResult(passed=True, checks=checks)


def _check_tool_whitelist(tool_name: str) -> dict[str, Any]:
    """工具白名单校验

    检查工具是否在 MCP 注册表或原生工具注册表中注册。
    对于 "资源:操作" 格式的工具名，只校验资源部分是否在注册表中。
    对于 "native_" 前缀的工具名，校验原生工具注册表。

    Args:
        tool_name: 工具名称

    Returns:
        包含 passed、reason、check_entry 的字典
    """
    from agent.core.mcp_integration import MCP_SERVER_REGISTRY

    # 提取资源前缀（如 "approval:approve" -> "approval"）
    resource_prefix = tool_name.split(":")[0] if ":" in tool_name else tool_name

    registered_in_mcp = resource_prefix in MCP_SERVER_REGISTRY

    # 检查原生工具注册表
    registered_in_native = False
    tool_source = "unknown"
    if tool_name.startswith("native_"):
        try:
            from agent.tools.registry import get_native_tool_registry
            native_registry = get_native_tool_registry()
            registered_in_native = native_registry.get_meta(tool_name) is not None
        except Exception:
            pass

    registered = registered_in_mcp or registered_in_native

    if registered:
        if registered_in_native:
            tool_source = "native"
        elif registered_in_mcp:
            tool_source = "mcp"

    return {
        "passed": registered,
        "reason": "" if registered else f"工具 {tool_name} 未在注册表中注册，不允许调用",
        "check_entry": {
            "check": "tool_whitelist",
            "result": "pass" if registered else "blocked",
            "tool_name": tool_name,
            "resource_prefix": resource_prefix,
            "source": tool_source,
        },
    }


def _check_tool_schema(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_schema: dict[str, Any],
) -> dict[str, Any]:
    """工具参数深度校验

    基于 JSON Schema 校验工具输入参数的类型、必填项和枚举值。

    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数
        tool_schema: 工具参数 JSON Schema

    Returns:
        包含 passed、errors、check_entry 的字典
    """
    errors: list[str] = []
    properties = tool_schema.get("properties", {})
    required_fields = tool_schema.get("required", [])

    # 必填项校验
    for field_name in required_fields:
        if field_name not in tool_input or tool_input[field_name] is None:
            errors.append(f"必填参数 '{field_name}' 缺失")

    # 类型和约束校验
    for field_name, field_value in tool_input.items():
        if field_name not in properties:
            continue

        field_schema = properties[field_name]
        field_type = field_schema.get("type", "")

        # 类型校验
        if field_type == "string" and not isinstance(field_value, str):
            errors.append(f"参数 '{field_name}' 应为字符串类型")
        elif field_type == "number" and not isinstance(field_value, (int, float)):
            errors.append(f"参数 '{field_name}' 应为数字类型")
        elif field_type == "integer" and not isinstance(field_value, int):
            errors.append(f"参数 '{field_name}' 应为整数类型")
        elif field_type == "boolean" and not isinstance(field_value, bool):
            errors.append(f"参数 '{field_name}' 应为布尔类型")

        # 枚举值校验
        enum_values = field_schema.get("enum")
        if enum_values and field_value not in enum_values:
            errors.append(f"参数 '{field_name}' 的值 '{field_value}' 不在允许范围 {enum_values} 中")

        # 字符串长度校验
        if isinstance(field_value, str):
            max_length = field_schema.get("maxLength")
            if max_length and len(field_value) > max_length:
                errors.append(f"参数 '{field_name}' 长度超过上限 {max_length}")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "check_entry": {
            "check": "tool_schema",
            "result": "pass" if len(errors) == 0 else "blocked",
            "tool_name": tool_name,
            "error_count": len(errors),
        },
    }


def _check_tool_quota(tool_name: str) -> dict[str, Any]:
    """工具调用配额检查

    从 Redis 读取该工具当天的调用次数，超限则警告。

    Args:
        tool_name: 工具名称

    Returns:
        包含 passed、action、reason、check_entry 的字典
    """
    import time

    try:
        from agent.core.config import get_settings
        settings = get_settings()
        quota = settings.tool_daily_quota
    except Exception:
        quota = 1000

    try:
        import redis.asyncio as aioredis
        from agent.core.config import get_settings

        settings = get_settings()
        r = aioredis.from_url(settings.redis_url, decode_responses=True)

        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 在异步上下文中，无法同步调用，跳过配额检查
            return {
                "passed": True,
                "action": "pass",
                "reason": "",
                "check_entry": {"check": "tool_quota", "result": "pass", "note": "异步上下文跳过"},
            }

        # 同步上下文：直接查询
        async def _check() -> dict[str, Any]:
            date_key = time.strftime("%Y-%m-%d")
            redis_key = f"tool_quota:{tool_name}:{date_key}"
            current = await r.get(redis_key)
            count = int(current) if current else 0

            if count >= quota:
                return {
                    "passed": True,
                    "action": "warn",
                    "reason": f"工具 {tool_name} 今日调用次数已达配额上限 ({quota})",
                    "check_entry": {
                        "check": "tool_quota",
                        "result": "warn",
                        "tool_name": tool_name,
                        "current": count,
                        "quota": quota,
                    },
                }

            return {
                "passed": True,
                "action": "pass",
                "reason": "",
                "check_entry": {
                    "check": "tool_quota",
                    "result": "pass",
                    "tool_name": tool_name,
                    "current": count,
                    "quota": quota,
                },
            }

        result = asyncio.run(_check())
        return result

    except Exception:
        # Redis 不可用时跳过配额检查
        return {
            "passed": True,
            "action": "pass",
            "reason": "",
            "check_entry": {"check": "tool_quota", "result": "pass", "note": "Redis不可用跳过"},
        }


def check_output_guardrails(
    content: str,
    user_roles: list[str] | None = None,
    knowledge_context: list[str] | None = None,
    query: str = "",
) -> GuardrailResult:
    """输出护栏检查

    检查 Agent 输出是否包含：
    1. 敏感数据泄露（PII 脱敏）
    2. 不合规内容
    3. 幻觉检测（仅知识库场景）

    Args:
        content: Agent 输出内容
        user_roles: 用户角色列表
        knowledge_context: 知识库检索到的原文片段列表（可选，传入时触发幻觉检测）
        query: 用户原始查询（可选，用于完整性检查）

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

    # 3. 幻觉检测（仅知识库场景）
    if knowledge_context:
        hallucination_result = _check_hallucination_sync(content, knowledge_context, query)
        checks.append(hallucination_result["check_entry"])
        if not hallucination_result["passed"]:
            return GuardrailResult(
                passed=True,
                action=GuardrailAction.WARN,
                reason=hallucination_result["reason"],
                checks=checks,
            )

    return GuardrailResult(passed=True, checks=checks)


def _check_hallucination_sync(
    content: str,
    knowledge_context: list[str],
    query: str,
) -> dict[str, Any]:
    """同步执行幻觉检测（在输出护栏中调用）

    幻觉检测不阻断正常输出，仅附加警告和置信度。

    Args:
        content: Agent 输出内容
        knowledge_context: 知识库检索到的原文片段列表
        query: 用户原始查询

    Returns:
        包含 passed、reason、check_entry 的字典
    """
    import asyncio

    try:
        from security.hallucination_detection import HallucinationDetector

        detector = HallucinationDetector()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 在异步上下文中，使用 create_task
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    detector.check(content, knowledge_context, query),
                )
                result = future.result(timeout=10)
        else:
            result = asyncio.run(detector.check(content, knowledge_context, query))

        return {
            "passed": result.passed,
            "reason": (
                f"输出质量检查: 事实一致性={result.factuality_score:.0%}, "
                f"引用={'有' if result.has_citations else '无'}, "
                f"完整性={result.completeness_score:.0%}"
                + (f", 警告: {'; '.join(result.warnings)}" if result.warnings else "")
            ),
            "check_entry": {
                "check": "hallucination",
                "result": "pass" if result.passed else "warn",
                "factuality_score": result.factuality_score,
                "has_citations": result.has_citations,
                "completeness_score": result.completeness_score,
                "confidence": result.confidence,
            },
        }

    except Exception as e:
        logger.warning("幻觉检测执行失败（非致命）: %s", e)
        return {
            "passed": True,
            "reason": "",
            "check_entry": {
                "check": "hallucination",
                "result": "pass",
                "note": f"检测失败跳过: {e}",
            },
        }


def _create_approval_for_sensitive_action(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> str | None:
    """为敏感操作自动创建审批单

    在 Guardrails 检测到敏感操作需要确认时调用。
    审批单创建失败不阻断主流程，仅记录警告。

    Args:
        tool_name: 敏感操作工具名称
        tool_input: 工具输入参数

    Returns:
        审批单ID，创建失败时返回 None
    """
    try:
        import asyncio
        from agent.core.approval_flow import get_approval_flow_manager

        # 获取当前上下文信息
        session_id = ""
        user_id = ""
        try:
            from agent.core.session_manager import get_session_manager

            async def _get_context() -> tuple[str, str]:
                await get_session_manager()
                return "", ""

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                pass
            else:
                session_id, user_id = asyncio.run(_get_context())
        except Exception:
            pass

        # 从 SENSITIVE_ACTIONS 获取审批链配置
        approval_chain: list[dict[str, Any]] = []
        sensitive_config = SENSITIVE_ACTIONS.get(tool_name, {})
        require_roles = sensitive_config.get("require_role", [])
        for role in require_roles:
            approval_chain.append({"role": str(role), "name": ""})

        async def _create() -> str | None:
            mgr = get_approval_flow_manager()
            approval = await mgr.create_approval(
                session_id=session_id,
                user_id=user_id,
                agent_name="",
                tool_name=tool_name,
                tool_input=tool_input or {},
                reason=f"敏感操作: {tool_name}",
                approval_chain=approval_chain if approval_chain else None,
            )
            return approval.approval_id

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 异步上下文：使用线程池执行
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _create())
                return future.result(timeout=5)
        else:
            return asyncio.run(_create())

    except Exception as e:
        logger.warning("创建审批单失败（非致命）: tool=%s error=%s", tool_name, e)
        return None
