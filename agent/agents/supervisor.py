"""Supervisor Agent - 规划与路由

================================================================================
模块职责
================================================================================
Supervisor 是多 Agent 系统的"大脑"，负责：
  1. 意图识别：理解用户消息，识别用户想要做什么
  2. 任务拆解：将复杂任务拆解为可执行的子任务
  3. Agent 调度：根据意图选择合适的领域 Agent
  4. 结果汇总：汇总各 Agent 的执行结果，返回给用户

================================================================================
核心流程
================================================================================
用户消息 -> 意图分类 -> 路由决策 -> Agent 调度 -> 结果汇总

意图分类流程：
  1. 优先从语义缓存获取（语义相似匹配）
  2. 其次从 L1 精确缓存获取
  3. 最后调用轻量级 LLM 进行分类

================================================================================
协作模式
================================================================================
- DIRECT：单 Agent 直接执行，适用于简单查询
- SELECTOR：SelectorGroupChat 模式，Agent + Reviewer，适用于需要审核的操作
- SWARM：Swarm 模式，Supervisor + 多领域 Agent + Reviewer，适用于跨系统/复杂任务

================================================================================
与其他模块的关系
================================================================================
- team_factory.py：根据 Supervisor 的意图分类结果创建团队
- routing.py：调用 Supervisor 的 classify_intent() 进行意图分类
- domain.py：Supervisor 调度领域 Agent 执行具体任务
- reviewer.py：SELECTOR/SWARM 模式下，Reviewer 审核敏感操作

================================================================================
模型配置
================================================================================
- 意图分类：使用轻量级模型（qwen-turbo），降低延迟和成本
- Supervisor Agent：使用高推理能力模型（qwen-max），确保任务规划质量

================================================================================
使用示例
================================================================================
    # 意图分类
    result = await classify_intent("帮我发一封邮件给张总")
    # result.intent = "email_send"
    # result.target_agent = "EmailAgent"
    # result.collaboration_mode = CollaborationMode.SELECTOR

    # 创建 Supervisor Agent
    supervisor = create_supervisor_agent()
"""

import json
import logging
from enum import Enum
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from pydantic import BaseModel, Field

from agent.core.model.model_client import get_supervisor_client, get_lightweight_client
from observability.metrics import record_agent_call

logger = logging.getLogger(__name__)


class CollaborationMode(str, Enum):
    """协作模式枚举

    定义 Agent 之间的协作方式，影响团队创建和执行流程。

    模式说明：
    -------------------------------------------------------------------------
    DIRECT：
        - 单 Agent 直接执行
        - 适用于简单查询，如"查看我的待审批"
        - 不需要 Reviewer 审核
        - 执行路径：用户 -> Agent -> 结果

    SELECTOR：
        - SelectorGroupChat 模式
        - 适用于需要审核的操作，如"发送邮件"、"提交审批"
        - 包含目标 Agent + Reviewer
        - 执行路径：用户 -> Agent -> Reviewer 审核 -> 结果

    SWARM：
        - Swarm 模式，Supervisor 主导的多 Agent 协作
        - 适用于跨系统操作或复杂多步任务
        - 包含 Supervisor + 多个领域 Agent + OfficeAssistant + Reviewer
        - 执行路径：用户 -> Supervisor -> Agent1/Agent2/... -> Reviewer -> 结果
    -------------------------------------------------------------------------
    """

    DIRECT = "direct"
    SELECTOR = "selector"
    SWARM = "swarm"


class IntentResult(BaseModel):
    """意图识别结果

    意图分类的输出结构，包含路由决策所需的所有信息。

    字段说明：
    -------------------------------------------------------------------------
    intent: 意图标签
        - 标识用户想要执行的操作类型
        - 示例：approval_query, email_send, cross_system

    confidence: 置信度
        - 意图分类的置信度，范围 [0, 1]
        - 低于 0.7 时，系统会请求用户澄清

    target_agent: 目标 Agent
        - 负责执行该意图的 Agent 名称
        - 示例：ApprovalAgent, EmailAgent, Swarm（SWARM 模式）

    collaboration_mode: 协作模式
        - 决定如何组织 Agent 团队
        - DIRECT/SELECTOR/SWARM 三种模式

    review_required: 是否需要审核
        - 敏感操作（发送邮件、提交审批等）需要 Reviewer 审核
        - 由意图类型和路由表决定

    sub_tasks: 子任务列表
        - 复杂任务拆解后的子任务
        - 用于 SWARM 模式的多步执行

    orchestration_mode: 编排模式（可选）
        - 仅 SWARM 模式下生效
        - 为空时默认使用顺序编排（sequential）
        - parallel: 多维度并行收集信息，各维度无依赖
        - debate: 多角度深度推理验证
        - vote: 多数决定提高准确率
    -------------------------------------------------------------------------
    """

    intent: str = Field(..., description="意图标签")
    confidence: float = Field(..., ge=0, le=1, description="置信度")
    target_agent: str = Field(..., description="目标 Agent")
    collaboration_mode: CollaborationMode = Field(
        default=CollaborationMode.DIRECT, description="协作模式"
    )
    review_required: bool = Field(default=False, description="是否需要审核")
    sub_tasks: list[str] = Field(default_factory=list, description="子任务列表")
    orchestration_mode: str | None = Field(
        default=None, description="编排模式: parallel/debate/vote，为空时默认顺序编排"
    )
    reasoning: str = Field(default="", description="意图分类的推理过程")


