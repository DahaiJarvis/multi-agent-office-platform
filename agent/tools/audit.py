"""原生工具审计日志

记录原生工具调用的审计信息，与现有审计体系对齐。
调用 security/audit.py 的 record_tool_call_audit()，确保格式一致。

记录内容：
  - tool_name: 工具名
  - agent_name: Agent 名
  - session_id: 会话 ID
  - user_id: 用户 ID
  - tool_input: 工具输入（敏感参数脱敏）
  - tool_output: 工具输出（截断至 500 字符）
  - duration_ms: 执行耗时
  - tool_source: "native"
  - permission_level: 权限级别
"""

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

OUTPUT_TRUNCATE_LENGTH = 500

SENSITIVE_PARAM_KEYS = {"password", "token", "secret", "api_key", "credential", "private_key"}

PATH_DESENSITIZE_PATTERN = re.compile(r"/home/[^/]+", re.IGNORECASE)
PATH_DESENSITIZE_PATTERN2 = re.compile(r"/Users/[^/]+", re.IGNORECASE)


def _desensitize_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """脱敏工具输入中的敏感参数

    Args:
        tool_input: 原始工具输入

    Returns:
        脱敏后的工具输入
    """
    if not tool_input:
        return tool_input

    desensitized = {}
    for key, value in tool_input.items():
        if key.lower() in SENSITIVE_PARAM_KEYS:
            desensitized[key] = "[已脱敏]"
        elif isinstance(value, str):
            value = PATH_DESENSITIZE_PATTERN.sub("/home/[用户]", value)
            value = PATH_DESENSITIZE_PATTERN2.sub("/Users/[用户]", value)
            desensitized[key] = value
        else:
            desensitized[key] = value
    return desensitized


def _truncate_output(tool_output: Any, max_length: int = OUTPUT_TRUNCATE_LENGTH) -> str:
    """截断工具输出，防止日志膨胀

    Args:
        tool_output: 工具输出
        max_length: 最大长度

    Returns:
        截断后的字符串
    """
    output_str = str(tool_output) if tool_output is not None else ""
    if len(output_str) > max_length:
        return output_str[:max_length] + "...[已截断]"
    return output_str


async def audit_native_tool_call(
    tool_name: str,
    agent_name: str,
    session_id: str,
    user_id: str,
    tool_input: dict[str, Any] | None = None,
    tool_output: Any = None,
    duration_ms: float = 0,
    permission_level: str = "read_only",
    status: str = "success",
) -> None:
    """记录原生工具调用的审计日志

    调用 security/audit.py 的 record_tool_call_audit()，
    在 detail 中附加 tool_source 和 permission_level 信息。

    Args:
        tool_name: 工具名称
        agent_name: Agent 名称
        session_id: 会话 ID
        user_id: 用户 ID
        tool_input: 工具输入
        tool_output: 工具输出
        duration_ms: 执行耗时（毫秒）
        permission_level: 权限级别
        status: 执行状态
    """
    try:
        from security.audit import record_tool_call_audit

        desensitized_input = _desensitize_input(tool_input or {})
        truncated_output = _truncate_output(tool_output)

        record_tool_call_audit(
            trace_id=session_id,
            user_id=user_id,
            user_role="",
            agent_name=agent_name,
            tool_name=tool_name,
            tool_input={
                **desensitized_input,
                "_tool_source": "native",
                "_permission_level": permission_level,
            },
            tool_output={"output": truncated_output},
            status=status,
            latency_ms=duration_ms,
        )

        logger.debug(
            "原生工具审计: tool=%s agent=%s session=%s duration=%.1fms status=%s",
            tool_name, agent_name, session_id[:8] if session_id else "", duration_ms, status,
        )
    except Exception as e:
        logger.warning("原生工具审计日志记录失败（非致命）: %s", e)


class ToolCallAuditor:
    """工具调用审计上下文管理器

    用于在工具调用前后自动记录审计日志，计算执行耗时。

    使用示例：
        async with ToolCallAuditor(tool_name, agent_name, session_id, user_id) as auditor:
            result = await some_tool_call(...)
            auditor.set_output(result)
    """

    def __init__(
        self,
        tool_name: str,
        agent_name: str,
        session_id: str,
        user_id: str,
        permission_level: str = "read_only",
    ):
        self.tool_name = tool_name
        self.agent_name = agent_name
        self.session_id = session_id
        self.user_id = user_id
        self.permission_level = permission_level
        self._start_time: float = 0
        self._output: Any = None
        self._status: str = "success"

    def set_output(self, output: Any) -> None:
        """设置工具输出"""
        self._output = output

    def set_status(self, status: str) -> None:
        """设置执行状态"""
        self._status = status

    async def __aenter__(self) -> "ToolCallAuditor":
        self._start_time = time.monotonic()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        duration_ms = (time.monotonic() - self._start_time) * 1000

        if exc_type is not None:
            self._status = "error"

        await audit_native_tool_call(
            tool_name=self.tool_name,
            agent_name=self.agent_name,
            session_id=self.session_id,
            user_id=self.user_id,
            tool_output=self._output,
            duration_ms=duration_ms,
            permission_level=self.permission_level,
            status=self._status,
        )
