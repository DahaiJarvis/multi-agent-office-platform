"""LLM 客户端初始化与管理

================================================================================
模块职责
================================================================================
提供阿里云通义千问系列模型客户端的初始化和管理，包括：
  - 模型分级策略
  - 客户端缓存
  - 语义缓存集成
  - Token 用量记录

================================================================================
模型分级策略
================================================================================
三级模型配置，平衡成本与能力：
  -------------------------------------------------------------------------
  qwen-max（最高能力）：
    - 适用场景：复杂推理、合同审查、数据分析
    - 用于：Supervisor Agent、Reviewer Agent
    - 成本：高

  qwen-plus（均衡性能）：
    - 适用场景：常规办公任务、邮件处理、日程管理
    - 用于：领域 Agent（EmailAgent、ApprovalAgent 等）
    - 成本：中

  qwen-turbo（轻量快速）：
    - 适用场景：简单查询、意图分类、快速响应
    - 用于：意图分类、轻量级任务
    - 成本：低
  -------------------------------------------------------------------------

================================================================================
与其他模块的关系
================================================================================
- supervisor.py: 使用 get_supervisor_client() 和 get_lightweight_client()
- domain.py: 使用 get_domain_agent_client()
- reviewer.py: 使用 get_reviewer_client()
- token_budget.py: 自动记录 Token 用量

================================================================================
使用示例
================================================================================
    # 获取不同级别的客户端
    supervisor_client = get_supervisor_client()  # qwen-max
    domain_client = get_domain_agent_client()    # qwen-plus
    lightweight_client = get_lightweight_client()  # qwen-turbo

    # 带语义缓存的调用
    result = await cached_model_call(
        messages=[UserMessage(content="帮我发邮件")],
        agent_name="EmailAgent",
        tier="plus",
    )
"""

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelInfo
from agent.core.config import get_settings

import logging
from typing import Any

logger = logging.getLogger(__name__)

_settings = get_settings()

# 模型级别映射
MODEL_TIERS = {
    "max": _settings.model_qwen_max,
    "plus": _settings.model_qwen_plus,
    "turbo": _settings.model_qwen_turbo,
}

# 客户端缓存，避免重复创建
_client_cache: dict[str, OpenAIChatCompletionClient] = {}

# 通义千问模型能力声明
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

    Args:
        model: 模型名称
        temperature: 推理温度（可选）

    Returns:
        OpenAIChatCompletionClient 实例
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

    从缓存中获取或创建新的客户端实例。

    Args:
        tier: 模型级别，可选：
            - "max": qwen-max，最高能力
            - "plus": qwen-plus，均衡性能（默认）
            - "turbo": qwen-turbo，轻量快速

    Returns:
        OpenAIChatCompletionClient 实例
    """
    model_name = MODEL_TIERS.get(tier, MODEL_TIERS["plus"])

    if model_name not in _client_cache:
        _client_cache[model_name] = _create_client(model_name)

    return _client_cache[model_name]


def get_supervisor_client() -> OpenAIChatCompletionClient:
    """获取 Supervisor 专用客户端

    Supervisor 需要高推理能力，使用 qwen-max。

    Returns:
        qwen-max 客户端实例
    """
    return get_model_client("max")


def get_reviewer_client() -> OpenAIChatCompletionClient:
    """获取 Reviewer 专用客户端

    Reviewer 需要高推理能力和低温度（确保审核严谨性），
    使用 qwen-max + temperature=0.1。

    Returns:
        qwen-max 低温度客户端实例
    """
    model_name = MODEL_TIERS["max"]
    cache_key = f"{model_name}:reviewer"

    if cache_key not in _client_cache:
        _client_cache[cache_key] = _create_client(model_name, temperature=0.1)

    return _client_cache[cache_key]


def get_domain_agent_client() -> OpenAIChatCompletionClient:
    """获取领域 Agent 客户端

    领域 Agent 处理常规办公任务，使用 qwen-plus。

    Returns:
        qwen-plus 客户端实例
    """
    return get_model_client("plus")


def get_lightweight_client() -> OpenAIChatCompletionClient:
    """获取轻量级客户端

    用于简单任务和意图分类，使用 qwen-turbo。

    Returns:
        qwen-turbo 客户端实例
    """
    return get_model_client("turbo")


async def cached_model_call(
    messages: list,
    agent_name: str = "",
    tier: str = "plus",
    ttl: float | None = None,
    **kwargs: Any,
) -> Any:
    """带语义缓存的 LLM 调用

    执行流程：
    -------------------------------------------------------------------------
    1. 提取用户查询文本作为缓存 key
    2. 查询语义缓存，命中则直接返回
    3. 未命中则调用 LLM
    4. 记录 Token 用量
    5. 写入语义缓存
    -------------------------------------------------------------------------

    适用场景：
    - Agent 的主推理调用
    - 语义相似但表述不同的查询

    不适用场景：
    - 需要实时数据的查询
    - 每次结果必须不同的场景

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

    Args:
        result: LLM 响应结果
        tier: 模型级别
        agent_name: Agent 名称
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
