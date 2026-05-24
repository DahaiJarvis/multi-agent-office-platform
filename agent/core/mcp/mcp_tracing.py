"""MCP 调用溯源与质量监控

记录每次 MCP 工具调用的完整链路信息，支持调用溯源、质量评估和异常告警。

核心能力：
  - 调用溯源：记录完整的调用链路（用户 -> Agent -> MCP -> 后端系统）
  - 质量监控：追踪每个 MCP 服务的成功率、延迟、错误率
  - 异常告警：连续失败或延迟异常时自动告警
  - 调用回放：支持根据 trace_id 回放完整调用链路

使用方式：
    from agent.core.mcp.mcp_tracing import trace_mcp_call, get_mcp_tracer

    tracer = get_mcp_tracer()

    # 记录调用
    trace_id = await tracer.start_call(server_name, tool_name, session_id, agent_name)
    try:
        result = await call_mcp_tool(...)
        await tracer.end_call(trace_id, status="success", response=result)
    except Exception as e:
        await tracer.end_call(trace_id, status="error", error=str(e))
"""

import json
import logging
import time
import uuid
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPCallTrace(BaseModel):
    """MCP 调用链路记录"""

    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="链路ID")
    parent_trace_id: str = Field(default="", description="父链路ID（关联用户请求）")
    session_id: str = Field(default="", description="会话ID")
    user_id: str = Field(default="", description="用户ID")
    agent_name: str = Field(default="", description="调用 Agent")
    server_name: str = Field(default="", description="MCP 服务名")
    tool_name: str = Field(default="", description="工具名")
    input_params: Any = Field(default=None, description="输入参数")
    output_data: Any = Field(default=None, description="输出数据")
    status: str = Field(default="pending", description="状态: pending/success/error/timeout")
    error_message: str = Field(default="", description="错误信息")
    start_time: float = Field(default_factory=time.time, description="开始时间")
    end_time: float = Field(default=0, description="结束时间")
    duration_ms: float = Field(default=0, description="耗时(毫秒)")
    validation_passed: bool = Field(default=True, description="响应校验是否通过")
    validation_confidence: float = Field(default=1.0, description="响应可信度")


class ServiceQualityMetrics(BaseModel):
    """服务质量指标"""

    server_name: str = Field(..., description="服务名")
    total_calls: int = Field(default=0, description="总调用次数")
    success_calls: int = Field(default=0, description="成功次数")
    error_calls: int = Field(default=0, description="失败次数")
    timeout_calls: int = Field(default=0, description="超时次数")
    avg_duration_ms: float = Field(default=0, description="平均耗时")
    p99_duration_ms: float = Field(default=0, description="P99 耗时")
    success_rate: float = Field(default=1.0, description="成功率")
    last_error: str = Field(default="", description="最近错误信息")
    last_error_time: float = Field(default=0, description="最近错误时间")
    consecutive_failures: int = Field(default=0, description="连续失败次数")


