"""任务路由

Supervisor 的路由决策逻辑：意图分类 -> 路由表匹配 -> 团队创建 -> 任务执行
"""

import logging
import time
from typing import Any

from agent.agents.supervisor import (
    classify_intent,
    create_supervisor_agent,
    IntentResult,
    CollaborationMode,
)
from agent.teams.team_factory import create_team
from observability.metrics import record_agent_call
from observability.tracing import langfuse_tracer

logger = logging.getLogger(__name__)

# 置信度阈值，低于此值需要用户确认
CONFIDENCE_THRESHOLD = 0.7


async def route_and_execute(
    user_message: str,
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    """路由并执行用户请求

    完整流程：
    1. 意图分类
    2. 置信度校验
    3. 路由表匹配
    4. 创建团队
    5. 执行任务
    6. 返回结果

    Args:
        user_message: 用户消息
        session_id: 会话ID
        user_id: 用户ID

    Returns:
        执行结果字典
    """
    start_time = time.time()

    # 1. 意图分类
    intent = await classify_intent(user_message)
    logger.info(
        "意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
        intent.intent,
        intent.confidence,
        intent.target_agent,
        intent.collaboration_mode.value,
    )

    # 2. 置信度校验
    if intent.confidence < CONFIDENCE_THRESHOLD:
        return {
            "status": "clarification_needed",
            "message": f"我不太确定您的意图（置信度: {intent.confidence:.0%}），请更详细地描述您的需求。",
            "intent": intent.intent,
            "confidence": intent.confidence,
        }

    # 3. 创建团队
    try:
        team = await create_team(intent)
    except Exception as e:
        logger.error("创建团队失败: %s", e)
        return {
            "status": "error",
            "message": "系统暂时无法处理您的请求，请稍后重试。",
            "intent": intent.intent,
        }

    # 4. 执行任务
    try:
        if intent.collaboration_mode == CollaborationMode.DIRECT:
            # 单 Agent 直接执行
            result = await team.run(task=user_message)
            output = result.messages[-1].content if result.messages else "处理完成"
        else:
            # 团队协作执行
            result = await team.run(task=user_message)
            output = result.messages[-1].content if result.messages else "处理完成"

        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "success", duration)

        # 记录到 Langfuse
        langfuse_tracer.trace_agent_call(
            trace_id=session_id,
            agent_name=intent.target_agent,
            input_text=user_message,
            output_text=output,
            metadata={
                "user_id": user_id,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
                "review_required": intent.review_required,
            },
        )

        return {
            "status": "success",
            "message": output,
            "agent_name": intent.target_agent,
            "intent": intent.intent,
            "collaboration_mode": intent.collaboration_mode.value,
        }

    except Exception as e:
        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "error", duration)
        logger.error("任务执行失败: agent=%s error=%s", intent.target_agent, e)

        return {
            "status": "error",
            "message": f"任务执行失败: {str(e)}",
            "agent_name": intent.target_agent,
            "intent": intent.intent,
        }
