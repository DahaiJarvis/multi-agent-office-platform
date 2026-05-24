"""步骤影响面分析引擎

当用户拒绝/跳过某个步骤时，分析该步骤的跳过对后续步骤的影响，
确定哪些后续步骤需要级联跳过，哪些可以继续执行。

核心逻辑：
  1. 结构性依赖：HUMAN_CONFIRM 步骤被跳过时，其守护的 AGENT_CALL 也应跳过
  2. 声明式依赖：步骤定义中的 depends_on 字段声明的依赖关系
  3. 级联传播：被跳过步骤的依赖者如果也被跳过，继续传播

依赖关系来源：
  - 结构性：HUMAN_CONFIRM 后紧跟的 AGENT_CALL（同 agent_name）自动构成依赖
  - 声明式：步骤定义中 depends_on 字段显式声明的依赖

使用方式：
  from agent.core.workflow.impact_analyzer import analyze_skip_impact
  affected = await analyze_skip_impact(execution, skipped_step_index)
"""

import logging
from typing import Any

from agent.core.workflow.task_checkpoint import (
    TaskExecution,
    StepType,
    StepStatus,
    StepCheckpoint,
    get_task_checkpoint_store,
)

logger = logging.getLogger(__name__)


async def analyze_skip_impact(
    execution: TaskExecution,
    skipped_checkpoint_index: int,
    comment: str = "",
) -> list[int]:
    """分析跳过某步骤后受影响的后续步骤索引列表

    根据步骤间的依赖关系，计算当指定步骤被跳过时，
    哪些后续步骤也应该被级联跳过。

    分析流程：
      1. 将 checkpoint_index 转换为 steps 列表中的 step_index
      2. 从该步骤开始，向后扫描所有后续步骤
      3. 对每个后续步骤检查是否依赖被跳过的步骤
      4. 依赖检查包括：结构性依赖和声明式依赖
      5. 被判定为依赖的步骤加入受影响集合，并继续级联分析

    Args:
        execution: 任务执行记录
        skipped_checkpoint_index: 被跳过步骤的检查点索引（从1开始）
        comment: 用户备注

    Returns:
        受影响的步骤检查点索引列表（不包含原始跳过步骤本身）
    """
    skipped_step_index = skipped_checkpoint_index - 1
    steps = execution.steps

    if skipped_step_index < 0 or skipped_step_index >= len(steps):
        logger.warning(
            "影响面分析-步骤索引越界: checkpoint_index=%d steps_count=%d",
            skipped_checkpoint_index, len(steps),
        )
        return []

    skipped_step = steps[skipped_step_index]
    affected_checkpoint_indices: list[int] = []
    skipped_set: set[int] = {skipped_step_index}

    logger.info(
        "影响面分析开始: execution=%s skipped_step=%d(%s)",
        execution.execution_id, skipped_checkpoint_index,
        skipped_step.get("name", ""),
    )

    for i in range(skipped_step_index + 1, len(steps)):
        step = steps[i]
        if _is_step_dependent(step, i, steps, skipped_set):
            skipped_set.add(i)
            affected_checkpoint_indices.append(i + 1)
            logger.info(
                "影响面分析-级联跳过: step=%d(%s) 依赖被跳过步骤",
                i + 1, step.get("name", ""),
            )

    logger.info(
        "影响面分析完成: execution=%s 原始跳过=%d 级联跳过=%s",
        execution.execution_id, skipped_checkpoint_index,
        affected_checkpoint_indices,
    )

    return affected_checkpoint_indices


def _is_step_dependent(
    step: dict[str, Any],
    step_index: int,
    steps: list[dict[str, Any]],
    skipped_set: set[int],
) -> bool:
    """判断步骤是否依赖被跳过的步骤

    依赖判定规则：
      1. 结构性依赖：前一步骤是 HUMAN_CONFIRM 且被跳过，
         当前步骤是 AGENT_CALL 且 agent_name 相同
      2. 声明式依赖：步骤的 depends_on 字段包含被跳过步骤的索引
      3. AGGREGATE 步骤豁免：汇总步骤始终执行，汇总已完成步骤的结果，
         不因部分步骤被跳过而级联跳过

    Args:
        step: 待判定的步骤定义
        step_index: 步骤在 steps 列表中的索引
        steps: 完整步骤列表
        skipped_set: 已被跳过的步骤索引集合

    Returns:
        True 表示该步骤依赖被跳过的步骤，应级联跳过
    """
    curr_type = step.get("type", "")

    if curr_type == StepType.AGGREGATE.value:
        return False

    prev_index = step_index - 1
    if prev_index >= 0 and prev_index in skipped_set:
        prev_step = steps[prev_index]
        prev_type = prev_step.get("type", "")
        prev_agent = prev_step.get("agent_name", "")
        curr_agent = step.get("agent_name", "")

        if prev_type == StepType.HUMAN_CONFIRM.value and curr_type == StepType.AGENT_CALL.value:
            if prev_agent and curr_agent and prev_agent == curr_agent:
                return True

    depends_on = step.get("depends_on", [])
    for dep_index in depends_on:
        if dep_index in skipped_set:
            return True

    return False


async def apply_skip_with_impact(
    execution: TaskExecution,
    skipped_checkpoint_index: int,
    comment: str = "",
) -> list[StepCheckpoint]:
    """执行跳过步骤及级联跳过，保存检查点

    当用户选择跳过某步骤时，执行以下操作：
      1. 分析受影响的后续步骤
      2. 将原始跳过步骤和所有受影响步骤标记为 SKIPPED
      3. 保存检查点到存储

    Args:
        execution: 任务执行记录
        skipped_checkpoint_index: 被跳过步骤的检查点索引
        comment: 用户备注

    Returns:
        所有被标记为 SKIPPED 的检查点列表
    """
    store = get_task_checkpoint_store()
    affected_indices = await analyze_skip_impact(execution, skipped_checkpoint_index, comment)

    all_skipped = [skipped_checkpoint_index] + affected_indices
    skipped_checkpoints: list[StepCheckpoint] = []

    for cp_index in all_skipped:
        step_index = cp_index - 1
        if step_index < 0 or step_index >= len(execution.steps):
            continue

        step = execution.steps[step_index]
        step_type = StepType(step.get("type", "agent_call"))
        step_name = step.get("name", "")
        agent_name = step.get("agent_name", "")

        checkpoint = StepCheckpoint(
            step_index=cp_index,
            step_type=step_type,
            step_name=step_name,
            agent_name=agent_name,
            status=StepStatus.SKIPPED,
            input_data=step,
            error=f"用户跳过: {comment}" if cp_index == skipped_checkpoint_index else f"级联跳过(依赖步骤被跳过): {comment}",
            failure_reason="user_rejected" if cp_index == skipped_checkpoint_index else "cascade_skip",
        )

        await store.save_checkpoint(execution.execution_id, checkpoint)
        skipped_checkpoints.append(checkpoint)

        logger.info(
            "步骤已跳过: execution=%s step=%d(%s) reason=%s",
            execution.execution_id, cp_index, step_name,
            checkpoint.failure_reason,
        )

    return skipped_checkpoints
