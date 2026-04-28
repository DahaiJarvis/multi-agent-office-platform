"""指标采集与暴露

使用 Prometheus 客户端采集和暴露应用指标。
"""

import logging

from prometheus_client import Counter, Histogram, Gauge, generate_latest
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ==================== 指标定义 ====================

# 请求指标
REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status_code"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时",
    ["method", "endpoint"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

# Agent 指标
AGENT_CALL_COUNT = Counter(
    "agent_calls_total",
    "Agent 调用总数",
    ["agent_name", "status"],
)

AGENT_CALL_DURATION = Histogram(
    "agent_call_duration_seconds",
    "Agent 调用耗时",
    ["agent_name"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

AGENT_ACTIVE_SESSIONS = Gauge(
    "agent_active_sessions",
    "当前活跃会话数",
)

# MCP 工具指标
MCP_TOOL_CALL_COUNT = Counter(
    "mcp_tool_calls_total",
    "MCP 工具调用总数",
    ["server_name", "tool_name", "status"],
)

MCP_TOOL_CALL_DURATION = Histogram(
    "mcp_tool_call_duration_seconds",
    "MCP 工具调用耗时",
    ["server_name", "tool_name"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# LLM 指标
LLM_TOKEN_USAGE = Counter(
    "llm_token_usage_total",
    "LLM Token 使用量",
    ["model", "token_type"],
)

LLM_CALL_COUNT = Counter(
    "llm_calls_total",
    "LLM 调用总数",
    ["model", "status"],
)


def record_request(method: str, endpoint: str, status_code: int, duration: float) -> None:
    """记录 HTTP 请求指标"""
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)


def record_agent_call(agent_name: str, status: str, duration: float) -> None:
    """记录 Agent 调用指标"""
    AGENT_CALL_COUNT.labels(agent_name=agent_name, status=status).inc()
    AGENT_CALL_DURATION.labels(agent_name=agent_name).observe(duration)


def record_mcp_tool_call(server_name: str, tool_name: str, status: str, duration: float) -> None:
    """记录 MCP 工具调用指标"""
    MCP_TOOL_CALL_COUNT.labels(server_name=server_name, tool_name=tool_name, status=status).inc()
    MCP_TOOL_CALL_DURATION.labels(server_name=server_name, tool_name=tool_name).observe(duration)


def record_llm_usage(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """记录 LLM Token 使用量"""
    LLM_TOKEN_USAGE.labels(model=model, token_type="prompt").inc(prompt_tokens)
    LLM_TOKEN_USAGE.labels(model=model, token_type="completion").inc(completion_tokens)


async def metrics_endpoint(request: Request) -> Response:
    """Prometheus 指标暴露端点"""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
