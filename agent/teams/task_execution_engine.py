"""任务编排引擎

将多Agent协作任务拆解为有序步骤，逐步执行并保存检查点，
支持从断点恢复执行，确保任务在中断后可继续。

核心能力：
  - 步骤规划：根据意图分类结果，规划执行步骤
  - 步骤执行：逐步执行，每步保存检查点
  - 断点恢复：从已保存的检查点恢复执行
  - 心跳维护：定期更新心跳，用于检测中断

与 routing.py 的关系：
  routing.py 负责意图分类、团队创建、上下文注入等，
  TaskExecutionEngine 在此基础上增加步骤编排和检查点保存。
  routing.py 的 route_and_execute() 委托给本引擎执行。

执行流程：
  1. 创建 TaskExecution 记录
  2. 根据意图规划步骤列表
  3. 逐步执行，每步保存 StepCheckpoint
  4. 异常时标记状态，支持恢复
  5. 完成后归档
"""

import logging
import time
from typing import Any

from agent.core.task_checkpoint import (
    TaskExecution,
    TaskCheckpointStore,
    TaskStatus,
    StepCheckpoint,
    StepType,
    StepStatus,
    FailurePolicy,
    get_task_checkpoint_store,
)
from agent.core.event_bus import publish_event, EventType
from agent.agents.supervisor import IntentResult, CollaborationMode
from agent.teams.team_factory import create_team
from agent.teams.execution_controller import get_execution_controller
from agent.teams.fault_isolation import FaultIsolationPolicy, get_fault_isolation_policy
from agent.core.human_confirm import (
    HumanConfirmManager,
    ConfirmType,
    get_human_confirm_manager,
)
from agent.core.session_manager import SessionState, get_session_manager

logger = logging.getLogger(__name__)


