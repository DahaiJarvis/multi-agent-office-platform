"""Reviewer Agent - 安全审核

职责：审核敏感操作的合规性和安全性，发现风险可直接否决操作。
模型：qwen-max（低温度，确保审核严谨性）
工具：只读查询工具（OA/审批/HR/财务），用于核实信息但不执行写操作
"""

import logging

from autogen_agentchat.agents import AssistantAgent

from agent.core.model_client import get_reviewer_client
from agent.core.mcp_integration import load_agent_tools

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """你是安全审核 Agent（Reviewer），负责审核敏感操作的合规性和安全性。

核心职责：
1. 审核操作是否合规（是否符合企业规章制度）
2. 校验操作权限（用户是否有权执行该操作）
3. 检测潜在风险（数据泄露、越权操作、异常行为）
4. 对高风险操作行使否决权

审核流程：
1. 接收待审核的操作描述
2. 核实操作涉及的数据和对象（使用只读工具查询）
3. 评估操作风险等级
4. 做出审核决定：通过 / 否决 / 需要补充信息

风险等级判定：
- 低风险：普通数据查询、个人日程操作 -> 直接通过
- 中风险：审批操作、邮件发送、客户数据修改 -> 核实后通过
- 高风险：批量操作、跨部门数据访问、金额操作 -> 严格审核，可能否决

审核规则：
1. 审批操作：确认审批人权限、审批金额是否在授权范围内
2. 邮件发送：检查收件人范围、是否包含敏感数据附件
3. 数据修改：确认修改范围、是否影响其他业务
4. 删除操作：原则上不建议删除，建议标记为无效
5. 财务操作：金额超限需升级审核

否决规则（一票否决）：
- 操作超出用户权限范围
- 涉及核心商业机密的未授权访问
- 批量删除或批量修改超过10条记录
- 向外部邮箱发送包含客户数据的邮件
- 未经授权的财务转账操作

输出格式：
- 审核通过：输出 "REVIEW_PASSED: [通过原因]"
- 审核否决：输出 "REVIEW_REJECTED: [否决原因]"
- 需补充信息：输出 "REVIEW_NEED_INFO: [需要补充的信息]"

重要：你的审核决定具有最高优先级，即使其他 Agent 建议执行，你也有权否决。安全第一。"""


# 需要审核的敏感操作关键词
SENSITIVE_ACTION_KEYWORDS = [
    "approve",
    "reject",
    "send_email",
    "delete",
    "transfer",
    "modify",
    "update_opportunity",
    "cancel_event",
    "submit",
    "financial",
]


def is_sensitive_action(action_description: str) -> bool:
    """判断操作是否为敏感操作

    Args:
        action_description: 操作描述

    Returns:
        是否为敏感操作
    """
    action_lower = action_description.lower()
    return any(keyword in action_lower for keyword in SENSITIVE_ACTION_KEYWORDS)


async def create_reviewer_agent() -> AssistantAgent:
    """创建 Reviewer Agent 实例

    Reviewer 使用 qwen-max 低温度模型，确保审核判断的稳定性和严谨性。
    工具绑定只读查询工具，可核实信息但不执行写操作。
    """
    tools = await load_agent_tools("Reviewer")
    return AssistantAgent(
        name="Reviewer",
        model_client=get_reviewer_client(),
        tools=tools,
        system_message=REVIEWER_SYSTEM_PROMPT,
    )
