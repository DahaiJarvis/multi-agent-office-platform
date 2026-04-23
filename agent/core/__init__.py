"""Agent 核心模块"""

from agent.core.config import get_settings, Settings
from agent.core.model_client import (
    get_model_client,
    get_supervisor_client,
    get_reviewer_client,
    get_domain_agent_client,
    get_lightweight_client,
)
from agent.core.mcp_integration import (
    load_mcp_tools,
    load_agent_tools,
    close_all_connections,
    register_mcp_server,
    disable_mcp_server,
    MCP_SERVER_REGISTRY,
    AGENT_TOOL_BINDINGS,
)
from agent.core.session_manager import (
    SessionManager,
    SessionState,
    get_session_manager,
)
from agent.core.context_manager import (
    compress_context,
    estimate_tokens,
    build_agent_context,
    extract_session_history,
    prepare_context_for_agent,
)

__all__ = [
    "get_settings",
    "Settings",
    "get_model_client",
    "get_supervisor_client",
    "get_reviewer_client",
    "get_domain_agent_client",
    "get_lightweight_client",
    "load_mcp_tools",
    "load_agent_tools",
    "close_all_connections",
    "register_mcp_server",
    "disable_mcp_server",
    "MCP_SERVER_REGISTRY",
    "AGENT_TOOL_BINDINGS",
    "SessionManager",
    "SessionState",
    "get_session_manager",
    "compress_context",
    "estimate_tokens",
    "build_agent_context",
    "extract_session_history",
    "prepare_context_for_agent",
]
