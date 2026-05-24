"""MCP 工具集成模块

提供 MCP 服务的连接、工具加载、校验、溯源等能力。
"""

from agent.core.mcp.mcp_integration import (
    MCPServerConfig,
    MCP_SERVER_REGISTRY,
    AGENT_TOOL_BINDINGS,
    TOOL_TO_MCP_SERVER_MAP,
    resolve_mcp_server,
    rebuild_tool_name_index,
    get_tool_server_name,
    discover_from_registry,
    load_mcp_tools,
    load_agent_tools,
    close_all_connections,
    register_mcp_server,
    disable_mcp_server,
    check_tool_health,
    call_tool_with_timeout,
)
from agent.core.mcp.mcp_tracing import (
    MCPCallTrace,
    ServiceQualityMetrics,
    MCPTracer,
    get_mcp_tracer,
    trace_mcp_call,
)
from agent.core.mcp.mcp_validator import (
    ValidationResult,
    validate_mcp_response,
    get_validation_stats,
)
from agent.core.mcp.tool_registry import (
    execute_tool,
)

__all__ = [
    "MCPServerConfig",
    "MCP_SERVER_REGISTRY",
    "AGENT_TOOL_BINDINGS",
    "TOOL_TO_MCP_SERVER_MAP",
    "resolve_mcp_server",
    "rebuild_tool_name_index",
    "get_tool_server_name",
    "discover_from_registry",
    "load_mcp_tools",
    "load_agent_tools",
    "close_all_connections",
    "register_mcp_server",
    "disable_mcp_server",
    "check_tool_health",
    "call_tool_with_timeout",
    "MCPCallTrace",
    "ServiceQualityMetrics",
    "MCPTracer",
    "get_mcp_tracer",
    "trace_mcp_call",
    "ValidationResult",
    "validate_mcp_response",
    "get_validation_stats",
    "execute_tool",
]
