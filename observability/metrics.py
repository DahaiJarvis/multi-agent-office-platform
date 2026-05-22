"""指标采集与暴露

================================================================================
模块职责
================================================================================
使用 Prometheus 客户端采集和暴露应用指标，包括：
  - HTTP 请求指标
  - Agent 调用指标
  - MCP 工具调用指标
  - LLM Token 使用指标
  - 业务指标（任务、意图、审批、邮件、工具、安全、工作流、技能等）

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

业务指标：
  - business_task_total: 业务任务执行总数
  - business_task_duration_seconds: 业务任务执行耗时
  - business_intent_distribution: 意图分布统计
  - business_clarification_total: 需要用户澄清的请求数
  - business_approval_action_total: 审批操作统计
  - business_email_sent_total: 邮件发送统计
  - business_session_duration_seconds: 会话持续时间
  - business_tool_usage_total: 工具使用频率
  - business_guardrail_block_total: 安全拦截统计
  - business_workflow_execution_total: 工作流执行统计
  - business_skill_usage_total: 技能使用频率
  - business_active_users: 活跃用户数

================================================================================
与其他模块的关系
================================================================================
- routing.py: 记录请求指标、业务任务指标
- domain.py: 记录 Agent 调用指标
- mcp_integration.py: 记录工具调用指标
- model_client.py: 记录 LLM Token 使用指标
- guardrails.py: 记录安全拦截指标
- workflow_engine.py: 记录工作流执行指标
- skill_adapter.py: 记录技能使用指标

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

    # 记录业务任务
    record_business_task("approval_query", "ApprovalAgent", "success", 1.2)

    # 记录安全拦截
    record_guardrail_block("tool_whitelist", "block")
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

# ==================== 业务指标定义 ====================

BUSINESS_TASK_COUNT = Counter(
    "business_task_total",
    "业务任务执行总数",
    ["intent", "agent", "status"],
)

BUSINESS_TASK_DURATION = Histogram(
    "business_task_duration_seconds",
    "业务任务执行耗时",
    ["intent", "agent"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

BUSINESS_INTENT_DISTRIBUTION = Counter(
    "business_intent_distribution",
    "意图分布统计",
    ["intent", "confidence_level"],
)

BUSINESS_CLARIFICATION_COUNT = Counter(
    "business_clarification_total",
    "需要用户澄清的请求数",
    ["intent"],
)

BUSINESS_APPROVAL_ACTION_COUNT = Counter(
    "business_approval_action_total",
    "审批操作统计",
    ["action_type"],
)

BUSINESS_EMAIL_SENT_COUNT = Counter(
    "business_email_sent_total",
    "邮件发送统计",
    ["agent", "has_attachment"],
)

BUSINESS_SESSION_DURATION = Histogram(
    "business_session_duration_seconds",
    "会话持续时间",
    ["user_tier"],
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600],
)

BUSINESS_TOOL_USAGE_COUNT = Counter(
    "business_tool_usage_total",
    "工具使用频率",
    ["tool_name", "agent_name"],
)

BUSINESS_GUARDRAIL_BLOCK_COUNT = Counter(
    "business_guardrail_block_total",
    "安全拦截统计",
    ["check_type", "action"],
)

BUSINESS_WORKFLOW_EXECUTION_COUNT = Counter(
    "business_workflow_execution_total",
    "工作流执行统计",
    ["workflow_id", "status"],
)

BUSINESS_SKILL_USAGE_COUNT = Counter(
    "business_skill_usage_total",
    "技能使用频率",
    ["skill_name", "agent_name"],
)

BUSINESS_ACTIVE_USERS = Gauge(
    "business_active_users",
    "活跃用户数",
    ["tenant_id"],
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


# ==================== 业务指标记录函数 ====================


def record_business_task(intent: str, agent: str, status: str, duration: float) -> None:
    """记录业务任务执行指标

    Args:
        intent: 意图标签（如 approval_query、email_send）
        agent: Agent 名称（如 ApprovalAgent、EmailAgent）
        status: 执行状态（success、error、timeout）
        duration: 执行耗时（秒）
    """
    BUSINESS_TASK_COUNT.labels(intent=intent, agent=agent, status=status).inc()
    BUSINESS_TASK_DURATION.labels(intent=intent, agent=agent).observe(duration)


def record_intent_distribution(intent: str, confidence: float) -> None:
    """记录意图分布统计

    根据置信度将意图分类为 high(>=0.9)、medium(>=0.7)、low(<0.7) 三个级别。

    Args:
        intent: 意图标签
        confidence: 置信度分数（0.0~1.0）
    """
    if confidence >= 0.9:
        level = "high"
    elif confidence >= 0.7:
        level = "medium"
    else:
        level = "low"
    BUSINESS_INTENT_DISTRIBUTION.labels(intent=intent, confidence_level=level).inc()


def record_clarification(intent: str) -> None:
    """记录需要用户澄清的请求

    Args:
        intent: 意图标签
    """
    BUSINESS_CLARIFICATION_COUNT.labels(intent=intent).inc()


def record_approval_action(action_type: str) -> None:
    """记录审批操作

    Args:
        action_type: 操作类型（approve、reject、transfer）
    """
    BUSINESS_APPROVAL_ACTION_COUNT.labels(action_type=action_type).inc()


def record_email_sent(agent: str, has_attachment: bool = False) -> None:
    """记录邮件发送

    Args:
        agent: Agent 名称
        has_attachment: 是否包含附件
    """
    BUSINESS_EMAIL_SENT_COUNT.labels(agent=agent, has_attachment=str(has_attachment)).inc()


def record_session_duration(user_tier: str, duration: float) -> None:
    """记录会话持续时间

    Args:
        user_tier: 用户层级（如 standard、professional、enterprise）
        duration: 会话持续时间（秒）
    """
    BUSINESS_SESSION_DURATION.labels(user_tier=user_tier).observe(duration)


def record_tool_usage(tool_name: str, agent_name: str) -> None:
    """记录工具使用频率

    Args:
        tool_name: 工具名称（如 email:send、approval:query）
        agent_name: Agent 名称
    """
    BUSINESS_TOOL_USAGE_COUNT.labels(tool_name=tool_name, agent_name=agent_name).inc()


def record_guardrail_block(check_type: str, action: str) -> None:
    """记录安全拦截统计

    Args:
        check_type: 检查类型（如 tool_whitelist、permission、sensitive_action）
        action: 拦截动作（如 block、confirm）
    """
    BUSINESS_GUARDRAIL_BLOCK_COUNT.labels(check_type=check_type, action=action).inc()


def record_workflow_execution(workflow_id: str, status: str) -> None:
    """记录工作流执行统计

    Args:
        workflow_id: 工作流ID
        status: 执行状态（success、error、timeout）
    """
    BUSINESS_WORKFLOW_EXECUTION_COUNT.labels(workflow_id=workflow_id, status=status).inc()


def record_skill_usage(skill_name: str, agent_name: str) -> None:
    """记录技能使用频率

    Args:
        skill_name: 技能名称
        agent_name: Agent 名称
    """
    BUSINESS_SKILL_USAGE_COUNT.labels(skill_name=skill_name, agent_name=agent_name).inc()


def set_active_users(tenant_id: str, count: float) -> None:
    """设置活跃用户数

    Args:
        tenant_id: 租户ID
        count: 活跃用户数
    """
    BUSINESS_ACTIVE_USERS.labels(tenant_id=tenant_id).set(count)


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
