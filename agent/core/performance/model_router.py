"""模型分级路由

根据任务复杂度自动选择合适的模型级别，实现成本与性能的平衡。

模型分级策略:
  - turbo (qwen-turbo): 简单任务，如意图分类、格式转换、简单查询
  - plus (qwen-plus): 常规任务，如领域 Agent 的日常操作
  - max (qwen-max): 复杂任务，如 Supervisor 规划、Reviewer 审核、复杂推理

路由依据:
  - Agent 角色: 不同角色默认使用不同级别
  - 任务类型: 简单查询 vs 复杂推理
  - Token 预算: 超出预算时自动降级
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.core.model.model_client import get_model_client

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    """模型级别"""

    TURBO = "turbo"
    PLUS = "plus"
    MAX = "max"


@dataclass
class ModelTierConfig:
    """模型级别配置"""

    tier: ModelTier
    model_name: str
    max_tokens: int
    cost_per_1k_input: float
    cost_per_1k_output: float


# 模型级别配置表
TIER_CONFIGS: dict[ModelTier, ModelTierConfig] = {
    ModelTier.TURBO: ModelTierConfig(
        tier=ModelTier.TURBO,
        model_name="qwen-turbo",
        max_tokens=8192,
        cost_per_1k_input=0.0003,
        cost_per_1k_output=0.0006,
    ),
    ModelTier.PLUS: ModelTierConfig(
        tier=ModelTier.PLUS,
        model_name="qwen-plus",
        max_tokens=131072,
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.006,
    ),
    ModelTier.MAX: ModelTierConfig(
        tier=ModelTier.MAX,
        model_name="qwen-max",
        max_tokens=32768,
        cost_per_1k_input=0.02,
        cost_per_1k_output=0.06,
    ),
}

# Agent 角色与默认模型级别映射
AGENT_TIER_MAPPING: dict[str, ModelTier] = {
    "Supervisor": ModelTier.MAX,
    "Reviewer": ModelTier.MAX,
    "ApprovalAgent": ModelTier.PLUS,
    "EmailAgent": ModelTier.PLUS,
    "CalendarAgent": ModelTier.PLUS,
    "CRMAgent": ModelTier.PLUS,
    "HRAgent": ModelTier.PLUS,
    "FinanceAgent": ModelTier.PLUS,
    "OfficeAssistant": ModelTier.PLUS,
}

# 任务类型与模型级别映射
TASK_TIER_MAPPING: dict[str, ModelTier] = {
    "intent_classification": ModelTier.TURBO,
    "simple_query": ModelTier.TURBO,
    "format_conversion": ModelTier.TURBO,
    "domain_task": ModelTier.PLUS,
    "cross_system": ModelTier.PLUS,
    "complex_reasoning": ModelTier.MAX,
    "security_review": ModelTier.MAX,
    "task_planning": ModelTier.MAX,
}


def get_tier_for_agent(agent_name: str) -> ModelTier:
    """根据 Agent 角色获取推荐模型级别

    Args:
        agent_name: Agent 名称

    Returns:
        推荐的模型级别
    """
    return AGENT_TIER_MAPPING.get(agent_name, ModelTier.PLUS)


def get_tier_for_task(task_type: str) -> ModelTier:
    """根据任务类型获取推荐模型级别

    Args:
        task_type: 任务类型

    Returns:
        推荐的模型级别
    """
    return TASK_TIER_MAPPING.get(task_type, ModelTier.PLUS)


def get_model_client_for_agent(agent_name: str) -> Any:
    """根据 Agent 角色获取对应的模型客户端

    Args:
        agent_name: Agent 名称

    Returns:
        OpenAIChatCompletionClient 实例
    """
    tier = get_tier_for_agent(agent_name)
    logger.debug("Agent %s 使用模型级别: %s", agent_name, tier.value)
    return get_model_client(tier.value)


def get_model_client_for_task(task_type: str) -> Any:
    """根据任务类型获取对应的模型客户端

    Args:
        task_type: 任务类型

    Returns:
        OpenAIChatCompletionClient 实例
    """
    tier = get_tier_for_task(task_type)
    logger.debug("任务类型 %s 使用模型级别: %s", task_type, tier.value)
    return get_model_client(tier.value)


def estimate_cost(tier: ModelTier, input_tokens: int, output_tokens: int) -> float:
    """估算模型调用成本

    Args:
        tier: 模型级别
        input_tokens: 输入 Token 数
        output_tokens: 输出 Token 数

    Returns:
        预估成本（元）
    """
    config = TIER_CONFIGS[tier]
    input_cost = (input_tokens / 1000) * config.cost_per_1k_input
    output_cost = (output_tokens / 1000) * config.cost_per_1k_output
    return round(input_cost + output_cost, 6)
