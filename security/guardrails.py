"""安全护栏（Guardrails）

Agent 运行时的实时安全防线，在执行链路的三个关键节点嵌入检查：
  - 输入护栏: Prompt 注入检测（4层防御）、PII 泄露检测（深度检测）
  - 工具调用护栏: 权限校验、敏感操作确认
  - 输出护栏: 数据脱敏、合规检查

与架构文档 7.4 节对齐。

注入检测集成 injection_detection.py 四层防御：
  - 第一层：规则引擎（正则模式匹配 + 启发式规则）
  - 第二层：语义分析（文本特征统计 + 结构异常检测）
  - 第三层：AI 检测（基于 LLM 的注入意图判断，条件触发）
  - 第四层：上下文一致性（对话历史与当前输入的语义偏差检测）

PII 检测集成 pii_detection.py 深度检测：
  - 快速路径：desensitize.has_pii 快速判断
  - 深度路径：pii_detection.detect_pii 11种PII + 分级脱敏
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


# ==================== Prompt 注入检测模式（简易版，作为降级方案） ====================

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


async def check_input_guardrails(
    content: str,
    user_roles: list[str] | None = None,
    conversation_history: list[dict] | None = None,
) -> GuardrailResult:
    """输入护栏检查

    检查用户输入是否包含：
    1. Prompt 注入攻击（4层防御：规则引擎 + 启发式 + AI检测 + 上下文一致性）
    2. PII 信息泄露（快速检测 + 深度检测）
    3. 非办公话题

    Args:
        content: 用户输入内容
        user_roles: 用户角色列表
        conversation_history: 对话历史（用于上下文一致性检测）

    Returns:
        GuardrailResult 检查结果
    """
    checks: list[dict[str, Any]] = []

    # 1. Prompt 注入检测（增强版4层防御）
    injection_result = await _check_injection_enhanced(content, conversation_history)
    checks.append(injection_result["check_entry"])

    if injection_result["action"] == "block":
        logger.warning("Prompt 注入攻击被拦截: content=%s", content[:100])
        return GuardrailResult(
            passed=False,
            action=GuardrailAction.BLOCK,
            reason=injection_result["reason"],
            checks=checks,
        )

    if injection_result["action"] == "redact":
        checks.append({"check": "prompt_injection", "result": "redacted"})
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.REDACT,
            reason=injection_result["reason"],
            redacted_content=injection_result.get("sanitized_content", content),
            checks=checks,
        )

    # 2. PII 泄露检测（双模式：快速检测 + 深度检测）
    pii_result = _check_pii_enhanced(content, user_roles)
    if pii_result["action"] == "redact":
        checks.append(pii_result["check_entry"])
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.REDACT,
            reason=pii_result["reason"],
            redacted_content=pii_result["redacted_content"],
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


async def _check_injection_enhanced(
    content: str,
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    """增强版注入检测（4层防御）

    调用 injection_detection.detect_injection 进行多层检测，
    不可用时降级到简易正则检测。

    Args:
        content: 用户输入
        conversation_history: 对话历史

    Returns:
        包含 action、reason、check_entry、sanitized_content 的字典
    """
    try:
        from security.injection_detection import detect_injection

        result = await detect_injection(
            content,
            conversation_history=conversation_history,
            enable_ai_detection=False,
        )

        action = "pass"
        reason = ""
        if result.is_injection:
            if result.action == "block":
                action = "block"
                reason = f"检测到 Prompt 注入攻击（威胁等级: {result.threat_level.value}，评分: {result.overall_score:.2f}），请求已被拦截"
            elif result.action == "redact":
                action = "redact"
                reason = f"检测到可疑输入（威胁等级: {result.threat_level.value}），已自动净化"
            elif result.action == "warn":
                action = "pass"
                reason = f"输入存在轻微风险（威胁等级: {result.threat_level.value}），已放行"

        detection_layers = [d.layer.value for d in result.detections]
        return {
            "action": action,
            "reason": reason,
            "sanitized_content": result.sanitized_content if action == "redact" else None,
            "check_entry": {
                "check": "prompt_injection",
                "result": action if action != "pass" else "pass",
                "threat_level": result.threat_level.value,
                "overall_score": result.overall_score,
                "detection_layers": detection_layers,
            },
        }

    except Exception as e:
        logger.warning("增强版注入检测失败，降级到简易检测: %s", e)
        return _check_injection_simple(content)


def _check_injection_simple(content: str) -> dict[str, Any]:
    """简易注入检测（降级方案）

    当增强版检测不可用时，使用简易正则检测。

    Args:
        content: 用户输入

    Returns:
        包含 action、reason、check_entry 的字典
    """
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(content):
            return {
                "action": "block",
                "reason": "检测到潜在的 Prompt 注入攻击，请求已被拦截",
                "check_entry": {
                    "check": "prompt_injection",
                    "result": "blocked",
                    "pattern": pattern.pattern,
                    "mode": "simple_fallback",
                },
            }

    return {
        "action": "pass",
        "reason": "",
        "check_entry": {
            "check": "prompt_injection",
            "result": "pass",
            "mode": "simple_fallback",
        },
    }


def _check_pii_enhanced(
    content: str,
    user_roles: list[str] | None = None,
) -> dict[str, Any]:
    """增强版 PII 检测（双模式）

    快速路径：先用 desensitize.has_pii 快速判断
    深度路径：用 pii_detection.detect_pii 获取详细结果和分级脱敏

    Args:
        content: 待检测文本
        user_roles: 用户角色列表

    Returns:
        包含 action、reason、redacted_content、check_entry 的字典
    """
    if not has_pii(content):
        return {
            "action": "pass",
            "reason": "",
            "redacted_content": None,
            "check_entry": {"check": "pii_leakage", "result": "pass"},
        }

    try:
        from security.pii_detection import detect_pii as deep_detect_pii

        deep_result = deep_detect_pii(content)
        if deep_result.has_pii:
            pii_types = list(deep_result.summary.keys())
            return {
                "action": "redact",
                "reason": f"输入包含敏感信息({', '.join(pii_types)})，已自动脱敏",
                "redacted_content": deep_result.redacted_content,
                "check_entry": {
                    "check": "pii_leakage",
                    "result": "redacted",
                    "pii_types": pii_types,
                    "detection_count": len(deep_result.detections),
                    "summary": deep_result.summary,
                    "mode": "deep_detection",
                },
            }
    except Exception as e:
        logger.warning("深度 PII 检测失败，降级到基础脱敏: %s", e)

    pii_detections = detect_pii(content)
    if pii_detections:
        pii_types = list({d.pii_type for d in pii_detections})
        redacted = desensitize_content(content, user_roles)
        return {
            "action": "redact",
            "reason": f"输入包含敏感信息({', '.join(pii_types)})，已自动脱敏",
            "redacted_content": redacted,
            "check_entry": {
                "check": "pii_leakage",
                "result": "redacted",
                "pii_types": pii_types,
                "mode": "basic_fallback",
            },
        }

    return {
        "action": "pass",
        "reason": "",
        "redacted_content": None,
        "check_entry": {"check": "pii_leakage", "result": "pass"},
    }


async def check_tool_call_guardrails(
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

    注意：此方法为异步方法，因为配额检查和审批单创建需要异步访问 Redis。

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
        try:
            from observability.metrics import record_guardrail_block
            record_guardrail_block("tool_whitelist", "block")
        except Exception:
            pass
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
        try:
            from observability.metrics import record_guardrail_block
            record_guardrail_block("permission", "block")
        except Exception:
            pass
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
            try:
                from observability.metrics import record_guardrail_block
                record_guardrail_block("sensitive_action", "confirm")
            except Exception:
                pass
            approval_id = await _create_approval_for_sensitive_action(
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

    # 4. 资源冲突检测（分布式锁）
    conflict_result = await _check_resource_conflict(tool_name, tool_input)
    checks.append(conflict_result["check_entry"])
    if conflict_result["action"] == "confirm":
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.CONFIRM,
            reason=conflict_result["reason"],
            checks=checks,
        )

    # 5. 工具参数深度校验
    if tool_schema and tool_input is not None:
        schema_result = _check_tool_schema(tool_name, tool_input, tool_schema)
        checks.append(schema_result["check_entry"])
        if not schema_result["passed"]:
            logger.warning("工具参数校验失败: tool=%s errors=%s", tool_name, schema_result["errors"])
            try:
                from observability.metrics import record_guardrail_block
                record_guardrail_block("tool_schema", "block")
            except Exception:
                pass
            return GuardrailResult(
                passed=False,
                action=GuardrailAction.BLOCK,
                reason=f"工具参数校验失败: {'; '.join(schema_result['errors'])}",
                checks=checks,
            )

    # 6. 工具调用配额检查
    quota_result = await _check_tool_quota(tool_name)
    checks.append(quota_result["check_entry"])
    if quota_result["action"] == "warn":
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.WARN,
            reason=quota_result["reason"],
            checks=checks,
        )

    return GuardrailResult(passed=True, checks=checks)


async def _check_resource_conflict(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """资源冲突检测

    从工具调用参数中提取资源标识，检查该资源是否已被其他操作锁定。
    对写操作（send/approve/reject/delete 等）自动检测，读操作不检测。

    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数

    Returns:
        包含 passed、action、reason、check_entry 的字典
    """
    if not tool_input:
        return {
            "passed": True,
            "action": "pass",
            "reason": "",
            "check_entry": {"check": "resource_conflict", "result": "pass", "note": "无参数跳过"},
        }

    try:
        from agent.core.distributed_lock import extract_resource_key, DistributedLock

        resource_key_mapping = _get_tool_resource_key_mapping()
        lock_key = extract_resource_key(tool_name, tool_input, resource_key_mapping)

        if lock_key is None:
            return {
                "passed": True,
                "action": "pass",
                "reason": "",
                "check_entry": {"check": "resource_conflict", "result": "pass", "note": "非写操作跳过"},
            }

        lock = DistributedLock(lock_key=lock_key, ttl_ms=30000, retry_count=0)
        is_locked = await lock.is_locked()

        if is_locked:
            holder = await lock.get_holder()
            return {
                "passed": True,
                "action": "confirm",
                "reason": f"资源 {lock_key} 正在被其他操作使用(holder={holder})，请稍后重试",
                "check_entry": {
                    "check": "resource_conflict",
                    "result": "conflict",
                    "lock_key": lock_key,
                    "holder": holder,
                },
            }

        return {
            "passed": True,
            "action": "pass",
            "reason": "",
            "check_entry": {
                "check": "resource_conflict",
                "result": "pass",
                "lock_key": lock_key,
            },
        }

    except Exception as e:
        logger.warning("资源冲突检测失败（非致命）: tool=%s error=%s", tool_name, e)
        return {
            "passed": True,
            "action": "pass",
            "reason": "",
            "check_entry": {"check": "resource_conflict", "result": "pass", "note": f"检测失败跳过: {e}"},
        }


def _get_tool_resource_key_mapping() -> dict[str, str]:
    """获取工具参数到资源标识的映射配置

    从全局配置中读取 TOOL_RESOURCE_KEY_MAPPING，
    定义工具参数名到资源标识字段的映射关系。

    Returns:
        映射字典，key 为 "资源:操作"，value 为参数名
    """
    try:
        from agent.core.config import get_settings
        settings = get_settings()
        return getattr(settings, "tool_resource_key_mapping", {})
    except Exception:
        return {}


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


async def _check_tool_quota(tool_name: str) -> dict[str, Any]:
    """工具调用配额检查

    从 Redis 读取该工具当天的调用次数，超限则警告。
    异步方法，复用全局统一 Redis 连接管理器。

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
        from agent.core.redis_manager import get_redis_client

        redis = await get_redis_client()
        if redis is None:
            return {
                "passed": True,
                "action": "pass",
                "reason": "",
                "check_entry": {"check": "tool_quota", "result": "pass", "note": "Redis不可用跳过"},
            }

        date_key = time.strftime("%Y-%m-%d")
        redis_key = f"tool_quota:{tool_name}:{date_key}"
        current = await redis.get(redis_key)
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

    except Exception:
        return {
            "passed": True,
            "action": "pass",
            "reason": "",
            "check_entry": {"check": "tool_quota", "result": "pass", "note": "Redis不可用跳过"},
        }


