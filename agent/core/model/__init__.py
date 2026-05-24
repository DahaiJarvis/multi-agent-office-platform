"""模型管理模块

提供 LLM 客户端初始化、多模型路由、Token 预算控制等能力。
"""

from agent.core.model.model_client import (
    get_model_client,
    get_supervisor_client,
    get_reviewer_client,
    get_domain_agent_client,
    get_lightweight_client,
    cached_model_call,
)
from agent.core.model.model_router import (
    LLMProvider,
    ModelTier,
    ProviderHealth,
    ModelRoute,
    RouteDecision,
    get_routes,
    resolve_route,
    resolve_route_by_name,
    create_client_from_route,
    record_provider_success,
    record_provider_error,
    get_provider_health,
    get_model_client_for_tier,
)
from agent.core.model.token_budget import (
    TokenUsage,
    BudgetConfig,
    UsageRecord,
    TokenBudgetManager,
    get_token_budget_manager,
)

__all__ = [
    "get_model_client",
    "get_supervisor_client",
    "get_reviewer_client",
    "get_domain_agent_client",
    "get_lightweight_client",
    "cached_model_call",
    "LLMProvider",
    "ModelTier",
    "ProviderHealth",
    "ModelRoute",
    "RouteDecision",
    "get_routes",
    "resolve_route",
    "resolve_route_by_name",
    "create_client_from_route",
    "record_provider_success",
    "record_provider_error",
    "get_provider_health",
    "get_model_client_for_tier",
    "TokenUsage",
    "BudgetConfig",
    "UsageRecord",
    "TokenBudgetManager",
    "get_token_budget_manager",
]