class TaskExecutionEngine:
    """任务编排引擎

    将多Agent协作任务拆解为有序步骤，逐步执行并保存检查点。
    支持从断点恢复执行。

    步骤类型：
      - intent_classify: 意图分类（已完成，由routing.py传入）
      - agent_call: 调用Agent执行
      - aggregate: 汇总多个Agent结果
    """

    def __init__(
        self,
        checkpoint_store: TaskCheckpointStore | None = None,
        fault_policy: FaultIsolationPolicy | None = None,
        confirm_manager: HumanConfirmManager | None = None,
    ) -> None:
        self._store = checkpoint_store or get_task_checkpoint_store()
        self._fault_policy = fault_policy or get_fault_isolation_policy()
        self._confirm_manager = confirm_manager or get_human_confirm_manager()

    async def execute(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
        intent: IntentResult,
        session: SessionState | None = None,
        knowledge_base_id: str | None = None,
        failure_policy: FailurePolicy = FailurePolicy.RELAXED,
    ) -> dict[str, Any]:
        """执行任务（同步模式，带检查点）

        Args:
            user_message: 用户原始消息
            session_id: 会话ID
            user_id: 用户ID
            intent: 意图分类结果（由routing.py已完成）
            session: 会话状态
            knowledge_base_id: 知识库ID
            failure_policy: 故障策略

        Returns:
            执行结果字典，与routing.py返回格式一致
        """
        # 创建执行记录
        execution = TaskExecution(
            session_id=session_id,
            user_id=user_id,
            original_message=user_message,
            intent_result={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "collaboration_mode": intent.collaboration_mode.value,
                "review_required": intent.review_required,
                "sub_tasks": intent.sub_tasks,
            },
            collaboration_mode=intent.collaboration_mode.value,
            failure_policy=failure_policy,
        )

        # 保存意图分类步骤的检查点（已完成）
        intent_checkpoint = StepCheckpoint(
            step_index=0,
            step_type=StepType.INTENT_CLASSIFY,
            step_name="意图分类",
            status=StepStatus.COMPLETED,
            output_data={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "collaboration_mode": intent.collaboration_mode.value,
            },
        )
        execution.checkpoints.append(intent_checkpoint)

        # 规划执行步骤
        steps = self._plan_steps(intent)
        execution.steps = steps

        # 保存执行记录
        await self._store.create_execution(execution)

        # 发布任务开始事件
        await self._publish_task_event(execution, "task_started")

        # 逐步执行
        for step_index in range(len(steps)):
            step = steps[step_index]
            step_checkpoint_index = step_index + 1  # +1 因为步骤0是意图分类

            # 跳过已完成的步骤（断点恢复时）
            if step_checkpoint_index < execution.current_step:
                continue

            # 更新心跳
            await self._store.update_heartbeat(execution.execution_id)

            # 更新任务状态为运行中
            execution.status = TaskStatus.RUNNING
            await self._store.update_execution(execution)

            try:
                checkpoint = await self._execute_step(
                    execution, step, step_checkpoint_index,
                    user_message, session_id, user_id, session, knowledge_base_id,
                )
                await self._store.save_checkpoint(execution.execution_id, checkpoint)

                # 如果步骤失败，启动故障隔离策略
                if checkpoint.status == StepStatus.FAILED:
                    # 使用故障隔离策略处理步骤失败
                    recovery_checkpoint = await self._fault_policy.handle_step_failure(
                        execution, step_checkpoint_index,
                        error=Exception(checkpoint.error),
                    )
                    await self._store.save_checkpoint(execution.execution_id, recovery_checkpoint)

                    # 重新加载执行记录（故障隔离策略可能已修改状态）
                    execution = await self._store.get_execution(execution.execution_id) or execution

                    # 严格模式/手动模式下任务可能已暂停
                    if execution.status == TaskStatus.PAUSED:
                        await self._publish_task_event(execution, "task_paused")
                        return self._build_result(execution)

                    # 降级完成的步骤也视为继续
                    if recovery_checkpoint.status == StepStatus.DEGRADED:
                        logger.info(
                            "步骤降级执行完成: step=%s fallback=%s",
                            recovery_checkpoint.step_name, recovery_checkpoint.fallback_used,
                        )

            except Exception as e:
                logger.error("步骤执行异常: step=%s error=%s", step.get("name", ""), e)

                # 使用故障隔离策略处理异常
                recovery_checkpoint = await self._fault_policy.handle_step_failure(
                    execution, step_checkpoint_index, error=e,
                )
                await self._store.save_checkpoint(execution.execution_id, recovery_checkpoint)

                # 重新加载执行记录
                execution = await self._store.get_execution(execution.execution_id) or execution

                if execution.status == TaskStatus.PAUSED:
                    await self._publish_task_event(execution, "task_paused")
                    return self._build_result(execution)

        # 所有步骤完成
        execution.status = TaskStatus.COMPLETED
        execution.updated_at = time.time()
        await self._store.update_execution(execution)
        await self._store.remove_from_running(execution.execution_id)
        await self._publish_task_event(execution, "task_completed")

        return self._build_result(execution)

    async def resume(
        self,
        execution_id: str,
        session_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """从断点恢复执行

        从已保存的检查点恢复任务执行，跳过已完成的步骤。

        Args:
            execution_id: 执行记录ID
            session_id: 会话ID
            user_id: 用户ID

        Returns:
            执行结果字典
        """
        execution = await self._store.get_execution(execution_id)
        if execution is None:
            return {"status": "error", "message": f"执行记录不存在: {execution_id}"}

        if execution.status not in (TaskStatus.INTERRUPTED, TaskStatus.PAUSED):
            return {
                "status": "error",
                "message": f"任务状态不允许恢复: {execution.status.value}",
            }

        # 恢复为运行状态
        execution.status = TaskStatus.RUNNING
        execution.error = ""
        await self._store.update_execution(execution)

        await self._publish_task_event(execution, "task_resumed")

        # 获取会话状态
        session_mgr = await get_session_manager()
        session = await session_mgr.get_session(session_id)

        # 从断点继续执行
        for step_index in range(len(execution.steps)):
            step = execution.steps[step_index]
            step_checkpoint_index = step_index + 1

            if step_checkpoint_index < execution.current_step:
                continue

            await self._store.update_heartbeat(execution.execution_id)

            try:
                checkpoint = await self._execute_step(
                    execution, step, step_checkpoint_index,
                    execution.original_message, session_id, user_id, session, None,
                )
                await self._store.save_checkpoint(execution.execution_id, checkpoint)

                if checkpoint.status == StepStatus.FAILED:
                    # 使用故障隔离策略处理步骤失败
                    recovery_checkpoint = await self._fault_policy.handle_step_failure(
                        execution, step_checkpoint_index,
                        error=Exception(checkpoint.error),
                    )
                    await self._store.save_checkpoint(execution.execution_id, recovery_checkpoint)

                    # 重新加载执行记录
                    execution = await self._store.get_execution(execution.execution_id) or execution

                    if execution.status == TaskStatus.PAUSED:
                        return self._build_result(execution)

            except Exception as e:
                logger.error("恢复执行步骤异常: step=%s error=%s", step.get("name", ""), e)

                # 使用故障隔离策略处理异常
                recovery_checkpoint = await self._fault_policy.handle_step_failure(
                    execution, step_checkpoint_index, error=e,
                )
                await self._store.save_checkpoint(execution.execution_id, recovery_checkpoint)

                # 重新加载执行记录
                execution = await self._store.get_execution(execution.execution_id) or execution

                if execution.status == TaskStatus.PAUSED:
                    return self._build_result(execution)

        execution.status = TaskStatus.COMPLETED
        execution.updated_at = time.time()
        await self._store.update_execution(execution)
        await self._store.remove_from_running(execution.execution_id)
        await self._publish_task_event(execution, "task_completed")

        return self._build_result(execution)

    def _plan_steps(self, intent: IntentResult) -> list[dict[str, Any]]:
        """根据意图规划执行步骤

        根据协作模式生成不同的步骤列表：
          - DIRECT: 单步Agent调用
          - SELECTOR: Agent调用 + 审核
          - SWARM: 多Agent调用 + 汇总

        Args:
            intent: 意图分类结果

        Returns:
            步骤列表，每个步骤包含 type, name, agent_name 等字段
        """
        steps: list[dict[str, Any]] = []

        if intent.collaboration_mode == CollaborationMode.DIRECT:
            steps.append({
                "type": StepType.AGENT_CALL.value,
                "name": f"调用{intent.target_agent}",
                "agent_name": intent.target_agent,
            })

        elif intent.collaboration_mode == CollaborationMode.SELECTOR:
            steps.append({
                "type": StepType.AGENT_CALL.value,
                "name": f"调用{intent.target_agent}",
                "agent_name": intent.target_agent,
            })
            if intent.review_required:
                steps.append({
                    "type": StepType.REVIEW.value,
                    "name": "审核结果",
                    "agent_name": "Reviewer",
                })

        elif intent.collaboration_mode == CollaborationMode.SWARM:
            # SWARM模式：根据子任务列表规划多步
            if intent.sub_tasks:
                for idx, sub_task in enumerate(intent.sub_tasks):
                    steps.append({
                        "type": StepType.AGENT_CALL.value,
                        "name": sub_task,
                        "agent_name": intent.target_agent,
                        "sub_task_index": idx,
                    })
            else:
                steps.append({
                    "type": StepType.AGENT_CALL.value,
                    "name": f"调用{intent.target_agent}",
                    "agent_name": intent.target_agent,
                })

            if intent.review_required:
                steps.append({
                    "type": StepType.REVIEW.value,
                    "name": "审核结果",
                    "agent_name": "Reviewer",
                })

            # 多步任务最后一步是汇总
            if len(steps) > 1:
                steps.append({
                    "type": StepType.AGGREGATE.value,
                    "name": "汇总结果",
                    "agent_name": "Supervisor",
                })

        return steps

    async def _execute_step(
        self,
        execution: TaskExecution,
        step: dict[str, Any],
        step_checkpoint_index: int,
        user_message: str,
        session_id: str,
        user_id: str,
        session: SessionState | None,
        knowledge_base_id: str | None,
    ) -> StepCheckpoint:
        """执行单个步骤

        根据步骤类型调用不同的执行逻辑。

        Args:
            execution: 任务执行记录
            step: 步骤定义
            step_checkpoint_index: 步骤检查点索引
            user_message: 用户原始消息
            session_id: 会话ID
            user_id: 用户ID
            session: 会话状态
            knowledge_base_id: 知识库ID

        Returns:
            步骤检查点
        """
        step_type = StepType(step.get("type", "agent_call"))
        step_name = step.get("name", "未知步骤")
        agent_name = step.get("agent_name", "")

        checkpoint = StepCheckpoint(
            step_index=step_checkpoint_index,
            step_type=step_type,
            step_name=step_name,
            agent_name=agent_name,
            status=StepStatus.RUNNING,
            input_data=step,
        )

        if step_type == StepType.AGENT_CALL:
            result = await self._execute_agent_call(
                execution, step, user_message, session_id, user_id, session, knowledge_base_id,
            )
            if result.get("status") == "success":
                checkpoint.status = StepStatus.COMPLETED
                checkpoint.output_data = result
            else:
                checkpoint.status = StepStatus.FAILED
                checkpoint.error = result.get("message", "执行失败")
                checkpoint.output_data = result

        elif step_type == StepType.REVIEW:
            # 审核步骤：调用Reviewer Agent对前一步骤的输出进行审核
            prev_output = ""
            for cp in reversed(execution.checkpoints):
                if cp.step_type == StepType.AGENT_CALL and cp.status == StepStatus.COMPLETED:
                    prev_output = cp.output_data.get("message", "") if cp.output_data else ""
                    break

            if prev_output and agent_name:
                try:
                    review_result = await self._execute_agent_call(
                        execution,
                        {
                            "type": StepType.AGENT_CALL.value,
                            "name": step_name,
                            "agent_name": agent_name,
                        },
                        f"请审核以下内容的准确性和完整性:\n\n{prev_output}",
                        session_id,
                        user_id,
                        session,
                        knowledge_base_id,
                    )
                    if review_result.get("status") == "success":
                        checkpoint.status = StepStatus.COMPLETED
                        checkpoint.output_data = {
                            "reviewed": True,
                            "review_message": review_result.get("message", ""),
                            "original_agent": step.get("agent_name", ""),
                        }
                    else:
                        checkpoint.status = StepStatus.FAILED
                        checkpoint.error = review_result.get("message", "审核失败")
                        checkpoint.output_data = review_result
                except Exception as e:
                    checkpoint.status = StepStatus.FAILED
                    checkpoint.error = f"审核步骤异常: {e}"
            else:
                checkpoint.status = StepStatus.COMPLETED
                checkpoint.output_data = {"reviewed": True, "review_message": "无可审核内容，自动通过"}

        elif step_type == StepType.AGGREGATE:
            # 汇总之前的所有Agent调用结果，生成综合回复
            agent_results = {}
            agent_messages = []
            for cp in execution.checkpoints:
                if cp.step_type == StepType.AGENT_CALL and cp.status == StepStatus.COMPLETED:
                    agent_results[cp.agent_name] = cp.output_data
                    msg = cp.output_data.get("message", "") if cp.output_data else ""
                    if msg:
                        agent_messages.append(f"[{cp.agent_name}]: {msg}")

            if agent_messages and agent_name:
                try:
                    aggregate_task = (
                        "请汇总以下多个Agent的执行结果，生成一份综合回复:\n\n"
                        + "\n\n".join(agent_messages)
                    )
                    aggregate_result = await self._execute_agent_call(
                        execution,
                        {
                            "type": StepType.AGENT_CALL.value,
                            "name": step_name,
                            "agent_name": agent_name,
                        },
                        aggregate_task,
                        session_id,
                        user_id,
                        session,
                        knowledge_base_id,
                    )
                    if aggregate_result.get("status") == "success":
                        checkpoint.status = StepStatus.COMPLETED
                        checkpoint.output_data = {
                            "aggregated": True,
                            "message": aggregate_result.get("message", ""),
                            "agent_results": agent_results,
                        }
                    else:
                        checkpoint.status = StepStatus.COMPLETED
                        checkpoint.output_data = {
                            "aggregated": True,
                            "message": "\n\n".join(agent_messages),
                            "agent_results": agent_results,
                        }
                except Exception:
                    checkpoint.status = StepStatus.COMPLETED
                    checkpoint.output_data = {
                        "aggregated": True,
                        "message": "\n\n".join(agent_messages),
                        "agent_results": agent_results,
                    }
            else:
                checkpoint.status = StepStatus.COMPLETED
                checkpoint.output_data = {"aggregated": True, "agent_results": agent_results}

        elif step_type == StepType.HUMAN_CONFIRM:
            # 人工确认步骤：创建确认请求，暂停任务等待用户决策
            confirm_type_str = step.get("confirm_type", "sensitive_action")
            confirm_type = ConfirmType(confirm_type_str)
            confirm_reason = step.get("confirm_reason", step_name)

            confirm_request = await self._confirm_manager.request_confirm(
                execution_id=execution.execution_id,
                step_index=step_checkpoint_index,
                session_id=session_id,
                user_id=user_id,
                confirm_type=confirm_type,
                reason=confirm_reason,
                agent_name=agent_name,
            )

            checkpoint.status = StepStatus.WAITING_CONFIRM
            checkpoint.output_data = {
                "confirm_id": confirm_request.confirm_id,
                "confirm_type": confirm_type.value,
                "confirm_reason": confirm_reason,
                "options": [o.to_dict() for o in confirm_request.options],
            }

            # 发布人工确认事件
            await publish_event(
                EventType.HUMAN_CONFIRM_REQUIRED,
                session_id,
                {
                    "execution_id": execution.execution_id,
                    "step_index": step_checkpoint_index,
                    "step_name": step_name,
                    "confirm_id": confirm_request.confirm_id,
                    "confirm_type": confirm_type.value,
                    "confirm_reason": confirm_reason,
                    "options": [o.to_dict() for o in confirm_request.options],
                },
            )

            # 暂停任务，等待人工确认
            execution.status = TaskStatus.PAUSED
            execution.error = f"等待人工确认: {step_name}"
            await self._store.update_execution(execution)

        return checkpoint

    async def _execute_agent_call(
        self,
        execution: TaskExecution,
        step: dict[str, Any],
        user_message: str,
        session_id: str,
        user_id: str,
        session: SessionState | None,
        knowledge_base_id: str | None,
    ) -> dict[str, Any]:
        """执行Agent调用步骤

        复用现有的团队创建和执行控制逻辑。

        Args:
            execution: 任务执行记录
            step: 步骤定义
            user_message: 用户原始消息
            session_id: 会话ID
            user_id: 用户ID
            session: 会话状态
            knowledge_base_id: 知识库ID

        Returns:
            执行结果字典
        """
        try:
            # 从意图结果重建IntentResult用于创建团队
            intent_data = execution.intent_result
            from agent.agents.supervisor import IntentResult, CollaborationMode
            intent = IntentResult(
                intent=intent_data.get("intent", ""),
                confidence=intent_data.get("confidence", 0),
                target_agent=intent_data.get("target_agent", ""),
                collaboration_mode=CollaborationMode(intent_data.get("collaboration_mode", "direct")),
                review_required=intent_data.get("review_required", False),
                sub_tasks=intent_data.get("sub_tasks", []),
            )

            # 创建团队
            team = await create_team(intent)

            # 构建任务描述
            from agent.teams.routing import _build_contextual_task
            task = await _build_contextual_task(user_message, intent, session, knowledge_base_id)

            # 执行任务
            controller = get_execution_controller()
            result, exec_meta = await controller.execute_with_control(
                team, task, session_id, user_id,
            )

            if exec_meta.status == "timeout":
                return {
                    "status": "error",
                    "message": f"任务执行超时（超过 {controller._config.max_runtime}s）",
                    "agent_name": step.get("agent_name", ""),
                }

            if exec_meta.status == "error" and result is None:
                return {
                    "status": "error",
                    "message": exec_meta.message,
                    "agent_name": step.get("agent_name", ""),
                }

            output = result.messages[-1].content if result and result.messages else "处理完成"
            return {
                "status": "success",
                "message": output,
                "agent_name": step.get("agent_name", ""),
                "retries": exec_meta.retries,
                "compacted": exec_meta.compacted,
            }

        except Exception as e:
            logger.error("Agent调用失败: step=%s error=%s", step.get("name", ""), e)
            return {
                "status": "error",
                "message": str(e),
                "agent_name": step.get("agent_name", ""),
            }

    async def retry_step(
        self,
        execution_id: str,
        step_index: int,
        agent_name_override: str | None = None,
    ) -> dict[str, Any]:
        """重试指定步骤

        支持指定不同的Agent来重试步骤，用于人工确认后的重试操作。

        Args:
            execution_id: 执行记录ID
            step_index: 步骤索引
            agent_name_override: 指定重试使用的Agent名称，为空则使用原Agent

        Returns:
            执行结果字典
        """
        execution = await self._store.get_execution(execution_id)
        if execution is None:
            return {"status": "error", "message": f"执行记录不存在: {execution_id}"}

        if execution.status not in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED, TaskStatus.RUNNING):
            return {
                "status": "error",
                "message": f"任务状态不允许重试: {execution.status.value}",
            }

        # 使用故障隔离策略的重试方法
        checkpoint = await self._fault_policy.retry_step(
            execution, step_index, agent_name_override,
        )
        await self._store.save_checkpoint(execution.execution_id, checkpoint)

        # 重新加载执行记录
        execution = await self._store.get_execution(execution.execution_id) or execution

        # 如果重试成功且任务之前是暂停状态，恢复执行
        if checkpoint.status in (StepStatus.COMPLETED, StepStatus.DEGRADED):
            if execution.status == TaskStatus.PAUSED:
                execution.status = TaskStatus.RUNNING
                execution.error = ""
                await self._store.update_execution(execution)

                # 继续执行后续步骤
                return await self.resume(execution.execution_id, execution.session_id, execution.user_id)

        return self._build_result(execution)

    def _build_result(self, execution: TaskExecution) -> dict[str, Any]:
        """构建执行结果

        将TaskExecution转换为与routing.py一致的返回格式。

        Args:
            execution: 任务执行记录

        Returns:
            结果字典
        """
        if execution.status == TaskStatus.PAUSED:
            return {
                "status": "paused",
                "message": f"任务已暂停: {execution.error}",
                "execution_id": execution.execution_id,
                "current_step": execution.current_step,
                "total_steps": len(execution.steps) + 1,
                "intent": execution.intent_result.get("intent", ""),
                "agent_name": execution.intent_result.get("target_agent", ""),
            }

        if execution.status == TaskStatus.INTERRUPTED:
            return {
                "status": "interrupted",
                "message": f"任务执行中断: {execution.error}",
                "execution_id": execution.execution_id,
                "current_step": execution.current_step,
                "total_steps": len(execution.steps) + 1,
                "intent": execution.intent_result.get("intent", ""),
                "agent_name": execution.intent_result.get("target_agent", ""),
            }

        # 从检查点中提取最终结果
        # 优先取COMPLETED，其次取DEGRADED
        final_output = ""
        for cp in reversed(execution.checkpoints):
            if cp.status == StepStatus.COMPLETED and cp.output_data:
                if "message" in cp.output_data:
                    final_output = cp.output_data["message"]
                    break
            elif cp.status == StepStatus.DEGRADED and cp.output_data:
                if "message" in cp.output_data:
                    final_output = cp.output_data["message"]
                    break

        if not final_output:
            final_output = "处理完成"

        # 检查是否有降级执行的步骤，在结果中标注
        degraded_steps = [
            cp.step_name for cp in execution.checkpoints
            if cp.status == StepStatus.DEGRADED
        ]
        failed_steps = [
            cp.step_name for cp in execution.checkpoints
            if cp.status == StepStatus.FAILED
        ]

        result = {
            "status": "success",
            "message": final_output,
            "agent_name": execution.intent_result.get("target_agent", ""),
            "intent": execution.intent_result.get("intent", ""),
            "collaboration_mode": execution.collaboration_mode,
            "execution_id": execution.execution_id,
        }

        if degraded_steps:
            result["degraded_steps"] = degraded_steps
        if failed_steps:
            result["failed_steps"] = failed_steps

        return result

    async def _publish_task_event(
        self,
        execution: TaskExecution,
        event_name: str,
    ) -> None:
        """发布任务事件

        Args:
            execution: 任务执行记录
            event_name: 事件名称
        """
        try:
            event_type_map = {
                "task_started": EventType.TASK_STARTED,
                "task_completed": EventType.TASK_COMPLETED,
                "task_paused": EventType.TASK_PAUSED,
                "task_resumed": EventType.TASK_RESUMED,
                "task_interrupted": EventType.TASK_INTERRUPTED,
                "step_completed": EventType.STEP_COMPLETED,
                "step_failed": EventType.STEP_FAILED,
            }
            event_type = event_type_map.get(event_name, EventType.AGENT_START)

            await publish_event(
                event_type,
                execution.session_id,
                {
                    "event_name": event_name,
                    "execution_id": execution.execution_id,
                    "current_step": execution.current_step,
                    "total_steps": len(execution.steps) + 1,
                    "status": execution.status.value,
                },
            )
        except Exception:
            pass

    async def get_execution_status(self, execution_id: str) -> dict[str, Any] | None:
        """查询任务执行状态

        Args:
            execution_id: 执行记录ID

        Returns:
            状态信息字典，不存在时返回None
        """
        execution = await self._store.get_execution(execution_id)
        if execution is None:
            return None

        step_details = []
        for cp in execution.checkpoints:
            step_info = {
                "step_index": cp.step_index,
                "step_name": cp.step_name,
                "step_type": cp.step_type.value,
                "agent_name": cp.agent_name,
                "status": cp.status.value,
                "error": cp.error,
                "fallback_used": cp.fallback_used,
            }
            # 人工确认步骤附加确认信息
            if cp.step_type == StepType.HUMAN_CONFIRM and cp.output_data:
                step_info["confirm_id"] = cp.output_data.get("confirm_id", "")
                step_info["confirm_type"] = cp.output_data.get("confirm_type", "")
                step_info["confirm_reason"] = cp.output_data.get("confirm_reason", "")
                step_info["options"] = cp.output_data.get("options", [])
            step_details.append(step_info)

        return {
            "execution_id": execution.execution_id,
            "session_id": execution.session_id,
            "status": execution.status.value,
            "current_step": execution.current_step,
            "total_steps": len(execution.steps) + 1,
            "failure_policy": execution.failure_policy.value,
            "error": execution.error,
            "steps": step_details,
            "created_at": execution.created_at,
            "updated_at": execution.updated_at,
        }

    async def get_execution_by_session(self, session_id: str) -> dict[str, Any] | None:
        """通过会话ID查询任务执行状态

        Args:
            session_id: 会话ID

        Returns:
            状态信息字典，不存在时返回None
        """
        execution = await self._store.get_execution_by_session(session_id)
        if execution is None:
            return None
        return await self.get_execution_status(execution.execution_id)


# ==================== 全局实例 ====================

_task_execution_engine: TaskExecutionEngine | None = None


def get_task_execution_engine() -> TaskExecutionEngine:
    """获取全局任务编排引擎实例"""
    global _task_execution_engine
    if _task_execution_engine is None:
        _task_execution_engine = TaskExecutionEngine()
    return _task_execution_engine