async def check_output_guardrails(
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

    # 1. 数据泄露检测与脱敏（使用增强版深度检测）
    pii_result = _check_pii_enhanced(content, user_roles)
    if pii_result["action"] == "redact":
        check_entry = pii_result["check_entry"].copy()
        check_entry["check"] = "data_leakage"
        checks.append(check_entry)
        return GuardrailResult(
            passed=True,
            action=GuardrailAction.REDACT,
            reason=pii_result["reason"],
            redacted_content=pii_result["redacted_content"],
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
        hallucination_result = await _check_hallucination(content, knowledge_context, query)
        checks.append(hallucination_result["check_entry"])
        if not hallucination_result["passed"]:
            return GuardrailResult(
                passed=True,
                action=GuardrailAction.WARN,
                reason=hallucination_result["reason"],
                checks=checks,
            )

    return GuardrailResult(passed=True, checks=checks)


async def _check_hallucination(
    content: str,
    knowledge_context: list[str],
    query: str,
) -> dict[str, Any]:
    """异步执行幻觉检测（在输出护栏中调用）

    幻觉检测不阻断正常输出，仅附加警告和置信度。
    直接 await HallucinationDetector.check()，避免在已有事件循环中使用
    asyncio.run() 导致的 RuntimeError。

    Args:
        content: Agent 输出内容
        knowledge_context: 知识库检索到的原文片段列表
        query: 用户原始查询

    Returns:
        包含 passed、reason、check_entry 的字典
    """
    try:
        from security.hallucination_detection import HallucinationDetector

        detector = HallucinationDetector()
        result = await detector.check(content, knowledge_context, query)

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


async def _create_approval_for_sensitive_action(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> str | None:
    """为敏感操作自动创建审批单

    在 Guardrails 检测到敏感操作需要确认时调用。
    审批单创建失败不阻断主流程，仅记录警告。

    异步方法，直接调用审批流管理器创建审批单，
    不再使用 asyncio.run() 或线程池绕过异步限制。

    Args:
        tool_name: 敏感操作工具名称
        tool_input: 工具输入参数

    Returns:
        审批单ID，创建失败时返回 None
    """
    try:
        from agent.core.approval_flow import get_approval_flow_manager

        # 获取当前上下文信息
        session_id = ""
        user_id = ""
        try:
            from agent.core.session_manager import get_session_manager
            mgr = await get_session_manager()
            # 尝试从上下文获取 session_id 和 user_id
            try:
                from security.tenant import get_current_tenant_id
                get_current_tenant_id()
            except Exception:
                pass
        except Exception:
            pass

        # 从 SENSITIVE_ACTIONS 获取审批链配置
        approval_chain: list[dict[str, Any]] = []
        sensitive_config = SENSITIVE_ACTIONS.get(tool_name, {})
        require_roles = sensitive_config.get("require_role", [])
        for role in require_roles:
            approval_chain.append({"role": str(role), "name": ""})

        approval_flow_mgr = get_approval_flow_manager()
        approval = await approval_flow_mgr.create_approval(
            session_id=session_id,
            user_id=user_id,
            agent_name="",
            tool_name=tool_name,
            tool_input=tool_input or {},
            reason=f"敏感操作: {tool_name}",
            approval_chain=approval_chain if approval_chain else None,
        )
        return approval.approval_id

    except Exception as e:
        logger.warning("创建审批单失败（非致命）: tool=%s error=%s", tool_name, e)
        return None
