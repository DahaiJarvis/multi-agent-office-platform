"""Agent 路由桥接模块

为工作流引擎提供 Agent 调用能力，桥接工作流节点与现有路由系统。

核心功能：
  - route_to_agent(): 将消息路由到指定 Agent 并执行
  - 复用 agent.teams.routing 的意图分类和团队创建机制

与直接调用 route_and_execute 的区别：
  - route_and_execute: 完整路由流程（意图分类 -> 创建团队 -> 执行）
  - route_to_agent: 直接指定 Agent 执行，跳过意图分类

使用场景：
  - 工作流引擎的 Agent 节点执行
  - 需要明确指定目标 Agent 的场景
"""

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


async def route_to_agent(
    agent_name: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将消息路由到指定 Agent 并执行

    直接指定目标 Agent，跳过意图分类环节，
    创建 DIRECT 模式的单 Agent 团队执行任务。

    执行流程：
    -------------------------------------------------------------------------
    1. 构建 IntentResult（指定目标 Agent，DIRECT 模式）
    2. 调用 create_team() 创建单 Agent 团队
    3. 调用 ExecutionController 执行任务
    4. 提取并返回 Agent 输出
    -------------------------------------------------------------------------

    Args:
        agent_name: 目标 Agent 名称，如 "EmailAgent"
        message: 输入消息文本
        context: 上下文变量字典（工作流引擎传入）

    Returns:
        执行结果字典，包含：
        - status: 执行状态（success / error）
        - response: Agent 输出文本
        - agent_name: 执行的 Agent 名称
    """
    if not agent_name:
        return {
            "status": "error",
            "response": "未指定目标 Agent",
            "agent_name": "",
        }

    if not message:
        return {
            "status": "error",
            "response": "输入消息为空",
            "agent_name": agent_name,
        }

    try:
        from agent.agents.supervisor import IntentResult, CollaborationMode
        from agent.teams.team_factory import create_team
        from agent.teams.execution_controller import get_execution_controller

        intent = IntentResult(
            intent="workflow_task",
            confidence=1.0,
            target_agent=agent_name,
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )

        team = await create_team(intent)

        session_id = context.get("session_id", str(uuid.uuid4())) if context else str(uuid.uuid4())
        user_id = context.get("user_id", "workflow") if context else "workflow"

        controller = get_execution_controller()
        result, exec_meta = await controller.execute_with_control(
            team, message, session_id, user_id,
        )

        if exec_meta.status == "timeout":
            return {
                "status": "error",
                "response": f"Agent {agent_name} 执行超时",
                "agent_name": agent_name,
            }

        if exec_meta.status == "error" and result is None:
            return {
                "status": "error",
                "response": f"Agent {agent_name} 执行失败: {exec_meta.message}",
                "agent_name": agent_name,
            }

        try:
            from agent.teams.advanced_orchestration import _extract_agent_response
            output = _extract_agent_response(result) if result else "处理完成"
        except Exception:
            output = str(result) if result else "处理完成"

        return {
            "status": "success",
            "response": output,
            "agent_name": agent_name,
        }

    except Exception as e:
        logger.error("Agent 路由执行失败: agent=%s, error=%s", agent_name, e)
        return {
            "status": "error",
            "response": f"Agent {agent_name} 路由失败: {str(e)}",
            "agent_name": agent_name,
        }
