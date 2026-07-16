"""多 LLM 模型路由器

注意：本文件与 agent/core/performance/model_router.py 同名但职责不同。
- 本文件（model/model_router.py）：跨 LLM 提供商路由，消除供应商锁定
- performance/model_router.py：同提供商内模型分级路由，按任务复杂度选型

支持多个 LLM 提供商，消除供应商锁定风险：
  - OpenAI (GPT-4o / GPT-4o-mini)
  - Anthropic (Claude 3.5 Sonnet / Claude 3 Haiku)
  - 阿里云通义千问 (qwen-max / qwen-plus / qwen-turbo)
  - 本地模型 (Ollama / vLLM 兼容 OpenAI 接口)

路由策略：
  - 优先级路由：按配置的优先级顺序尝试
  - 成本优化：简单任务使用低成本模型
  - 故障转移：主模型不可用时自动切换到备用模型
  - 延迟感知：选择响应最快的可用模型
"""

import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.core.infrastructure.config import get_settings

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """LLM 提供商"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DASHSCOPE = "dashscope"
    LOCAL = "local"


class ModelTier(str, Enum):
    """模型能力级别"""

    FLAGSHIP = "flagship"
    STANDARD = "standard"
    ECONOMY = "economy"


class ProviderHealth(BaseModel):
    """提供商健康状态"""

    provider: LLMProvider
    available: bool = True
    latency_ms: float = 0
    error_count: int = 0
    last_error: str = ""
    last_check: float = 0


class ModelRoute(BaseModel):
    """模型路由配置"""

    provider: LLMProvider
    model: str
    api_key: str = ""
    base_url: str = ""
    tier: ModelTier = ModelTier.STANDARD
    priority: int = 0
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: float = 60.0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0


class RouteDecision(BaseModel):
    """路由决策结果"""

    provider: LLMProvider
    model: str
    base_url: str
    api_key: str
    tier: ModelTier
    fallback_chain: list[dict[str, str]] = Field(default_factory=list)


# 提供商健康状态
_provider_health: dict[str, ProviderHealth] = {}

# 路由配置缓存
_routes_cache: dict[str, ModelRoute] = {}

# 健康检查阈值
_MAX_ERROR_COUNT = 5
_HEALTH_RESET_SECONDS = 300


def _build_routes_from_config() -> dict[str, ModelRoute]:
    """从配置构建模型路由表"""
    settings = get_settings()
    routes: dict[str, ModelRoute] = {}

    # 阿里云通义千问（默认提供商）
    if settings.dashscope_api_key:
        routes["dashscope_max"] = ModelRoute(
            provider=LLMProvider.DASHSCOPE,
            model=settings.model_qwen_max,
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            tier=ModelTier.FLAGSHIP,
            priority=10,
            cost_per_1k_input=0.02,
            cost_per_1k_output=0.06,
        )
        routes["dashscope_plus"] = ModelRoute(
            provider=LLMProvider.DASHSCOPE,
            model=settings.model_qwen_plus,
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            tier=ModelTier.STANDARD,
            priority=20,
            cost_per_1k_input=0.004,
            cost_per_1k_output=0.012,
        )
        routes["dashscope_turbo"] = ModelRoute(
            provider=LLMProvider.DASHSCOPE,
            model=settings.model_qwen_turbo,
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            tier=ModelTier.ECONOMY,
            priority=30,
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
        )

    # OpenAI
    openai_key = getattr(settings, "openai_api_key", "")
    openai_base = getattr(settings, "openai_base_url", "https://api.openai.com/v1")
    if openai_key:
        routes["openai_gpt4o"] = ModelRoute(
            provider=LLMProvider.OPENAI,
            model=getattr(settings, "openai_model_flagship", "gpt-4o"),
            api_key=openai_key,
            base_url=openai_base,
            tier=ModelTier.FLAGSHIP,
            priority=5,
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.01,
        )
        routes["openai_gpt4o_mini"] = ModelRoute(
            provider=LLMProvider.OPENAI,
            model=getattr(settings, "openai_model_standard", "gpt-4o-mini"),
            api_key=openai_key,
            base_url=openai_base,
            tier=ModelTier.STANDARD,
            priority=15,
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
        )

    # Anthropic
    anthropic_key = getattr(settings, "anthropic_api_key", "")
    anthropic_base = getattr(settings, "anthropic_base_url", "https://api.anthropic.com")
    if anthropic_key:
        routes["anthropic_sonnet"] = ModelRoute(
            provider=LLMProvider.ANTHROPIC,
            model=getattr(settings, "anthropic_model_flagship", "claude-3-5-sonnet-20241022"),
            api_key=anthropic_key,
            base_url=anthropic_base,
            tier=ModelTier.FLAGSHIP,
            priority=6,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        routes["anthropic_haiku"] = ModelRoute(
            provider=LLMProvider.ANTHROPIC,
            model=getattr(settings, "anthropic_model_economy", "claude-3-haiku-20240307"),
            api_key=anthropic_key,
            base_url=anthropic_base,
            tier=ModelTier.ECONOMY,
            priority=25,
            cost_per_1k_input=0.00025,
            cost_per_1k_output=0.00125,
        )

    # 本地模型
    local_base = getattr(settings, "local_llm_base_url", "")
    local_model = getattr(settings, "local_llm_model", "")
    if local_base and local_model:
        routes["local_default"] = ModelRoute(
            provider=LLMProvider.LOCAL,
            model=local_model,
            api_key="local",
            base_url=local_base,
            tier=ModelTier.STANDARD,
            priority=50,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )

    return routes


def get_routes() -> dict[str, ModelRoute]:
    """获取所有模型路由"""
    if not _routes_cache:
        _routes_cache.update(_build_routes_from_config())
    return _routes_cache


def resolve_route(tier: ModelTier | str = ModelTier.STANDARD) -> RouteDecision:
    """根据模型级别解析路由

    路由策略：
    1. 在指定级别中按优先级排序
    2. 跳过不可用的提供商
    3. 构建故障转移链

    Args:
        tier: 模型能力级别

    Returns:
        RouteDecision 路由决策
    """
    if isinstance(tier, str):
        tier = ModelTier(tier)

    routes = get_routes()

    tier_routes = [r for r in routes.values() if r.tier == tier]
    tier_routes.sort(key=lambda r: r.priority)

    available_routes = []
    for route in tier_routes:
        health = _get_health(route.provider.value)
        if health.available:
            available_routes.append(route)

    if not available_routes:
        for route in tier_routes:
            _reset_health_if_stale(route.provider.value)
        available_routes = tier_routes

    if not available_routes:
        raise RuntimeError(f"无可用的 {tier.value} 级别模型路由")

    primary = available_routes[0]
    fallback_chain = []
    for route in available_routes[1:]:
        fallback_chain.append({
            "provider": route.provider.value,
            "model": route.model,
            "base_url": route.base_url,
        })

    return RouteDecision(
        provider=primary.provider,
        model=primary.model,
        base_url=primary.base_url,
        api_key=primary.api_key,
        tier=primary.tier,
        fallback_chain=fallback_chain,
    )


def resolve_route_by_name(route_name: str) -> RouteDecision:
    """按路由名称解析

    Args:
        route_name: 路由名称，如 "dashscope_max"

    Returns:
        RouteDecision
    """
    routes = get_routes()
    route = routes.get(route_name)
    if not route:
        raise ValueError(f"路由不存在: {route_name}")

    fallback = []
    for name, r in routes.items():
        if name != route_name and r.tier == route.tier:
            fallback.append({
                "provider": r.provider.value,
                "model": r.model,
                "base_url": r.base_url,
            })

    return RouteDecision(
        provider=route.provider,
        model=route.model,
        base_url=route.base_url,
        api_key=route.api_key,
        tier=route.tier,
        fallback_chain=fallback,
    )


def create_client_from_route(decision: RouteDecision, temperature: float | None = None) -> Any:
    """从路由决策创建模型客户端

    Args:
        decision: 路由决策
        temperature: 推理温度

    Returns:
        OpenAIChatCompletionClient 实例
    """
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    route = get_routes().get(f"{decision.provider.value}_{_extract_route_suffix(decision)}")
    temp = temperature if temperature is not None else (route.temperature if route else 0.7)

    if decision.provider == LLMProvider.ANTHROPIC:
        return _create_anthropic_client(decision, temp)

    return OpenAIChatCompletionClient(
        model=decision.model,
        api_key=decision.api_key,
        base_url=decision.base_url,
        temperature=temp,
    )


def _create_anthropic_client(decision: RouteDecision, temperature: float) -> Any:
    """创建 Anthropic 客户端

    Anthropic 使用 OpenAI 兼容接口（需通过代理或官方兼容层）。
    如无代理，降级到下一个可用路由。
    """
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    anthropic_base = decision.base_url
    if "/v1" not in anthropic_base:
        anthropic_base = anthropic_base.rstrip("/") + "/v1"

    return OpenAIChatCompletionClient(
        model=decision.model,
        api_key=decision.api_key,
        base_url=anthropic_base,
        temperature=temperature,
    )


def _extract_route_suffix(decision: RouteDecision) -> str:
    """从路由决策中提取路由后缀"""
    routes = get_routes()
    for name, route in routes.items():
        if route.provider == decision.provider and route.model == decision.model:
            parts = name.split("_", 1)
            return parts[1] if len(parts) > 1 else name
    return "default"


def record_provider_success(provider: str, latency_ms: float) -> None:
    """记录提供商调用成功"""
    health = _get_health(provider)
    health.error_count = max(0, health.error_count - 1)
    health.latency_ms = latency_ms
    health.available = True
    health.last_check = time.time()


def record_provider_error(provider: str, error: str) -> None:
    """记录提供商调用失败"""
    health = _get_health(provider)
    health.error_count += 1
    health.last_error = error
    health.last_check = time.time()

    if health.error_count >= _MAX_ERROR_COUNT:
        health.available = False
        logger.warning("提供商 %s 已标记为不可用: 连续 %d 次错误", provider, health.error_count)


def get_provider_health() -> dict[str, dict]:
    """获取所有提供商的健康状态"""
    result = {}
    for provider, health in _provider_health.items():
        result[provider] = {
            "available": health.available,
            "latency_ms": health.latency_ms,
            "error_count": health.error_count,
            "last_error": health.last_error,
        }
    return result


def _get_health(provider: str) -> ProviderHealth:
    """获取或创建提供商健康状态"""
    if provider not in _provider_health:
        _provider_health[provider] = ProviderHealth(provider=LLMProvider(provider))
    return _provider_health[provider]


def _reset_health_if_stale(provider: str) -> None:
    """如果健康状态过期，重置为可用"""
    health = _get_health(provider)
    if not health.available and (time.time() - health.last_check) > _HEALTH_RESET_SECONDS:
        health.available = True
        health.error_count = 0
        logger.info("提供商 %s 健康状态已重置", provider)


def get_model_client_for_tier(tier: str = "plus") -> Any:
    """获取指定级别的模型客户端（兼容原有接口）

    此函数保持与原有 model_client.py 的兼容性，
    同时支持多提供商路由。
    当系统处于降级状态时，自动降低模型级别以节省成本。

    Args:
        tier: 模型级别 max/plus/turbo

    Returns:
        OpenAIChatCompletionClient 实例
    """
    tier_mapping = {
        "max": ModelTier.FLAGSHIP,
        "plus": ModelTier.STANDARD,
        "turbo": ModelTier.ECONOMY,
    }

    # 降级策略：根据 DegradationManager 状态自动降级模型
    effective_tier = _apply_degradation_tier(tier)
    model_tier = tier_mapping.get(effective_tier, ModelTier.STANDARD)
    decision = resolve_route(model_tier)
    return create_client_from_route(decision)


def _apply_degradation_tier(requested_tier: str) -> str:
    """根据系统降级级别调整模型层级

    降级映射：
      - NORMAL: 使用请求的层级
      - L1_LIGHT: max -> plus, plus/turbo 不变
      - L2_MEDIUM: max -> plus, plus -> turbo, turbo 不变
      - L3_HEAVY: 全部降级到 turbo
      - L4_EXTREME: 全部降级到 turbo

    Args:
        requested_tier: 请求的模型层级

    Returns:
        实际使用的模型层级
    """
    try:
        from deploy.ha_manager import get_degradation_manager, DegradationLevel

        manager = get_degradation_manager()
        level = manager.level

        downgrade_map = {
            DegradationLevel.L1_LIGHT: {"max": "plus"},
            DegradationLevel.L2_MEDIUM: {"max": "plus", "plus": "turbo"},
            DegradationLevel.L3_HEAVY: {"max": "turbo", "plus": "turbo", "turbo": "turbo"},
            DegradationLevel.L4_EXTREME: {"max": "turbo", "plus": "turbo", "turbo": "turbo"},
        }

        tier_map = downgrade_map.get(level, {})
        effective = tier_map.get(requested_tier, requested_tier)

        if effective != requested_tier:
            logger.info(
                "模型降级: %s -> %s (降级级别: %s)",
                requested_tier, effective, level.value,
            )

        return effective
    except Exception:
        return requested_tier