class MCPTracer:
    """MCP 调用溯源与质量监控

    记录每次 MCP 调用的完整信息，支持调用链路追踪和质量监控。
    """

    def __init__(self, max_traces: int = 10000) -> None:
        self._traces: dict[str, MCPCallTrace] = {}
        self._max_traces = max_traces
        self._durations: dict[str, list[float]] = defaultdict(list)
        self._quality: dict[str, ServiceQualityMetrics] = {}
        self._alert_callbacks: list[Any] = []

    async def start_call(
        self,
        server_name: str,
        tool_name: str,
        session_id: str = "",
        agent_name: str = "",
        user_id: str = "",
        input_params: Any = None,
        parent_trace_id: str = "",
    ) -> str:
        """记录 MCP 调用开始

        Args:
            server_name: MCP 服务名
            tool_name: 工具名
            session_id: 会话ID
            agent_name: Agent 名称
            user_id: 用户ID
            input_params: 输入参数
            parent_trace_id: 父链路ID

        Returns:
            trace_id 用于后续 end_call
        """
        trace = MCPCallTrace(
            parent_trace_id=parent_trace_id,
            session_id=session_id,
            user_id=user_id,
            agent_name=agent_name,
            server_name=server_name,
            tool_name=tool_name,
            input_params=self._sanitize_params(input_params),
        )

        self._evict_if_needed()
        self._traces[trace.trace_id] = trace

        logger.debug(
            "MCP 调用开始: trace=%s server=%s tool=%s agent=%s",
            trace.trace_id[:8], server_name, tool_name, agent_name,
        )

        return trace.trace_id

    async def end_call(
        self,
        trace_id: str,
        status: str = "success",
        response: Any = None,
        error: str = "",
        validation_passed: bool = True,
        validation_confidence: float = 1.0,
    ) -> None:
        """记录 MCP 调用结束

        Args:
            trace_id: start_call 返回的链路ID
            status: 调用状态
            response: 响应数据
            error: 错误信息
            validation_passed: 响应校验是否通过
            validation_confidence: 响应可信度
        """
        trace = self._traces.get(trace_id)
        if trace is None:
            logger.warning("未找到链路记录: %s", trace_id)
            return

        trace.end_time = time.time()
        trace.duration_ms = (trace.end_time - trace.start_time) * 1000
        trace.status = status
        trace.error_message = error
        trace.validation_passed = validation_passed
        trace.validation_confidence = validation_confidence

        # 截断过大的响应数据
        if response is not None:
            trace.output_data = self._truncate_response(response)

        # 更新质量指标
        self._update_quality(trace)

        # 记录 Prometheus 指标
        try:
            from observability.metrics import record_mcp_tool_call
            record_mcp_tool_call(
                server_name=trace.server_name,
                tool_name=trace.tool_name,
                status=status,
                duration=trace.duration_ms / 1000.0,
            )
        except Exception:
            pass

        # 检查是否需要告警
        self._check_alert(trace)

        logger.debug(
            "MCP 调用结束: trace=%s status=%s duration=%.0fms",
            trace_id[:8], status, trace.duration_ms,
        )

    def get_trace(self, trace_id: str) -> MCPCallTrace | None:
        """获取链路记录"""
        return self._traces.get(trace_id)

    def get_session_traces(self, session_id: str) -> list[MCPCallTrace]:
        """获取会话的所有链路记录"""
        return [
            t for t in self._traces.values()
            if t.session_id == session_id
        ]

    def get_agent_traces(self, agent_name: str, limit: int = 50) -> list[MCPCallTrace]:
        """获取 Agent 的最近调用记录"""
        traces = [
            t for t in self._traces.values()
            if t.agent_name == agent_name
        ]
        traces.sort(key=lambda t: t.start_time, reverse=True)
        return traces[:limit]

    def get_quality_metrics(self, server_name: str) -> ServiceQualityMetrics | None:
        """获取服务质量指标"""
        return self._quality.get(server_name)

    def get_all_quality_metrics(self) -> dict[str, ServiceQualityMetrics]:
        """获取所有服务质量指标"""
        return dict(self._quality)

    def register_alert_callback(self, callback: Any) -> None:
        """注册告警回调函数

        当检测到异常时调用回调函数，参数为 (trace, alert_type, message)
        """
        self._alert_callbacks.append(callback)

    def _update_quality(self, trace: MCPCallTrace) -> None:
        """更新服务质量指标"""
        server = trace.server_name
        if server not in self._quality:
            self._quality[server] = ServiceQualityMetrics(server_name=server)

        q = self._quality[server]
        q.total_calls += 1

        if trace.status == "success":
            q.success_calls += 1
            q.consecutive_failures = 0
        elif trace.status == "timeout":
            q.timeout_calls += 1
            q.consecutive_failures += 1
        else:
            q.error_calls += 1
            q.consecutive_failures += 1
            q.last_error = trace.error_message[:200]
            q.last_error_time = trace.end_time

        # 更新延迟统计
        self._durations[server].append(trace.duration_ms)
        durations = self._durations[server][-1000:]
        q.avg_duration_ms = sum(durations) / len(durations)
        # 分层 P99 计算：样本不足时使用最大值，样本充足时使用插值
        sorted_durations = sorted(durations)
        if len(sorted_durations) >= 100:
            idx = int(len(sorted_durations) * 0.99)
            q.p99_duration_ms = sorted_durations[idx]
        elif len(sorted_durations) >= 10:
            idx = int(len(sorted_durations) * 0.99)
            q.p99_duration_ms = sorted_durations[min(idx, len(sorted_durations) - 1)]
        else:
            q.p99_duration_ms = max(sorted_durations)

        # 更新成功率
        q.success_rate = q.success_calls / q.total_calls if q.total_calls > 0 else 1.0

    def _check_alert(self, trace: MCPCallTrace) -> None:
        """检查是否需要告警"""
        alerts: list[tuple[str, str]] = []

        # 连续失败告警
        q = self._quality.get(trace.server_name)
        if q and q.consecutive_failures >= 3:
            alerts.append((
                "consecutive_failure",
                f"MCP 服务 {trace.server_name} 连续失败 {q.consecutive_failures} 次",
            ))

        # 延迟告警
        if trace.duration_ms > 10000:
            alerts.append((
                "high_latency",
                f"MCP 服务 {trace.server_name} 工具 {trace.tool_name} 耗时 {trace.duration_ms:.0f}ms",
            ))

        # 成功率告警
        if q and q.total_calls >= 10 and q.success_rate < 0.5:
            alerts.append((
                "low_success_rate",
                f"MCP 服务 {trace.server_name} 成功率仅 {q.success_rate:.0%}",
            ))

        # 触发告警回调
        for alert_type, message in alerts:
            logger.warning("MCP 告警: %s - %s", alert_type, message)
            for callback in self._alert_callbacks:
                try:
                    callback(trace, alert_type, message)
                except Exception as e:
                    logger.error("告警回调执行失败: %s", e)

    def _evict_if_needed(self) -> None:
        """清理超限的链路记录"""
        if len(self._traces) <= self._max_traces:
            return

        # 按时间排序，删除最旧的记录
        sorted_keys = sorted(
            self._traces.keys(),
            key=lambda k: self._traces[k].start_time,
        )
        to_remove = len(self._traces) - self._max_traces + 100
        for key in sorted_keys[:to_remove]:
            del self._traces[key]

    @staticmethod
    def _sanitize_params(params: Any) -> Any:
        """清洗输入参数中的敏感信息"""
        if params is None:
            return None
        try:
            from agent.core.common.sanitize_utils import sanitize_data
            return sanitize_data(params)
        except ImportError:
            content = json.dumps(params, ensure_ascii=False, default=str)
            import re
            content = re.sub(r'\b1[3-9]\d{9}\b', '[手机号已脱敏]', content)
            content = re.sub(
                r'\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b',
                '[身份证已脱敏]', content,
            )
            try:
                return json.loads(content)
            except Exception:
                return content

    @staticmethod
    def _truncate_response(response: Any, max_length: int = 5000) -> Any:
        """截断过大的响应数据"""
        if isinstance(response, str) and len(response) > max_length:
            return response[:max_length] + "...（内容过长，已截断）"
        if isinstance(response, (dict, list)):
            content = json.dumps(response, ensure_ascii=False, default=str)
            if len(content) > max_length:
                return content[:max_length] + "...（内容过长，已截断）"
        return response


# 全局 MCP Tracer 实例
_tracer: MCPTracer | None = None


def get_mcp_tracer() -> MCPTracer:
    """获取全局 MCP Tracer 实例"""
    global _tracer
    if _tracer is None:
        _tracer = MCPTracer()
    return _tracer


def trace_mcp_call(server_name: str, tool_name: str):
    """MCP 调用溯源装饰器

    用法：
        @trace_mcp_call("knowledge", "search")
        async def search_knowledge(query: str) -> dict:
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            tracer = get_mcp_tracer()
            trace_id = await tracer.start_call(
                server_name=server_name,
                tool_name=tool_name,
            )
            try:
                result = await func(*args, **kwargs)
                await tracer.end_call(trace_id, status="success", response=result)
                return result
            except Exception as e:
                await tracer.end_call(trace_id, status="error", error=str(e))
                raise
        return wrapper
    return decorator
