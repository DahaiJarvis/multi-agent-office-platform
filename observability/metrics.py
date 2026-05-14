"""指标采集与暴露

================================================================================
模块职责
================================================================================
使用 Prometheus 客户端采集和暴露应用指标，包括：
  - HTTP 请求指标
  - Agent 调用指标
  - MCP 工具调用指标
  - LLM Token 使用指标

================================================================================
指标类型
================================================================================
Counter（计数器）：
  - 只增不减的累计值
  - 用于请求总数、调用次数等

Histogram（直方图）：
  - 观测值的分布统计
  - 用于请求耗时、响应大小等
  - 自动计算分位数（P50、P90、P99）

Gauge（仪表盘）：
  - 可增可减的瞬时值
  - 用于活跃会话数、内存使用等

================================================================================
指标分类
================================================================================
HTTP 请求指标：
  - http_requests_total: 请求总数
  - http_request_duration_seconds: 请求耗时

Agent 指标：
  - agent_calls_total: Agent 调用总数
  - agent_call_duration_seconds: Agent 调用耗时
  - agent_active_sessions: 当前活跃会话数

MCP 工具指标：
  - mcp_tool_calls_total: 工具调用总数
  - mcp_tool_call_duration_seconds: 工具调用耗时

LLM 指标：
  - llm_token_usage_total: Token 使用量
  - llm_calls_total: LLM 调用总数

================================================================================
与其他模块的关系
================================================================================
- routing.py: 记录请求指标
- domain.py: 记录 Agent 调用指标
- mcp_integration.py: 记录工具调用指标
- model_client.py: 记录 LLM Token 使用指标

================================================================================
使用示例
================================================================================
    # 记录 HTTP 请求
    record_request("POST", "/api/v1/chat", 200, 1.5)

    # 记录 Agent 调用
    record_agent_call("EmailAgent", "success", 2.3)

    # 记录 MCP 工具调用
    record_mcp_tool_call("email_server", "send_email", "success", 0.5)

    # 记录 LLM Token 使用
    record_llm_usage("qwen-plus", 1000, 500)
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
    """记录 HTTP 请求指标

    Args:
        method: HTTP 方法（GET、POST、PUT、DELETE 等）
        endpoint: 端点路径（如 /api/v1/chat）
        status_code: HTTP 状态码（如 200、400、500）
        duration: 请求耗时（秒）
    """
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)


def record_agent_call(agent_name: str, status: str, duration: float) -> None:
    """记录 Agent 调用指标

    Args:
        agent_name: Agent 名称（如 EmailAgent、ApprovalAgent）
        status: 调用状态（success、error、timeout）
        duration: 调用耗时（秒）
    """
    AGENT_CALL_COUNT.labels(agent_name=agent_name, status=status).inc()
    AGENT_CALL_DURATION.labels(agent_name=agent_name).observe(duration)


def record_mcp_tool_call(server_name: str, tool_name: str, status: str, duration: float) -> None:
    """记录 MCP 工具调用指标

    Args:
        server_name: MCP 服务器名称（如 email_server、knowledge_server）
        tool_name: 工具名称（如 send_email、query_knowledge）
        status: 调用状态（success、error、timeout）
        duration: 调用耗时（秒）
    """
    MCP_TOOL_CALL_COUNT.labels(server_name=server_name, tool_name=tool_name, status=status).inc()
    MCP_TOOL_CALL_DURATION.labels(server_name=server_name, tool_name=tool_name).observe(duration)


def record_llm_usage(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """记录 LLM Token 使用量

    分别记录 prompt tokens 和 completion tokens。

    Args:
        model: 模型名称（如 qwen-max、qwen-plus、qwen-turbo）
        prompt_tokens: 输入 Token 数量
        completion_tokens: 输出 Token 数量
    """
    LLM_TOKEN_USAGE.labels(model=model, token_type="prompt").inc(prompt_tokens)
    LLM_TOKEN_USAGE.labels(model=model, token_type="completion").inc(completion_tokens)


async def metrics_endpoint(request: Request) -> Response:
    """Prometheus 指标暴露端点

    供 Prometheus 抓取指标的 HTTP 端点。

    路径：/metrics
    格式：Prometheus 文本格式

    Args:
        request: HTTP 请求对象

    Returns:
        Prometheus 指标文本响应
    """
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
