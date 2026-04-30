"""Supervisor Agent - 规划与路由

职责：意图识别、任务拆解、Agent 调度、结果汇总
模型：qwen-max（高推理能力）
"""

import json
import logging
from enum import Enum
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from pydantic import BaseModel, Field

from agent.core.model_client import get_supervisor_client, get_lightweight_client
from observability.metrics import record_agent_call

logger = logging.getLogger(__name__)


class CollaborationMode(str, Enum):
    """协作模式"""

    DIRECT = "direct"
    SELECTOR = "selector"
    SWARM = "swarm"


class IntentResult(BaseModel):
    """意图识别结果"""

    intent: str = Field(..., description="意图标签")
    confidence: float = Field(..., ge=0, le=1, description="置信度")
    target_agent: str = Field(..., description="目标 Agent")
    collaboration_mode: CollaborationMode = Field(
        default=CollaborationMode.DIRECT, description="协作模式"
    )
    review_required: bool = Field(default=False, description="是否需要审核")
    sub_tasks: list[str] = Field(default_factory=list, description="子任务列表")


# 意图路由表
INTENT_ROUTING_TABLE: dict[str, dict[str, Any]] = {
    "approval_query": {"agent": "ApprovalAgent", "mode": "direct", "review": False},
    "approval_action": {"agent": "ApprovalAgent", "mode": "selector", "review": True},
    "email_send": {"agent": "EmailAgent", "mode": "selector", "review": True},
    "email_query": {"agent": "EmailAgent", "mode": "direct", "review": False},
    "calendar_query": {"agent": "CalendarAgent", "mode": "direct", "review": False},
    "calendar_create": {"agent": "CalendarAgent", "mode": "selector", "review": True},
    "crm_query": {"agent": "CRMAgent", "mode": "direct", "review": False},
    "hr_query": {"agent": "HRAgent", "mode": "direct", "review": False},
    "hr_action": {"agent": "HRAgent", "mode": "selector", "review": True},
    "finance_query": {"agent": "FinanceAgent", "mode": "direct", "review": False},
    "finance_action": {"agent": "FinanceAgent", "mode": "selector", "review": True},
    "knowledge_query": {"agent": "KnowledgeAgent", "mode": "direct", "review": False},
    "document_parse": {"agent": "KnowledgeAgent", "mode": "direct", "review": False},
    "document_summary": {"agent": "KnowledgeAgent", "mode": "direct", "review": False},
    "document_compare": {"agent": "KnowledgeAgent", "mode": "direct", "review": False},
    "report_generate": {"agent": "KnowledgeAgent", "mode": "selector", "review": True},
    "web_search": {"agent": "KnowledgeAgent", "mode": "direct", "review": False},
    "image_analyze": {"agent": "KnowledgeAgent", "mode": "direct", "review": False},
    "kb_manage": {"agent": "KnowledgeAgent", "mode": "selector", "review": True},
    "cross_system": {"agent": "Swarm", "mode": "swarm", "review": True},
    "complex_task": {"agent": "Swarm", "mode": "swarm", "review": True},
    "general": {"agent": "OfficeAssistant", "mode": "direct", "review": False},
}

# 需要审核的操作
REVIEW_REQUIRED_ACTIONS = [
    "submit_approval_action",
    "send_email",
    "modify_data",
    "delete_record",
    "financial_operation",
]

