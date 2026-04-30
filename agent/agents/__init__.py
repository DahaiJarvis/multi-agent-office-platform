"""Agent 模块

包含所有 Agent 定义：
  - Supervisor: 意图分类与路由调度
  - Domain Agents: 各领域专业 Agent
  - Reviewer: 安全审核 Agent
"""

from agent.agents.supervisor import (
    CollaborationMode,
    IntentResult,
    create_supervisor_agent,
    classify_intent,
    INTENT_ROUTING_TABLE,
)
from agent.agents.domain import (
    create_domain_agent,
    AGENT_PROMPTS,
    AGENT_CREATORS,
    SkillConfig,
    BUILTIN_SKILLS,
    AGENT_SKILL_BINDINGS,
    get_agent_skills,
    register_skill,
    bind_skill_to_agent,
)
from agent.agents.reviewer import (
    create_reviewer_agent,
    is_sensitive_action,
    SENSITIVE_ACTION_KEYWORDS,
    REVIEWER_SYSTEM_PROMPT,
)

__all__ = [
    "CollaborationMode",
    "IntentResult",
    "create_supervisor_agent",
    "classify_intent",
    "INTENT_ROUTING_TABLE",
    "create_domain_agent",
    "AGENT_PROMPTS",
    "AGENT_CREATORS",
    "SkillConfig",
    "BUILTIN_SKILLS",
    "AGENT_SKILL_BINDINGS",
    "get_agent_skills",
    "register_skill",
    "bind_skill_to_agent",
    "create_reviewer_agent",
    "is_sensitive_action",
    "SENSITIVE_ACTION_KEYWORDS",
    "REVIEWER_SYSTEM_PROMPT",
]
