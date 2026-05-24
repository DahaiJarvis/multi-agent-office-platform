"""会话与上下文模块

提供会话管理、上下文压缩、应用上下文、长期记忆等能力。
"""

from agent.core.session.session_manager import (
    SessionState,
    SessionManager,
    get_session_manager,
)
from agent.core.session.context_manager import (
    estimate_tokens,
    compress_context,
    extract_and_store_knowledge,
    build_agent_context,
    extract_session_history,
    prepare_context_for_agent,
    compact_messages,
)
from agent.core.session.app_context import (
    AppContext,
    get_app_context,
)
from agent.core.session.long_term_memory import (
    LongTermMemory,
    get_long_term_memory,
)

__all__ = [
    "SessionState",
    "SessionManager",
    "get_session_manager",
    "estimate_tokens",
    "compress_context",
    "extract_and_store_knowledge",
    "build_agent_context",
    "extract_session_history",
    "prepare_context_for_agent",
    "compact_messages",
    "AppContext",
    "get_app_context",
    "LongTermMemory",
    "get_long_term_memory",
]