# 意图分类 Prompt
INTENT_CLASSIFICATION_PROMPT = """你是一个意图分类器。根据用户消息，输出意图分类结果。

可选意图标签：
- approval_query: 审批查询（查看待审批、审批详情、审批进度）
- approval_action: 审批操作（提交审批、同意/拒绝审批、撤回审批）
- email_query: 邮件查询（查看邮件、搜索邮件、邮件摘要）
- email_send: 邮件发送（发送邮件、回复邮件、转发邮件、起草邮件）
- calendar_query: 日程查询（查看日程、会议安排、日程提醒）
- calendar_create: 日程创建（创建会议、修改日程、取消会议）
- crm_query: CRM查询（客户信息、商机查询、销售数据）
- hr_query: HR查询（考勤、薪资查询、假期余额）
- hr_action: HR操作（请假申请、加班申请、离职申请）
- finance_query: 财务查询（预算、报销查询、费用统计）
- finance_action: 财务操作（提交报销、发票管理、付款申请）
- knowledge_query: 知识查询（在知识库中搜索信息、查找文档、问答）
- document_parse: 文档解析（解析PDF/Word等文件内容）
- document_summary: 文档摘要（总结文档内容、提取要点）
- document_compare: 文档对比（比较两份文档的异同）
- report_generate: 报告生成（生成研究报告、分析报告、数据报告）
- web_search: 网络搜索（搜索互联网获取实时信息，如天气、新闻、股价、汇率等实时数据）
- image_analyze: 图片分析（分析图片内容、图片问答）
- kb_manage: 知识库管理（创建知识库、上传文档、管理知识库）
- cross_system: 跨系统操作（涉及多个系统的联动）
- complex_task: 复杂多步任务（需要多个步骤或多个Agent协作完成）
- general: 通用办公（简单的日常对话、问候、闲聊）

分类示例：
- "北京明天天气怎么样" -> web_search（天气是实时信息，需要网络搜索）
- "帮我查一下今天的新闻" -> web_search
- "美元兑人民币汇率" -> web_search
- "帮我发一封邮件给张总" -> email_send
- "查看我的待审批列表" -> approval_query
- "帮我请一天假" -> hr_action
- "你好" -> general
- "帮我总结这份报告" -> document_summary

请严格按以下 JSON 格式输出，不要输出其他内容：
{"intent": "意图标签", "confidence": 0.0-1.0, "sub_tasks": ["子任务1", "子任务2"]}
"""

# Supervisor 系统提示词
SUPERVISOR_SYSTEM_PROMPT = """你是企业级多Agent办公平台的 Supervisor（规划与路由 Agent）。

你的职责：
1. 理解用户的办公需求
2. 将复杂任务拆解为子任务
3. 将子任务分配给合适的领域 Agent
4. 汇总各 Agent 的执行结果
5. 确保任务完整、准确地完成

你可以调度的 Agent：
- OfficeAssistant: 通用办公操作、简单查询
- EmailAgent: 邮件收发、分类、摘要
- ApprovalAgent: 审批查询与操作
- CalendarAgent: 日程查询与会议安排
- CRMAgent: 客户查询与商机跟进
- HRAgent: 请假、考勤、薪资
- FinanceAgent: 报销、预算、发票
- KnowledgeAgent: 知识库检索、文档处理、智能问答、报告生成

安全规则：
- 涉及数据修改、删除、发送的操作必须经过 Reviewer 审核
- 不确定时宁可多确认，不可擅自执行敏感操作
- 始终保护用户隐私和企业数据安全

完成所有任务后，请输出: TASK_COMPLETE
"""


