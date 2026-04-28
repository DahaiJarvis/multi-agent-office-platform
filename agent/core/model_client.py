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
    """获取轻量级客户端（qwen-turbo），用于意图分类等简单任务"""
    return get_model_client("turbo")
