"""Agent 核心模块

按职责划分为以下子模块：
  - infrastructure: 基础设施（配置、Redis、异步工具、熔断器、分布式锁、事件总线、插件）
  - mcp: MCP 工具集成（连接、加载、校验、溯源、工具桥接）
  - skill: 技能系统（适配、解析、能力卡片）
  - model: 模型管理（客户端、路由、Token 预算）
  - session: 会话与上下文（会话管理、上下文压缩、应用上下文、长期记忆）
  - prompt: 提示词（模板库、注册中心）
  - workflow: 工作流与任务（引擎、审批、长任务、消息队列、人工确认、检查点、定时任务）
  - data: 数据与检索（分析引擎、搜索、RAG 增强）
  - observability: 可观测性（审计、反馈、SLA）
  - common: 通用工具（无障碍、国际化、多模态、脱敏）
  - performance: 性能优化（缓存、连接池、语义缓存）
"""

import sys

# 从子模块导入关键公共 API
from agent.core.infrastructure.config import get_settings, Settings
from agent.core.model.model_client import (
    get_model_client,
    get_supervisor_client,
    get_reviewer_client,
    get_domain_agent_client,
    get_lightweight_client,
)
from agent.core.mcp.mcp_integration import (
    load_mcp_tools,
    load_agent_tools,
    close_all_connections,
    register_mcp_server,
    disable_mcp_server,
    MCP_SERVER_REGISTRY,
    AGENT_TOOL_BINDINGS,
)
from agent.core.session.session_manager import (
    SessionManager,
    SessionState,
    get_session_manager,
)
from agent.core.session.context_manager import (
    compress_context,
    estimate_tokens,
    build_agent_context,
    extract_session_history,
    prepare_context_for_agent,
)

# sys.modules 向后兼容别名
# 使得 from agent.core.config import get_settings 等旧路径仍然可用
_MODULE_ALIASES = {
    "agent.core.config": "agent.core.infrastructure.config",
    "agent.core.redis_manager": "agent.core.infrastructure.redis_manager",
    "agent.core.async_utils": "agent.core.infrastructure.async_utils",
    "agent.core.circuit_breaker": "agent.core.infrastructure.circuit_breaker",
    "agent.core.distributed_lock": "agent.core.infrastructure.distributed_lock",
    "agent.core.event_bus": "agent.core.infrastructure.event_bus",
    "agent.core.plugin_system": "agent.core.infrastructure.plugin_system",
    "agent.core.mcp_integration": "agent.core.mcp.mcp_integration",
    "agent.core.mcp_tracing": "agent.core.mcp.mcp_tracing",
    "agent.core.mcp_validator": "agent.core.mcp.mcp_validator",
    "agent.core.tool_registry": "agent.core.mcp.tool_registry",
    "agent.core.skill_adapter": "agent.core.skill.skill_adapter",
    "agent.core.skill_resolver": "agent.core.skill.skill_resolver",
    "agent.core.capability_card": "agent.core.skill.capability_card",
    "agent.core.model_client": "agent.core.model.model_client",
    "agent.core.model_router": "agent.core.model.model_router",
    "agent.core.token_budget": "agent.core.model.token_budget",
    "agent.core.session_manager": "agent.core.session.session_manager",
    "agent.core.context_manager": "agent.core.session.context_manager",
    "agent.core.app_context": "agent.core.session.app_context",
    "agent.core.long_term_memory": "agent.core.session.long_term_memory",
    "agent.core.prompt_library": "agent.core.prompt.prompt_library",
    "agent.core.prompt_registry": "agent.core.prompt.prompt_registry",
    "agent.core.workflow_engine": "agent.core.workflow.workflow_engine",
    "agent.core.approval_flow": "agent.core.workflow.approval_flow",
    "agent.core.long_task": "agent.core.workflow.long_task",
    "agent.core.task_checkpoint": "agent.core.workflow.task_checkpoint",
    "agent.core.scheduler": "agent.core.workflow.scheduler",
    "agent.core.agent_router": "agent.core.workflow.agent_router",
    "agent.core.message_queue": "agent.core.workflow.message_queue",
    "agent.core.human_confirm": "agent.core.workflow.human_confirm",
    "agent.core.data_analysis": "agent.core.data.data_analysis",
    "agent.core.search_engine": "agent.core.data.search_engine",
    "agent.core.rag_enhanced": "agent.core.data.rag_enhanced",
    "agent.core.audit": "agent.core.observability.audit",
    "agent.core.feedback": "agent.core.observability.feedback",
    "agent.core.sla": "agent.core.observability.sla",
    "agent.core.accessibility": "agent.core.common.accessibility",
    "agent.core.i18n": "agent.core.common.i18n",
    "agent.core.multimodal": "agent.core.common.multimodal",
    "agent.core.sanitize_utils": "agent.core.common.sanitize_utils",
}

for _old, _new in _MODULE_ALIASES.items():
    if _old not in sys.modules:
        try:
            import importlib
            _mod = importlib.import_module(_new)
            sys.modules[_old] = _mod
        except ImportError:
            pass

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