async def classify_intent(user_message: str) -> IntentResult:
    """使用轻量级模型进行意图分类

    优先级：语义缓存 -> L1 精确缓存 -> LLM 调用

    Args:
        user_message: 用户消息

    Returns:
        IntentResult 意图分类结果
    """
    import time

    start_time = time.time()

    # 1. 尝试从语义缓存获取（语义相似匹配）
    try:
        from agent.core.performance.semantic_cache import get_semantic_cache
        sem_cache = get_semantic_cache()
        cached_result = await sem_cache.get(user_message, agent_name="IntentClassifier")
        if cached_result is not None:
            logger.debug("意图分类命中语义缓存: %s", user_message[:30])
            record_agent_call("Supervisor", "success", time.time() - start_time)
            return IntentResult.model_validate(cached_result)
    except Exception:
        pass

    # 2. 尝试从 L1 精确缓存获取
    try:
        from agent.core.performance.cache import get_cache, generate_cache_key

        cache = get_cache()
        cache_key = generate_cache_key("intent", user_message)

        cached = cache.get_l1(cache_key)
        if cached is not None:
            logger.debug("意图分类命中缓存: %s", user_message[:30])
            record_agent_call("Supervisor", "success", time.time() - start_time)
            return IntentResult.model_validate(cached)
    except Exception:
        pass

    try:
        from autogen_core.models import SystemMessage, UserMessage

        client = get_lightweight_client()
        # 优先从 Prompt Registry 加载意图分类 Prompt
        intent_prompt = _get_intent_classification_prompt()
        response = await client.create(
            messages=[
                SystemMessage(source="system", content=intent_prompt),
                UserMessage(source="user", content=user_message),
            ],
            json_output=True,
        )

        content = response.content
        if isinstance(content, list):
            content = "".join(
                part.text for part in content if hasattr(part, "text")
            )

        result = json.loads(content)
        intent = result.get("intent", "general")
        confidence = result.get("confidence", 0.5)
        sub_tasks = result.get("sub_tasks", [])

        # 查路由表：优先使用 Capability Card 动态路由
        routing = _resolve_routing(intent)

        intent_result = IntentResult(
            intent=intent,
            confidence=confidence,
            target_agent=routing["agent"],
            collaboration_mode=CollaborationMode(routing["mode"]),
            review_required=routing["review"],
            sub_tasks=sub_tasks,
        )

        # 写入 L1 精确缓存（5 分钟）
        try:
            cache.set_l1(cache_key, intent_result.model_dump(), ttl=300)
        except Exception:
            pass

        # 写入语义缓存（10 分钟）
        try:
            from agent.core.performance.semantic_cache import get_semantic_cache
            sem_cache = get_semantic_cache()
            await sem_cache.set(
                user_message,
                intent_result.model_dump(),
                agent_name="IntentClassifier",
                ttl=600,
            )
        except Exception:
            pass

        record_agent_call("Supervisor", "success", time.time() - start_time)
        return intent_result

    except Exception as e:
        logger.error("意图分类失败: %s", e)
        record_agent_call("Supervisor", "error", time.time() - start_time)

        return IntentResult(
            intent="general",
            confidence=0.0,
            target_agent="OfficeAssistant",
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )


def create_supervisor_agent() -> AssistantAgent:
    """创建 Supervisor Agent 实例

    优先从 Prompt Registry 加载外置 Prompt，降级到代码内嵌默认值。
    """
    prompt = _get_supervisor_prompt()
    return AssistantAgent(
        name="Supervisor",
        model_client=get_supervisor_client(),
        system_message=prompt,
    )


def _get_supervisor_prompt() -> str:
    """获取 Supervisor 的 System Prompt

    优先从 Prompt Registry 加载，降级到代码内嵌默认值。
    """
    try:
        from agent.core.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        prompt = registry.get_prompt_sync("Supervisor")
        if prompt:
            return prompt
    except Exception:
        pass
    return SUPERVISOR_SYSTEM_PROMPT


def _get_intent_classification_prompt() -> str:
    """获取意图分类 Prompt

    优先从 Prompt Registry 加载，降级到代码内嵌默认值。
    """
    try:
        from agent.core.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        prompt = registry.get_prompt_sync("IntentClassifier")
        if prompt:
            return prompt
    except Exception:
        pass
    return INTENT_CLASSIFICATION_PROMPT


def _resolve_routing(intent: str) -> dict[str, Any]:
    """根据意图解析路由信息

    优先从 Capability Card Registry 动态查找匹配的 Agent，
    降级到硬编码的 INTENT_ROUTING_TABLE。

    Args:
        intent: 意图标签

    Returns:
        路由信息字典 {"agent": ..., "mode": ..., "review": ...}
    """
    try:
        from agent.core.capability_card import get_capability_registry
        registry = get_capability_registry()
        matched_cards = registry.find_by_intent(intent)

        if matched_cards:
            best_card = matched_cards[0]
            # 根据安全约束判断是否需要审核
            review_required = bool(best_card.security_constraints) or intent in {
                "approval_action", "email_send", "hr_action",
                "finance_action", "kb_manage", "report_generate",
                "cross_system", "complex_task",
            }
            # 根据意图类型推断协作模式
            mode = "selector" if review_required else "direct"
            if intent in ("cross_system", "complex_task"):
                mode = "swarm"

            return {
                "agent": best_card.agent_name,
                "mode": mode,
                "review": review_required,
            }
    except Exception:
        pass

    # 降级到硬编码路由表
    return INTENT_ROUTING_TABLE.get(intent, INTENT_ROUTING_TABLE["general"])