# 意图路由表
# -------------------------------------------------------------------------
# 定义意图到 Agent 的映射关系，包含：
#   - agent: 目标 Agent 名称
#   - mode: 协作模式（direct/selector/swarm）
#   - review: 是否需要 Reviewer 审核
#
# 路由表设计原则：
#   1. 查询类操作（*_query）使用 DIRECT 模式，无需审核
#   2. 操作类操作（*_action, *_send, *_create）使用 SELECTOR 模式，需要审核
#   3. 跨系统/复杂任务使用 SWARM 模式，需要审核
# -------------------------------------------------------------------------
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

# 需要审核的敏感操作关键词
# -------------------------------------------------------------------------
# 用于判断操作是否需要 Reviewer 审核
# 包含这些关键词的操作会被标记为 review_required=True
# -------------------------------------------------------------------------
REVIEW_REQUIRED_ACTIONS = [
    "submit_approval_action",
    "send_email",
    "modify_data",
    "delete_record",
    "financial_operation",
]

# 意图分类 Prompt
# -------------------------------------------------------------------------
# 用于指导 LLM 进行意图分类的系统提示词
# 包含：
#   - 可选意图标签列表及说明
#   - 分类示例
#   - 输出格式要求（JSON）
# -------------------------------------------------------------------------
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
- "先查客户CRM信息再发跟进邮件" -> cross_system（涉及CRM和邮件两个系统联动）
- "查一下日程然后发邮件通知参会人" -> cross_system（涉及日程和邮件两个系统联动）
- "查报销进度并催办审批" -> cross_system（涉及财务和审批两个系统联动）
- "帮我分析一下这个客户的风险并生成报告" -> complex_task（需要多步骤深度分析）

请严格按以下 JSON 格式输出，不要输出其他内容：
{"intent": "意图标签", "confidence": 0.0-1.0, "sub_tasks": ["子任务1", "子任务2"], "orchestration_mode": null}

