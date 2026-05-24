"""提示词管理模块

提供 Prompt 模板库和注册中心，支持模板创建、渲染、版本管理。
"""

from agent.core.prompt.prompt_library import (
    PromptCategory,
    PromptVariable,
    PromptTemplate,
    PromptExecution,
    create_template,
    get_template,
    list_templates,
    update_template,
    delete_template,
    render_template,
    recommend_templates,
    rate_template,
    SkillPromptTemplate,
    create_skill_template,
    list_skill_templates,
    get_skill_template,
    create_agent_from_skill_template,
)
from agent.core.prompt.prompt_registry import (
    PromptVersion,
    PromptEntry,
    IntentDefinition,
    IntentExample,
    PromptRegistry,
    get_prompt_registry,
)

__all__ = [
    "PromptCategory",
    "PromptVariable",
    "PromptTemplate",
    "PromptExecution",
    "create_template",
    "get_template",
    "list_templates",
    "update_template",
    "delete_template",
    "render_template",
    "recommend_templates",
    "rate_template",
    "SkillPromptTemplate",
    "create_skill_template",
    "list_skill_templates",
    "get_skill_template",
    "create_agent_from_skill_template",
    "PromptVersion",
    "PromptEntry",
    "IntentDefinition",
    "IntentExample",
    "PromptRegistry",
    "get_prompt_registry",
]
