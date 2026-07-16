"""CoT 推理指令模板

为不同类型的 Agent 提供结构化 Chain of Thought 推理指令片段。
这些片段会被注入到 Agent 的 System Prompt 中，指导 LLM
在关键决策点输出结构化的推理过程。

设计原则：
  1. 推理指令作为 Prompt 片段追加到现有 System Prompt 末尾
  2. 推理输出使用 <reasoning>...</reasoning> 标签包裹，
     不影响原有输出格式，向后兼容
  3. 不同 Agent 类型的推理指令侧重不同维度

使用方式：
  from agent.core.reasoning.cot_prompts import get_cot_prompt
  cot_fragment = get_cot_prompt("intent_classifier")
  full_prompt = base_prompt + cot_fragment
"""

from agent.core.reasoning.chain import ReasoningType


# 意图分类 CoT 推理指令
# -------------------------------------------------------------------------
# 在意图分类时，要求 LLM 先分析用户消息的关键信息，
# 再推导出意图标签，提升分类准确率
# -------------------------------------------------------------------------
INTENT_CLASSIFIER_COT = """

推理要求：
在分类前，请先进行推理分析，按以下结构输出：

<reasoning>
1. 关键信息提取：用户消息中包含哪些关键操作词和对象？
2. 系统匹配：这些操作词对应哪些业务系统（审批/邮件/日程/CRM/HR/财务/知识库）？
3. 操作类型判断：是查询类还是操作类？是否涉及敏感操作？
4. 复杂度评估：是否涉及多个系统联动？是否需要多步骤完成？
5. 结论推导：基于以上分析，最合适的意图标签是什么？
</reasoning>

推理完成后，再输出分类结果的 JSON。
"""

# Supervisor 任务规划 CoT 推理指令
# -------------------------------------------------------------------------
# 在 SWARM 模式下，Supervisor 需要将复杂任务拆解为子任务，
# CoT 指导其先分析任务结构再规划步骤
# -------------------------------------------------------------------------
SUPERVISOR_PLANNING_COT = """

推理要求：
在分配任务前，请先进行推理分析，按以下结构输出：

<reasoning>
1. 需求理解：用户的核心需求是什么？涉及哪些业务领域？
2. 依赖分析：各子任务之间是否有先后依赖关系？
3. Agent匹配：每个子任务最适合由哪个 Agent 执行？
4. 风险识别：是否存在敏感操作？是否需要审核？
5. 执行规划：按什么顺序执行？哪些可以并行？
</reasoning>

推理完成后，再进行任务分配和 Handoff。
"""

# Reviewer 审核决策 CoT 推理指令
# -------------------------------------------------------------------------
# Reviewer 的审核决策直接影响操作安全性，
# CoT 确保审核过程有据可依
# -------------------------------------------------------------------------
REVIEWER_COT = """

推理要求：
在做出审核决定前，请先进行推理分析，按以下结构输出：

<reasoning>
1. 操作识别：待审核的具体操作是什么？涉及哪些数据和对象？
2. 权限核实：执行者是否有权进行此操作？
3. 合规检查：操作是否符合企业规章制度？
4. 风险评估：操作可能带来什么风险？风险等级如何？
5. 决策推导：基于以上分析，审核结论是什么？
</reasoning>

推理完成后，再输出审核决定（REVIEW_PASSED / REVIEW_REJECTED / REVIEW_NEED_INFO）。
"""

# 领域 Agent 工具选择 CoT 推理指令
# -------------------------------------------------------------------------
# 当领域 Agent 有多个工具可选时，CoT 指导其先分析再选择
# -------------------------------------------------------------------------
DOMAIN_AGENT_COT = """

推理要求：
在调用工具前，如果有多个可选工具，请先进行推理分析：

<reasoning>
1. 需求分析：当前需要完成什么操作？
2. 工具评估：有哪些可用工具？各自的能力和限制是什么？
3. 选择依据：基于当前需求，最合适的工具是什么？为什么？
</reasoning>

推理完成后，再调用选定的工具。
"""

# CoT 指令映射表
_COT_PROMPTS: dict[str, str] = {
    "intent_classifier": INTENT_CLASSIFIER_COT,
    "supervisor": SUPERVISOR_PLANNING_COT,
    "reviewer": REVIEWER_COT,
    "domain_agent": DOMAIN_AGENT_COT,
}


def get_cot_prompt(agent_type: str) -> str:
    """获取指定 Agent 类型的 CoT 推理指令片段

    Args:
        agent_type: Agent 类型标识，支持：
            - intent_classifier: 意图分类器
            - supervisor: Supervisor 规划 Agent
            - reviewer: 审核 Agent
            - domain_agent: 领域 Agent

    Returns:
        CoT 推理指令文本，未匹配时返回空字符串
    """
    return _COT_PROMPTS.get(agent_type, "")


def get_cot_prompt_by_reasoning_type(reasoning_type: ReasoningType) -> str:
    """根据推理类型获取对应的 CoT 推理指令

    Args:
        reasoning_type: 推理类型枚举

    Returns:
        CoT 推理指令文本
    """
    mapping = {
        ReasoningType.INTENT: INTENT_CLASSIFIER_COT,
        ReasoningType.PLANNING: SUPERVISOR_PLANNING_COT,
        ReasoningType.REVIEW: REVIEWER_COT,
        ReasoningType.TOOL_SELECTION: DOMAIN_AGENT_COT,
    }
    return mapping.get(reasoning_type, "")