orchestration_mode 说明（仅 SWARM 模式需要填写）：
- null 或不填: 默认顺序编排，子任务按先后依赖关系依次执行（大多数场景）
- "parallel": 多维度并行收集信息，各维度无依赖关系（如"从市场、财务、风险三个角度分析项目"）
- "debate": 需要多角度深度推理和验证的决策问题（如"评审这个技术方案的可行性"）
- "vote": 需要高准确率的事实性判断（如"判断这段文本属于哪个类别"）
"""

# Supervisor 系统提示词
# -------------------------------------------------------------------------
# Supervisor Agent 的系统提示词，定义其职责、可调度 Agent 和安全规则
# -------------------------------------------------------------------------
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

    这是意图分类的主入口函数，采用三级缓存策略优化性能。

    执行流程：
    -------------------------------------------------------------------------
    步骤 1：尝试从语义缓存获取
      - 使用语义相似度匹配，命中则直接返回缓存结果
      - 语义缓存适用于语义相近但表述不同的用户消息

    步骤 2：尝试从 L1 精确缓存获取
      - 使用精确键匹配，命中则直接返回缓存结果
      - L1 缓存适用于完全相同的用户消息

    步骤 3：调用轻量级 LLM 进行意图分类
      - 使用 qwen-turbo 模型（低延迟、低成本）
      - 输入：意图分类 Prompt + 用户消息
      - 输出：JSON 格式的意图分类结果

    步骤 4：路由解析
      - 调用 _resolve_routing() 根据意图查找路由信息
      - 从 Capability Card Registry 动态查找（YAML 配置外置化）
      - 未匹配时降级到通用 OfficeAssistant

    步骤 5：缓存结果
      - 写入 L1 精确缓存（TTL 5 分钟）
      - 写入语义缓存（TTL 10 分钟）

    步骤 6：记录指标
      - 记录调用成功/失败和耗时
    -------------------------------------------------------------------------

    Args:
        user_message: 用户消息文本

    Returns:
        IntentResult 意图分类结果，包含：
            - intent: 意图标签
            - confidence: 置信度
            - target_agent: 目标 Agent
            - collaboration_mode: 协作模式
            - review_required: 是否需要审核
            - sub_tasks: 子任务列表

    异常处理：
        - 所有异常被捕获，返回默认意图（general）
        - 确保系统在意图分类失败时仍能正常工作
    """
    import time

    start_time = time.time()

    # 1. 尝试从 L1 精确缓存获取（O(1)，优先级最高）
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

    # 2. 尝试从语义缓存获取（语义相似匹配，O(n)）
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

        # 提取 CoT 推理过程
        reasoning_text = ""
        if "<reasoning>" in content and "</reasoning>" in content:
            try:
                reasoning_start = content.index("<reasoning>") + len("<reasoning>")
                reasoning_end = content.index("</reasoning>")
                reasoning_text = content[reasoning_start:reasoning_end].strip()
                # 从 content 中移除推理标签，只保留 JSON
                content = content[:content.index("<reasoning>")] + content[reasoning_end + len("</reasoning>"):]
                content = content.strip()
            except (ValueError, IndexError):
                pass

        result = json.loads(content)
        intent = result.get("intent", "general")
        confidence = result.get("confidence", 0.5)
        sub_tasks = result.get("sub_tasks", [])
        orchestration_mode = result.get("orchestration_mode")

        # 查路由表：优先使用 Capability Card 动态路由
        routing = _resolve_routing(intent)

        intent_result = IntentResult(
            intent=intent,
            confidence=confidence,
            target_agent=routing["agent"],
            collaboration_mode=CollaborationMode(routing["mode"]),
            review_required=routing["review"],
            sub_tasks=sub_tasks,
            orchestration_mode=orchestration_mode,
            reasoning=reasoning_text,
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

    Supervisor Agent 是 SWARM 模式的核心，负责：
      - 理解用户需求
      - 拆解复杂任务
      - 调度领域 Agent
      - 汇总执行结果

    模型配置：
      - 使用 qwen-max（高推理能力）
      - 确保任务规划的准确性和完整性

    Prompt 加载：
      - 优先从 Prompt Registry 加载外置 Prompt
      - 降级到代码内嵌的 SUPERVISOR_SYSTEM_PROMPT

    Returns:
        AssistantAgent 实例，可直接用于 SWARM 团队
    """
    prompt = _get_supervisor_prompt()
    return AssistantAgent(
        name="Supervisor",
        model_client=get_supervisor_client(),
        system_message=prompt,
    )


def _get_supervisor_prompt() -> str:
    """获取 Supervisor 的 System Prompt

    Prompt 加载策略：
      1. 优先从 Prompt Registry 加载外置版本管理的 Prompt
      2. 降级到代码内嵌的 SUPERVISOR_SYSTEM_PROMPT 默认值

    Prompt Registry 的优势：
      - 支持热更新，无需重启服务
      - 支持版本管理和回滚
      - 支持多环境配置（开发/测试/生产）

    Returns:
        System Prompt 字符串
    """
    try:
        from agent.core.prompt.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        prompt = registry.get_prompt_sync("Supervisor")
        if prompt:
            return prompt
    except Exception:
        pass
    return SUPERVISOR_SYSTEM_PROMPT


def _get_intent_classification_prompt() -> str:
    """获取意图分类 Prompt

    Prompt 加载策略：
      1. 优先从 Prompt Registry 加载外置版本管理的 Prompt
      2. 降级到代码内嵌的 INTENT_CLASSIFICATION_PROMPT 默认值

    Returns:
        意图分类 Prompt 字符串
    """
    try:
        from agent.core.prompt.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        prompt = registry.get_prompt_sync("IntentClassifier")
        if prompt:
            return prompt
    except Exception:
        pass
    return INTENT_CLASSIFICATION_PROMPT


def _resolve_routing(intent: str) -> dict[str, Any]:
    """根据意图解析路由信息

    路由解析策略：
    -------------------------------------------------------------------------
    从 Capability Card Registry 动态查找（唯一来源）
      - Capability Card 是 Agent 能力的元数据注册表
      - 支持从 YAML 文件动态加载，实现配置外置化
      - 根据意图匹配最合适的 Agent
      - 优先使用 intent_configs 中的精确配置
      - 未配置时根据意图名称和安全约束推断

    路由信息包含：
      - agent: 目标 Agent 名称
      - mode: 协作模式（direct/selector/swarm）
      - review: 是否需要 Reviewer 审核

    Args:
        intent: 意图标签，如 "email_send", "approval_query"

    Returns:
        路由信息字典 {"agent": ..., "mode": ..., "review": ...}
    """
    from agent.core.skill.capability_card import get_capability_registry
    registry = get_capability_registry()
    routing = registry.get_routing_for_intent(intent)

    if routing:
        return routing

    # 未找到匹配的 Agent，返回通用降级配置
    logger.warning("意图 %s 未匹配到任何 Agent，使用通用降级配置", intent)
    return {"agent": "OfficeAssistant", "mode": "direct", "review": False}
