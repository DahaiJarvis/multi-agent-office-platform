"""LLM 客户端初始化与管理

阿里云通义千问系列模型客户端，兼容 OpenAI 接口格式。
模型分级策略：
  - qwen-max:   高推理能力，用于 Supervisor / Reviewer
  - qwen-plus:  均衡性能，用于领域 Agent
  - qwen-turbo: 轻量快速，用于简单任务 / 意图分类
"""

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelInfo
from agent.core.config import get_settings

import logging
from typing import Any

logger = logging.getLogger(__name__)

_settings = get_settings()

MODEL_TIERS = {
    "max": _settings.model_qwen_max,
    "plus": _settings.model_qwen_plus,
    "turbo": _settings.model_qwen_turbo,
}

_client_cache: dict[str, OpenAIChatCompletionClient] = {}

_QWEN_MODEL_INFO = ModelInfo(
    vision=False,
    function_calling=True,
    json_output=True,
    structured_output=True,
    family="unknown",
)


def _create_client(model: str, temperature: float | None = None) -> OpenAIChatCompletionClient:
    """创建通义千问模型客户端

    通义千问模型名称（如 qwen-max）不属于 OpenAI 标准模型，
    AutoGen 要求提供 model_info 以声明模型能力。
    """
    kwargs: dict = {
        "model": model,
        "api_key":_settings.dashscope_api_key,
        "base_url": _settings.dashscope_base_url,
        "model_info": _QWEN_MODEL_INFO,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return OpenAIChatCompletionClient(**kwargs)


def get_model_client(tier: str = "plus") -> OpenAIChatCompletionClient:
    """获取指定级别的模型客户端

    Args:
        tier: 模型级别，可选 max / plus / turbo

    Returns:
        OpenAIChatCompletionClient 实例
    """
    model_name = MODEL_TIERS.get(tier, MODEL_TIERS["plus"])

    if model_name not in _client_cache:
        _client_cache[model_name] = _create_client(model_name)

    return _client_cache[model_name]


def get_supervisor_client() -> OpenAIChatCompletionClient:
    """获取 Supervisor 专用客户端（qwen-max）"""
    return get_model_client("max")


def get_reviewer_client() -> OpenAIChatCompletionClient:
    """获取 Reviewer 专用客户端（qwen-max，低温度）"""
    model_name = MODEL_TIERS["max"]
    cache_key = f"{model_name}:reviewer"

    if cache_key not in _client_cache:
        _client_cache[cache_key] = _create_client(model_name, temperature=0.1)

    return _client_cache[cache_key]


def get_domain_agent_client() -> OpenAIChatCompletionClient:
    """获取领域 Agent 客户端（qwen-plus）"""
    return get_model_client("plus")


def get_lightweight_client() -> OpenAIChatCompletionClient:
    """获取轻量级客户端（qwen-turbo），用于简单任务 / 意图分类"""
    return get_model_client("turbo")


async def cached_model_call(
    messages: list,
    agent_name: str = "",
    tier: str = "plus",
    ttl: float | None = None,
    **kwargs: Any,
) -> Any:
    """带语义缓存的 LLM 调用

    先查询语义缓存，命中则直接返回；未命中则调用 LLM 并写入缓存。
    适用于 Agent 的主推理调用，不适用于需要实时数据的场景。

    Args:
        messages: 消息列表（AutoGen LLMMessage 格式）
        agent_name: Agent 名称，用于缓存分区
        tier: 模型级别
        ttl: 缓存生存时间(秒)
        **kwargs: 传递给 client.create 的额外参数

    Returns:
        LLM 响应（CreateResult）
    """
    from autogen_core.models import UserMessage

    # 提取用户查询文本作为缓存 key
    query_text = ""
    for msg in reversed(messages):
        if isinstance(msg, UserMessage):
            query_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            break
        elif hasattr(msg, "content"):
            content = msg.content
            if isinstance(content, list):
                content = "".join(
                    part.text for part in content if hasattr(part, "text")
                )
            if content:
                query_text = str(content)
                break

    if not query_text:
        client = get_model_client(tier)
        return await client.create(messages=messages, **kwargs)

    # 查询语义缓存
    try:
        from agent.core.performance.semantic_cache import get_semantic_cache
        cache = get_semantic_cache()
        cached = await cache.get(query_text, agent_name=agent_name)
        if cached is not None:
            logger.debug("语义缓存命中: agent=%s query=%s", agent_name, query_text[:30])
            return cached
    except Exception:
        pass

    # 调用 LLM
    client = get_model_client(tier)
    result = await client.create(messages=messages, **kwargs)

    # 自动记录 Token 用量
    _record_token_usage(result, tier, agent_name)

    # 写入缓存
    try:
        from agent.core.performance.semantic_cache import get_semantic_cache
        cache = get_semantic_cache()
        await cache.set(query_text, result, agent_name=agent_name, ttl=ttl)
    except Exception:
        pass

    return result


def _record_token_usage(result: Any, tier: str, agent_name: str) -> None:
    """从 LLM 响应中提取 Token 用量并记录到预算管理器

    AutoGen CreateResult 包含 usage 字段，格式为 RequestUsage。
    """
    try:
        usage = getattr(result, "usage", None)
        if usage is None:
            return

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        if prompt_tokens == 0 and completion_tokens == 0:
            return

        model_name = MODEL_TIERS.get(tier, "")

        # 异步记录（通过 asyncio.create_task 避免阻塞调用方）
        import asyncio
        try:
            from agent.core.token_budget import get_token_budget_manager
            manager = get_token_budget_manager()
            loop = asyncio.get_running_loop()

            async def _safe_record() -> None:
                try:
                    await manager.record_usage(
                        user_id="system",
                        session_id="auto",
                        model=model_name,
                        tier=tier,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        agent_name=agent_name,
                    )
                except Exception:
                    pass

            loop.create_task(_safe_record())
        except Exception:
            pass

        logger.debug(
            "Token 用量: agent=%s tier=%s prompt=%d completion=%d",
            agent_name, tier, prompt_tokens, completion_tokens,
        )
    except Exception:
        pass
