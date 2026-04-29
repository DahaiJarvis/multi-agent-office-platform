"""审计日志

全链路审计记录，与架构文档 7.6 节对齐。
记录用户操作、Agent 决策、工具调用、护栏检查等关键事件，
确保所有操作可追溯、可审计。
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 专用审计日志记录器（与业务日志分离）
_audit_logger = logging.getLogger("audit")


class AuditEvent(BaseModel):
    """审计事件"""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    user_id: str = ""
    tenant_id: str = ""
    user_role: str = ""
    channel: str = ""
    event_type: str = ""  # request / agent_call / tool_call / guardrail / auth
    intent: str = ""
    agent_name: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    guardrail_checks: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: float = 0
    risk_level: str = "low"  # low / medium / high
    status: str = ""  # success / blocked / error
    detail: str = ""


def _determine_risk_level(event: AuditEvent) -> str:
    """根据事件内容判定风险等级

    Args:
        event: 审计事件

    Returns:
        风险等级: low / medium / high
    """
    # 高风险：护栏拦截
    for check in event.guardrail_checks:
        if check.get("result") in ("blocked", "confirm_required"):
            return "high"

    # 中风险：敏感操作
    if event.tool_name and _is_sensitive_tool(event.tool_name):
        return "medium"

    # 中风险：认证失败
    if event.event_type == "auth" and event.status == "failed":
        return "medium"

    return "low"


def _is_sensitive_tool(tool_name: str) -> bool:
    """判断工具是否为敏感工具"""
    sensitive_prefixes = ["approval:approve", "finance:", "data:delete", "email:send_all"]
    return any(tool_name.startswith(prefix) for prefix in sensitive_prefixes)


def _map_event_type(event_type: str):
    """将安全审计事件类型映射为集中化审计事件类型

    Args:
        event_type: 安全审计事件类型字符串

    Returns:
        AuditEventType 枚举值
    """
    from agent.core.audit import AuditEventType

    event_type_map = {
        "request": AuditEventType.AGENT,
        "agent_call": AuditEventType.AGENT,
        "tool_call": AuditEventType.DATA,
        "guardrail": AuditEventType.SYSTEM,
        "auth": AuditEventType.AUTH,
    }
    return event_type_map.get(event_type, AuditEventType.SYSTEM)


def _build_audit_action(event: AuditEvent) -> str:
    """根据审计事件构建操作动作描述

    Args:
        event: 安全审计事件

    Returns:
        操作动作字符串
    """
    if event.tool_name:
        return f"tool_call:{event.tool_name}"
    if event.event_type == "guardrail":
        return f"guardrail:{event.status}"
    return event.event_type


def _build_audit_detail(event: AuditEvent) -> dict[str, Any]:
    """根据审计事件构建详情字典

    Args:
        event: 安全审计事件

    Returns:
        详情字典
    """
    detail: dict[str, Any] = {
        "risk_level": event.risk_level,
        "status": event.status,
        "latency_ms": event.latency_ms,
    }
    if event.detail:
        detail["detail"] = event.detail
    if event.token_usage:
        detail["token_usage"] = event.token_usage
    if event.guardrail_checks:
        detail["guardrail_checks"] = event.guardrail_checks
    return detail


def record_audit(event: AuditEvent) -> None:
    """记录审计事件

    将审计事件同时写入:
    1. 专用审计日志（Python logging，独立文件）
    2. 集中化审计系统（Redis 缓冲 -> PostgreSQL 持久化）

    多租户模式下，自动从上下文填充 tenant_id。

    Args:
        event: 审计事件
    """
    # 自动从上下文填充 tenant_id
    if not event.tenant_id:
        try:
            from security.tenant import get_current_tenant_id
            event.tenant_id = get_current_tenant_id() or ""
        except Exception:
            pass

    event.risk_level = _determine_risk_level(event)
    event_json = event.model_dump_json()
    _audit_logger.info(event_json)

    # 直接写入集中化审计系统
    try:
        import asyncio
        from agent.core.audit import audit_log

        audit_type = _map_event_type(event.event_type)
        action = _build_audit_action(event)
        detail = _build_audit_detail(event)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(audit_log(
                event_type=audit_type,
                action=action,
                user_id=event.user_id,
                session_id=event.trace_id,
                agent_name=event.agent_name,
                resource=event.tool_name,
                detail=detail,
                request_id=event.event_id,
            ))
        except RuntimeError:
            pass
    except Exception as e:
        logger.debug("写入集中化审计失败（非致命）: %s", e)


def record_request_audit(
    trace_id: str,
    user_id: str,
    user_role: str,
    channel: str,
    intent: str,
    agent_name: str,
    status: str,
    latency_ms: float,
    guardrail_checks: list[dict[str, Any]] | None = None,
    token_usage: dict[str, int] | None = None,
) -> None:
    """记录请求级别的审计事件

    Args:
        trace_id: 追踪ID
        user_id: 用户ID
        user_role: 用户角色
        channel: 接入渠道
        intent: 意图
        agent_name: Agent 名称
        status: 状态
        latency_ms: 延迟（毫秒）
        guardrail_checks: 护栏检查结果
        token_usage: Token 使用量
    """
    event = AuditEvent(
        trace_id=trace_id,
        user_id=user_id,
        user_role=user_role,
        channel=channel,
        event_type="request",
        intent=intent,
        agent_name=agent_name,
        status=status,
        latency_ms=latency_ms,
        guardrail_checks=guardrail_checks or [],
        token_usage=token_usage or {},
    )
    record_audit(event)


def record_tool_call_audit(
    trace_id: str,
    user_id: str,
    user_role: str,
    agent_name: str,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_output: dict[str, Any] | None = None,
    status: str = "success",
    latency_ms: float = 0,
    guardrail_checks: list[dict[str, Any]] | None = None,
) -> None:
    """记录工具调用级别的审计事件

    Args:
        trace_id: 追踪ID
        user_id: 用户ID
        user_role: 用户角色
        agent_name: Agent 名称
        tool_name: 工具名称
        tool_input: 工具输入
        tool_output: 工具输出
        status: 状态
        latency_ms: 延迟（毫秒）
        guardrail_checks: 护栏检查结果
    """
    event = AuditEvent(
        trace_id=trace_id,
        user_id=user_id,
        user_role=user_role,
        event_type="tool_call",
        agent_name=agent_name,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        status=status,
        latency_ms=latency_ms,
        guardrail_checks=guardrail_checks or [],
    )
    record_audit(event)


def record_auth_audit(
    trace_id: str,
    user_id: str,
    channel: str,
    status: str,
    detail: str = "",
) -> None:
    """记录认证级别的审计事件

    Args:
        trace_id: 追踪ID
        user_id: 用户ID
        channel: 接入渠道
        status: 状态 (success/failed)
        detail: 详情
    """
    event = AuditEvent(
        trace_id=trace_id,
        user_id=user_id,
        channel=channel,
        event_type="auth",
        status=status,
        detail=detail,
    )
    record_audit(event)


def record_guardrail_audit(
    trace_id: str,
    user_id: str,
    user_role: str,
    guardrail_type: str,
    check_results: list[dict[str, Any]],
    action: str,
    reason: str = "",
) -> None:
    """记录护栏检查的审计事件

    Args:
        trace_id: 追踪ID
        user_id: 用户ID
        user_role: 用户角色
        guardrail_type: 护栏类型 (input/tool_call/output)
        check_results: 检查结果
        action: 动作 (pass/block/redact/confirm/warn)
        reason: 原因
    """
    event = AuditEvent(
        trace_id=trace_id,
        user_id=user_id,
        user_role=user_role,
        event_type="guardrail",
        guardrail_checks=check_results,
        status=action,
        detail=f"[{guardrail_type}] {reason}",
    )
    record_audit(event)
