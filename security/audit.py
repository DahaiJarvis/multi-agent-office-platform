"""审计日志

全链路审计记录，与架构文档 7.6 节对齐。
记录用户操作、Agent 决策、工具调用、护栏检查等关键事件，
确保所有操作可追溯、可审计。
"""

import json
import logging
import time
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


def record_audit(event: AuditEvent) -> None:
    """记录审计事件

    将审计事件以结构化 JSON 格式写入专用审计日志。
    审计日志应配置独立的日志文件和保留策略。

    Args:
        event: 审计事件
    """
    event.risk_level = _determine_risk_level(event)
    event_json = event.model_dump_json()
    _audit_logger.info(event_json)


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
