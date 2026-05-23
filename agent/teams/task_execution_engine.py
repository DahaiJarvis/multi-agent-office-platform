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
from collections.abc import AsyncGenerator
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
from agent.agents.domain import create_domain_agent
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

    async def create_execution_record(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
        intent: IntentResult,
        session: SessionState | None = None,
        knowledge_base_id: str | None = None,
        failure_policy: FailurePolicy = FailurePolicy.RELAXED,
    ) -> TaskExecution:
        """创建任务执行记录（不含执行）

        在流式模式下，先创建执行记录获取 execution_id，
        然后再逐步执行，以便前端能尽早订阅任务事件。

        Args:
            与 execute() 参数一致

        Returns:
            TaskExecution 执行记录对象
        """
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

        return execution

    async def execute_with_progress(
        self,
        execution: TaskExecution,
        user_message: str,
        session_id: str,
        user_id: str,
        intent: IntentResult,
        session: SessionState | None = None,
        knowledge_base_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """带进度事件的任务执行（异步生成器模式）

        在流式模式下使用，逐步执行并yield步骤进度事件，
        上层路由可将其作为流式事件输出到前端。

        Yields:
            步骤进度事件字典：
            - {"type": "step_start", "step_name": ..., "agent_name": ..., ...}
            - {"type": "step_done", "step_name": ..., "status": ..., ...}

        最后一次yield为执行结果：
            - {"type": "result", "status": ..., "message": ..., ...}
        """
        # 逐步执行
        for step_index in range(len(execution.steps)):
            step = execution.steps[step_index]
            step_checkpoint_index = step_index + 1  # +1 因为步骤0是意图分类

            # 跳过已完成的步骤（断点恢复时）
            if step_checkpoint_index < execution.current_step:
                continue

            # 更新心跳
            await self._store.update_heartbeat(execution.execution_id)

            # 更新任务状态为运行中
            execution.status = TaskStatus.RUNNING
            await self._store.update_execution(execution)

            # 输出步骤开始事件
            step_info = {
                "step_name": step.get("name", ""),
                "agent_name": step.get("agent_name", ""),
                "step_index": step_checkpoint_index,
                "total_steps": len(execution.steps) + 1,
            }
            yield {"type": "step_start", **step_info}
            # 发布步骤开始事件到事件总线
            await publish_event(
                EventType.TASK_STEP_START,
                session_id,
                {
                    "execution_id": execution.execution_id,
                    "step_index": step_checkpoint_index,
                    "step_name": step.get("name", ""),
                    "agent_name": step.get("agent_name", ""),
                    "total_steps": len(execution.steps) + 1,
                },
            )

            try:
                checkpoint = await self._execute_step(
                    execution, step, step_checkpoint_index,
                    user_message, session_id, user_id, session, knowledge_base_id,
                )
                await self._store.save_checkpoint(execution.execution_id, checkpoint)

                # 输出步骤完成事件
                done_info = {
                    "step_name": step.get("name", ""),
                    "step_type": step.get("type", ""),
                    "agent_name": step.get("agent_name", ""),
                    "step_index": step_checkpoint_index,
                    "total_steps": len(execution.steps) + 1,
                    "status": checkpoint.status.value,
                }
                # 携带步骤输出结果，供前端展示
                if checkpoint.output_data:
                    msg = checkpoint.output_data.get("message", "")
                    if msg:
                        done_info["message"] = msg
                    err = checkpoint.error
                    if err:
                        done_info["error"] = err
                yield {"type": "step_done", **done_info}

                # 如果步骤需要人工确认（WAITING_CONFIRM），暂停执行
                if checkpoint.status == StepStatus.WAITING_CONFIRM:
                    # 重新加载执行记录（_execute_step 可能已修改状态为 PAUSED）
                    execution = await self._store.get_execution(execution.execution_id) or execution
                    if execution.status == TaskStatus.PAUSED:
                        await self._publish_task_event(execution, "task_paused")
                        yield {"type": "result", **self._build_result(execution)}
                        return

                # 如果步骤失败，启动故障隔离策略
                if checkpoint.status == StepStatus.FAILED:
                    recovery_checkpoint = await self._fault_policy.handle_step_failure(
                        execution, step_checkpoint_index,
                        error=Exception(checkpoint.error),
                    )
                    await self._store.save_checkpoint(execution.execution_id, recovery_checkpoint)
                    execution = await self._store.get_execution(execution.execution_id) or execution

                    if execution.status == TaskStatus.PAUSED:
                        await self._publish_task_event(execution, "task_paused")
                        yield {"type": "result", **self._build_result(execution)}
                        return

                    if recovery_checkpoint.status == StepStatus.DEGRADED:
                        logger.info(
                            "步骤降级执行完成: step=%s fallback=%s",
                            recovery_checkpoint.step_name, recovery_checkpoint.fallback_used,
                        )

            except Exception as e:
                logger.error("步骤执行异常: step=%s error=%s", step.get("name", ""), e)

                recovery_checkpoint = await self._fault_policy.handle_step_failure(
                    execution, step_checkpoint_index, error=e,
                )
                await self._store.save_checkpoint(execution.execution_id, recovery_checkpoint)
                execution = await self._store.get_execution(execution.execution_id) or execution

                # 输出步骤完成事件（失败）
                done_info = {
                    "step_name": step.get("name", ""),
                    "step_type": step.get("type", ""),
                    "agent_name": step.get("agent_name", ""),
                    "step_index": step_checkpoint_index,
                    "total_steps": len(execution.steps) + 1,
                    "status": "failed",
                }
                yield {"type": "step_done", **done_info}

                if execution.status == TaskStatus.PAUSED:
                    await self._publish_task_event(execution, "task_paused")
                    yield {"type": "result", **self._build_result(execution)}
                    return

        # 所有步骤完成
        execution.status = TaskStatus.COMPLETED
        execution.updated_at = time.time()
        await self._store.update_execution(execution)
        await self._store.remove_from_running(execution.execution_id)
        await self._publish_task_event(execution, "task_completed")

        yield {"type": "result", **self._build_result(execution)}

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
        supplementary_message: str | None = None,
    ) -> dict[str, Any]:
        """从断点恢复执行

        从已保存的检查点恢复任务执行，跳过已完成的步骤。

        Args:
            execution_id: 执行记录ID
            session_id: 会话ID
            user_id: 用户ID
            supplementary_message: 补充需求，追加到原始请求上下文中

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

        # 记录补充需求
        if supplementary_message and supplementary_message.strip():
            execution.supplementary_messages.append(supplementary_message.strip())

        # 构建包含补充需求的完整用户消息
        user_message = execution.original_message
        if execution.supplementary_messages:
            supplement_parts = "\n\n".join(
                f"[补充需求{i+1}] {msg}"
                for i, msg in enumerate(execution.supplementary_messages)
            )
            user_message = f"{execution.original_message}\n\n{supplement_parts}"

        # 补充需求触发的重新路由检测
        # 如果补充需求导致意图发生变化，重新规划未执行的步骤
        if supplementary_message and supplementary_message.strip():
            reclassify_result = await self._try_reclassify_on_resume(
                execution, user_message,
            )
            if reclassify_result is not None:
                execution = reclassify_result

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
                    user_message, session_id, user_id, session, None,
                )
                await self._store.save_checkpoint(execution.execution_id, checkpoint)

                # 重新加载执行记录，确保后续步骤能看到最新的 checkpoints
                execution = await self._store.get_execution(execution.execution_id) or execution

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

    # 敏感操作关键词，包含这些关键词的子任务需要人工确认
    SENSITIVE_ACTION_KEYWORDS = [
        "发送邮件", "发邮件", "邮件发送",
        "提交审批", "审批操作",
        "删除", "修改数据",
        "提交报销", "付款",
    ]

    # 子任务关键词到领域 Agent 的映射
    # 用于将子任务智能路由到对应的专业 Agent
    SUB_TASK_AGENT_MAP: dict[str, str] = {
        "审批": "ApprovalAgent",
        "待审批": "ApprovalAgent",
        "OA": "ApprovalAgent",
        "邮件": "EmailAgent",
        "收件箱": "EmailAgent",
        "日程": "CalendarAgent",
        "会议": "CalendarAgent",
        "日历": "CalendarAgent",
        "客户": "CRMAgent",
        "商机": "CRMAgent",
        "CRM": "CRMAgent",
        "考勤": "HRAgent",
        "请假": "HRAgent",
        "薪资": "HRAgent",
        "假期": "HRAgent",
        "HR": "HRAgent",
        "报销": "FinanceAgent",
        "预算": "FinanceAgent",
        "发票": "FinanceAgent",
        "财务": "FinanceAgent",
        "知识库": "KnowledgeAgent",
        "文档": "KnowledgeAgent",
        "搜索": "KnowledgeAgent",
        "消息": "OfficeAssistant",
        "通知": "OfficeAssistant",
    }

    def _resolve_agent_for_sub_task(self, sub_task: str, default_agent: str) -> str:
        """根据子任务内容智能解析目标 Agent

        通过关键词匹配将子任务路由到最合适的领域 Agent。
        如果无法匹配，则使用默认 Agent。

        Args:
            sub_task: 子任务描述
            default_agent: 默认 Agent 名称

        Returns:
            匹配到的 Agent 名称
        """
        for keyword, agent_name in self.SUB_TASK_AGENT_MAP.items():
            if keyword in sub_task:
                return agent_name
        return default_agent

    def _plan_steps(self, intent: IntentResult) -> list[dict[str, Any]]:
        """根据意图规划执行步骤

        根据协作模式生成不同的步骤列表：
          - DIRECT: 单步Agent调用
          - SELECTOR: Agent调用 + 审核
          - SWARM + cross_system: PARALLEL并行编排
          - SWARM + complex_task: DEBATE辩论编排
          - SWARM + 其他: 多Agent调用 + 汇总

        对于包含敏感操作的步骤，会自动在操作前插入人工确认步骤。

        Args:
            intent: 意图分类结果

        Returns:
            步骤列表，每个步骤包含 type, name, agent_name 等字段
        """
        steps: list[dict[str, Any]] = []

        if intent.collaboration_mode == CollaborationMode.DIRECT:
            if intent.review_required:
                steps.append({
                    "type": StepType.HUMAN_CONFIRM.value,
                    "name": f"确认操作: {intent.target_agent}",
                    "agent_name": intent.target_agent,
                    "confirm_type": "sensitive_action",
                    "confirm_reason": f"即将执行 {intent.target_agent} 的操作，请确认是否继续",
                })
            steps.append({
                "type": StepType.AGENT_CALL.value,
                "name": f"调用{intent.target_agent}",
                "agent_name": intent.target_agent,
            })

        elif intent.collaboration_mode == CollaborationMode.SELECTOR:
            if intent.review_required:
                steps.append({
                    "type": StepType.HUMAN_CONFIRM.value,
                    "name": f"确认操作: {intent.target_agent}",
                    "agent_name": intent.target_agent,
                    "confirm_type": "sensitive_action",
                    "confirm_reason": f"即将执行 {intent.target_agent} 的操作，请确认是否继续",
                })
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
            # cross_system 意图 -> PARALLEL 并行编排
            if intent.intent == "cross_system":
                steps = self._plan_parallel_steps(intent)
            # complex_task 意图 -> DEBATE 辩论编排
            elif intent.intent == "complex_task":
                steps = self._plan_debate_steps(intent)
            # 其他 SWARM 意图 -> 原有的多步编排
            else:
                steps = self._plan_swarm_steps(intent)

        return steps

    def _plan_parallel_steps(self, intent: IntentResult) -> list[dict[str, Any]]:
        """为 cross_system 意图规划 PARALLEL 并行编排步骤

        PARALLEL 模式：多个 Agent 并行执行同一任务，Aggregator 汇总结果。
        步骤规划：
          1. 并行执行步骤（包含所有参与的 Agent）
          2. 结果汇总步骤

        Args:
            intent: 意图分类结果

        Returns:
            步骤列表
        """
        agent_names = self._resolve_parallel_agents(intent)
        steps: list[dict[str, Any]] = []

        if intent.review_required:
            steps.append({
                "type": StepType.HUMAN_CONFIRM.value,
                "name": "确认并行执行",
                "agent_name": "Aggregator",
                "confirm_type": "sensitive_action",
                "confirm_reason": f"即将并行调用 {', '.join(agent_names)} 执行任务，请确认是否继续",
            })

        steps.append({
            "type": StepType.PARALLEL_EXEC.value,
            "name": f"并行执行（{', '.join(agent_names)}）",
            "agent_name": "Aggregator",
            "parallel_agents": agent_names,
        })

        steps.append({
            "type": StepType.AGGREGATE.value,
            "name": "汇总并行结果",
            "agent_name": "Aggregator",
        })

        return steps

    def _plan_debate_steps(self, intent: IntentResult) -> list[dict[str, Any]]:
        """为 complex_task 意图规划 DEBATE 辩论编排步骤

        DEBATE 模式：多个 Agent 从不同角度辩论，Judge 裁决。
        步骤规划：
          1. 各轮辩论步骤
          2. 裁判裁决步骤

        Args:
            intent: 意图分类结果

        Returns:
            步骤列表
        """
        agent_names = self._resolve_debate_agents(intent)
        debate_rounds = 3
        steps: list[dict[str, Any]] = []

        if intent.review_required:
            steps.append({
                "type": StepType.HUMAN_CONFIRM.value,
                "name": "确认辩论执行",
                "agent_name": "Judge",
                "confirm_type": "sensitive_action",
                "confirm_reason": f"即将启动辩论模式（{', '.join(agent_names)}），请确认是否继续",
            })

        for round_idx in range(debate_rounds):
            round_label = "初始立场" if round_idx == 0 else f"第{round_idx + 1}轮辩论"
            steps.append({
                "type": StepType.DEBATE_ROUND.value,
                "name": f"辩论 - {round_label}",
                "agent_name": "Judge",
                "debate_agents": agent_names,
                "round_index": round_idx,
                "total_rounds": debate_rounds,
            })

        steps.append({
            "type": StepType.AGGREGATE.value,
            "name": "裁判裁决",
            "agent_name": "Judge",
        })

        return steps

    def _plan_swarm_steps(self, intent: IntentResult) -> list[dict[str, Any]]:
        """为普通 SWARM 意图规划多步编排步骤

        Args:
            intent: 意图分类结果

        Returns:
            步骤列表
        """
        steps: list[dict[str, Any]] = []
        default_agent = intent.target_agent
        if default_agent in ("Swarm",):
            default_agent = "OfficeAssistant"

        if intent.sub_tasks:
            for idx, sub_task in enumerate(intent.sub_tasks):
                resolved_agent = self._resolve_agent_for_sub_task(sub_task, default_agent)
                if self._is_sensitive_action(sub_task):
                    steps.append({
                        "type": StepType.HUMAN_CONFIRM.value,
                        "name": f"确认: {sub_task}",
                        "agent_name": resolved_agent,
                        "confirm_type": "sensitive_action",
                        "confirm_reason": f"即将执行「{sub_task}」，此操作需要确认后才能继续",
                    })
                steps.append({
                    "type": StepType.AGENT_CALL.value,
                    "name": sub_task,
                    "agent_name": resolved_agent,
                    "sub_task_index": idx,
                })
        else:
            if intent.review_required:
                steps.append({
                    "type": StepType.HUMAN_CONFIRM.value,
                    "name": f"确认操作: {default_agent}",
                    "agent_name": default_agent,
                    "confirm_type": "sensitive_action",
                    "confirm_reason": f"即将执行 {default_agent} 的操作，请确认是否继续",
                })
            steps.append({
                "type": StepType.AGENT_CALL.value,
                "name": f"调用{default_agent}",
                "agent_name": default_agent,
            })

        if intent.review_required:
            steps.append({
                "type": StepType.REVIEW.value,
                "name": "审核结果",
                "agent_name": "Reviewer",
            })

        if len(steps) > 1:
            steps.append({
                "type": StepType.AGGREGATE.value,
                "name": "汇总结果",
                "agent_name": "OfficeAssistant",
            })

        return steps

    def _resolve_parallel_agents(self, intent: IntentResult) -> list[str]:
        """根据意图和子任务解析 PARALLEL 模式需要的 Agent 列表

        优先从子任务中提取，否则根据意图类型选择默认组合。

        Args:
            intent: 意图分类结果

        Returns:
            Agent 名称列表
        """
        if intent.sub_tasks:
            agents = []
            for sub_task in intent.sub_tasks:
                agent = self._resolve_agent_for_sub_task(sub_task, "")
                if agent and agent not in agents:
                    agents.append(agent)
            if agents:
                return agents[:4]

        return ["KnowledgeAgent", "CRMAgent", "FinanceAgent"]

    def _resolve_debate_agents(self, intent: IntentResult) -> list[str]:
        """根据意图和子任务解析 DEBATE 模式需要的 Agent 列表

        辩论需要至少两个不同视角的 Agent。

        Args:
            intent: 意图分类结果

        Returns:
            Agent 名称列表
        """
        if intent.sub_tasks:
            agents = []
            for sub_task in intent.sub_tasks:
                agent = self._resolve_agent_for_sub_task(sub_task, "")
                if agent and agent not in agents:
                    agents.append(agent)
            if len(agents) >= 2:
                return agents[:4]

        return ["KnowledgeAgent", "OfficeAssistant"]

    def _is_sensitive_action(self, task_name: str) -> bool:
        """判断子任务是否包含敏感操作

        Args:
            task_name: 子任务名称

        Returns:
            是否为敏感操作
        """
        for keyword in self.SENSITIVE_ACTION_KEYWORDS:
            if keyword in task_name:
                return True
        return False

    async def _check_step_capability(
        self,
        agent_name: str,
        user_message: str,
    ) -> dict[str, Any]:
        """步骤执行前的能力预检测

        通过CapabilityRegistry检查Agent是否具备处理当前消息的能力。
        如果Agent能力不足，返回建议的替代Agent。

        Args:
            agent_name: 待检测的Agent名称
            user_message: 包含原始需求和补充需求的完整消息

        Returns:
            能力检测结果字典:
            - can_handle: bool, 是否能处理
            - matched_keywords: list[str], 匹配到的能力关键词
            - unmatched_keywords: list[str], 未匹配到的需求关键词
            - limitations: list[str], Agent的限制说明
            - suggested_agents: list[str], 建议的替代Agent
        """
        try:
            from agent.core.capability_card import get_capability_registry
            registry = get_capability_registry()
            return registry.check_agent_capability(agent_name, user_message)
        except Exception as e:
            logger.warning("能力预检测异常(非致命，跳过检测): agent=%s error=%s", agent_name, e)
            return {"can_handle": True, "matched_keywords": [], "unmatched_keywords": [], "limitations": [], "suggested_agents": []}

    async def _try_reclassify_on_resume(
        self,
        execution: TaskExecution,
        full_message: str,
    ) -> TaskExecution | None:
        """补充需求触发的重新路由检测

        当用户在恢复任务时追加了补充需求，检测补充需求是否导致
        意图发生变化。如果意图改变，重新规划未执行的步骤。

        重新路由策略：
          1. 对完整消息（原始需求+补充需求）重新进行意图分类
          2. 比较新意图与原始意图是否一致
          3. 如果意图改变，重新规划未执行的步骤
          4. 保留已完成的步骤不变

        Args:
            execution: 当前任务执行记录
            full_message: 包含原始需求和补充需求的完整消息

        Returns:
            更新后的TaskExecution（意图改变时），或None（意图未改变时）
        """
        try:
            from agent.agents.supervisor import classify_intent

            new_intent = await classify_intent(full_message)

            original_intent = execution.intent_result.get("intent", "")
            new_intent_label = new_intent.intent

            if new_intent_label == original_intent:
                logger.info(
                    "重新路由检测-意图未变: original=%s new=%s",
                    original_intent, new_intent_label,
                )
                return None

            logger.info(
                "重新路由检测-意图变化: original=%s -> new=%s confidence=%.2f",
                original_intent, new_intent_label, new_intent.confidence,
            )

            # 意图变化，重新规划未执行的步骤
            # 保留已完成的步骤（current_step之前的步骤不变）
            new_steps = self._plan_steps(new_intent)

            # 找到未执行的步骤范围
            old_steps_count = len(execution.steps)
            completed_steps_count = execution.current_step - 1  # -1 因为步骤0是意图分类

            # 替换未执行的步骤
            if completed_steps_count < old_steps_count:
                execution.steps = execution.steps[:completed_steps_count] + new_steps
            else:
                execution.steps = execution.steps + new_steps

            # 更新意图结果
            execution.intent_result = {
                "intent": new_intent.intent,
                "confidence": new_intent.confidence,
                "target_agent": new_intent.target_agent,
                "collaboration_mode": new_intent.collaboration_mode.value,
                "review_required": new_intent.review_required,
                "sub_tasks": new_intent.sub_tasks,
                "reclassified_from": original_intent,
            }
            execution.collaboration_mode = new_intent.collaboration_mode.value

            # 保存重新路由的检查点
            reclassify_checkpoint = StepCheckpoint(
                step_index=execution.current_step,
                step_type=StepType.INTENT_CLASSIFY,
                step_name="重新意图分类",
                status=StepStatus.COMPLETED,
                output_data={
                    "original_intent": original_intent,
                    "new_intent": new_intent_label,
                    "confidence": new_intent.confidence,
                    "target_agent": new_intent.target_agent,
                    "collaboration_mode": new_intent.collaboration_mode.value,
                    "reason": "补充需求导致意图变化",
                },
                fallback_used="reclassify_on_resume",
            )
            execution.checkpoints.append(reclassify_checkpoint)

            await self._store.update_execution(execution)

            await publish_event(
                EventType.TASK_STEP_START,
                execution.session_id,
                {
                    "execution_id": execution.execution_id,
                    "event": "intent_reclassified",
                    "original_intent": original_intent,
                    "new_intent": new_intent_label,
                    "new_steps_count": len(new_steps),
                },
            )

            return execution

        except Exception as e:
            logger.warning("重新路由检测异常(非致命，保持原步骤): execution=%s error=%s", execution.execution_id, e)
            return None

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

        # 发布步骤开始事件
        await self._publish_step_event(execution, "task_step_start", checkpoint)

        # 能力预检测：在执行前检查Agent是否具备处理当前消息的能力
        if step_type == StepType.AGENT_CALL and agent_name:
            capability_result = await self._check_step_capability(
                agent_name, user_message,
            )
            if not capability_result.get("can_handle", True):
                unmatched = capability_result.get("unmatched_keywords", [])
                suggested = capability_result.get("suggested_agents", [])
                limitations = capability_result.get("limitations", [])

                if suggested:
                    replacement = suggested[0]
                    logger.info(
                        "能力预检测-替换Agent: %s -> %s (未匹配关键词: %s)",
                        agent_name, replacement, unmatched,
                    )
                    step["agent_name"] = replacement
                    checkpoint.agent_name = replacement
                    checkpoint.fallback_used = f"capability_redirect:{agent_name}->{replacement}"
                else:
                    logger.warning(
                        "能力预检测-Agent能力不足且无替代: agent=%s unmatched=%s limitations=%s",
                        agent_name, unmatched, limitations,
                    )
                    checkpoint.status = StepStatus.FAILED
                    limitation_msg = "、".join(limitations) if limitations else "超出能力范围"
                    checkpoint.error = (
                        f"当前Agent({agent_name})无法处理需求中的: {', '.join(unmatched)}。"
                        f"限制说明: {limitation_msg}"
                    )
                    checkpoint.output_data = {
                        "status": "error",
                        "message": checkpoint.error,
                        "unmatched_keywords": unmatched,
                        "limitations": limitations,
                        "agent_name": agent_name,
                    }
                    await self._publish_step_event(execution, "step_failed", checkpoint)
                    return checkpoint

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

        elif step_type == StepType.PARALLEL_EXEC:
            result = await self._execute_parallel_step(
                execution, step, user_message, session_id, user_id, session, knowledge_base_id,
            )
            if result.get("status") == "success":
                checkpoint.status = StepStatus.COMPLETED
                checkpoint.output_data = result
            else:
                checkpoint.status = StepStatus.FAILED
                checkpoint.error = result.get("message", "并行执行失败")
                checkpoint.output_data = result

        elif step_type == StepType.DEBATE_ROUND:
            result = await self._execute_debate_round(
                execution, step, user_message, session_id, user_id, session, knowledge_base_id,
            )
            if result.get("status") == "success":
                checkpoint.status = StepStatus.COMPLETED
                checkpoint.output_data = result
            else:
                checkpoint.status = StepStatus.FAILED
                checkpoint.error = result.get("message", "辩论执行失败")
                checkpoint.output_data = result

        elif step_type == StepType.VOTE_EXEC:
            result = await self._execute_vote_step(
                execution, step, user_message, session_id, user_id, session, knowledge_base_id,
            )
            if result.get("status") == "success":
                checkpoint.status = StepStatus.COMPLETED
                checkpoint.output_data = result
            else:
                checkpoint.status = StepStatus.FAILED
                checkpoint.error = result.get("message", "投票执行失败")
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
            # 汇总之前的所有步骤结果，生成综合回复
            agent_results = {}
            agent_messages = []
            advanced_step_types = {
                StepType.AGENT_CALL,
                StepType.PARALLEL_EXEC,
                StepType.DEBATE_ROUND,
                StepType.VOTE_EXEC,
            }
            for cp in execution.checkpoints:
                if cp.step_type in advanced_step_types and cp.status == StepStatus.COMPLETED:
                    agent_results[cp.agent_name] = cp.output_data
                    msg = cp.output_data.get("message", "") if cp.output_data else ""
                    if msg:
                        agent_messages.append(f"[{cp.agent_name}]: {msg}")

            if agent_messages:
                try:
                    aggregate_task = (
                        "请汇总以下多个Agent的执行结果，生成一份综合回复:\n\n"
                        + "\n\n".join(agent_messages)
                    )
                    # AGGREGATE 步骤使用 OfficeAssistant（Supervisor 不在 AGENT_CREATORS 中）
                    aggregate_result = await self._execute_agent_call(
                        execution,
                        {
                            "type": StepType.AGENT_CALL.value,
                            "name": step_name,
                            "agent_name": "OfficeAssistant",
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

            # 收集前置步骤的执行结果，作为确认上下文供用户预览
            prev_results = []
            for cp in execution.checkpoints:
                if cp.step_type == StepType.AGENT_CALL and cp.status == StepStatus.COMPLETED and cp.output_data:
                    msg = cp.output_data.get("message", "")
                    if msg:
                        prev_results.append(f"[{cp.agent_name}] {msg}")
            if prev_results:
                confirm_reason += "\n\n--- 已收集的信息 ---\n" + "\n\n".join(prev_results)

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

        # 发布步骤完成/失败事件（WAITING_CONFIRM 不发布完成事件，等确认后再继续）
        if checkpoint.status == StepStatus.COMPLETED:
            await self._publish_step_event(execution, "step_completed", checkpoint)
        elif checkpoint.status == StepStatus.FAILED:
            await self._publish_step_event(execution, "step_failed", checkpoint)
        elif checkpoint.status == StepStatus.SKIPPED:
            await self._publish_step_event(execution, "step_completed", checkpoint)

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

        对于 SWARM 模式下的子任务步骤，使用步骤自身的 agent_name
        创建 DIRECT 模式的领域 Agent 执行，而非使用原始意图创建团队。

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
            step_agent = step.get("agent_name", "")
            step_name = step.get("name", "")
            is_swarm = execution.collaboration_mode == "swarm"

            # SWARM 模式下，每个子任务用步骤自身的 agent_name 创建 DIRECT 模式的领域 Agent
            # 而不是用原始意图（target_agent="Swarm"）创建高级编排团队
            if is_swarm and step_agent and step_agent not in ("Swarm",):
                from agent.agents.supervisor import IntentResult, CollaborationMode
                step_intent = IntentResult(
                    intent="task_step",
                    confidence=1.0,
                    target_agent=step_agent,
                    collaboration_mode=CollaborationMode.DIRECT,
                    review_required=False,
                )
                team = await create_team(step_intent)

                # 构建子任务描述：包含原始请求和前置步骤结果，使 Agent 理解上下文
                from agent.teams.routing import _build_contextual_task
                task_parts = [f"[子任务] {step_name}"]
                task_parts.append(f"[原始请求] {user_message}")

                # 收集前置步骤的执行结果作为上下文
                prev_results = []
                for cp in execution.checkpoints:
                    if cp.step_type == StepType.AGENT_CALL and cp.status == StepStatus.COMPLETED and cp.output_data:
                        msg = cp.output_data.get("message", "")
                        if msg:
                            prev_results.append(f"[{cp.agent_name}的结果] {msg}")
                if prev_results:
                    task_parts.append("[前置步骤结果]\n" + "\n".join(prev_results))

                task = "\n\n".join(task_parts)
            else:
                # 非 SWARM 模式，使用原始意图创建团队
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
                    "agent_name": step_agent,
                }

            if exec_meta.status == "error" and result is None:
                return {
                    "status": "error",
                    "message": exec_meta.message,
                    "agent_name": step_agent,
                }

            from agent.teams.advanced_orchestration import _extract_agent_response
            output = _extract_agent_response(result) if result else "处理完成"
            return {
                "status": "success",
                "message": output,
                "agent_name": step_agent,
                "retries": exec_meta.retries,
                "compacted": exec_meta.compacted,
            }

        except Exception as e:
            logger.error("Agent调用失败: step=%s agent=%s error=%s", step.get("name", ""), step.get("agent_name", ""), e)
            return {
                "status": "error",
                "message": str(e),
                "agent_name": step.get("agent_name", ""),
            }

    async def _execute_parallel_step(
        self,
        execution: TaskExecution,
        step: dict[str, Any],
        user_message: str,
        session_id: str,
        user_id: str,
        session: SessionState | None,
        knowledge_base_id: str | None,
    ) -> dict[str, Any]:
        """执行 PARALLEL 并行编排步骤

        使用 ParallelTeam 并行执行多个 Agent，收集各 Agent 的结果。

        Args:
            execution: 任务执行记录
            step: 步骤定义（包含 parallel_agents 字段）
            user_message: 用户原始消息
            session_id: 会话ID
            user_id: 用户ID
            session: 会话状态
            knowledge_base_id: 知识库ID

        Returns:
            执行结果字典
        """
        try:
            from agent.teams.advanced_orchestration import ParallelTeam

            agent_names = step.get("parallel_agents", ["KnowledgeAgent", "CRMAgent", "FinanceAgent"])
            team = ParallelTeam(agent_names=agent_names)

            # 构建任务描述：包含原始请求和前置步骤结果
            task_parts = [user_message]
            prev_results = self._collect_prev_results(execution)
            if prev_results:
                task_parts.append("[前置步骤结果]\n" + "\n".join(prev_results))
            task = "\n\n".join(task_parts)

            result = await team.run(task=task)

            # 格式化并行结果为可读文本
            formatted_parts = []
            for name, agent_result in result.agent_results.items():
                formatted_parts.append(f"**{name}**的分析:\n{agent_result}")
            formatted = "\n\n---\n\n".join(formatted_parts)
            if result.aggregated:
                formatted = result.aggregated

            return {
                "status": "success",
                "message": formatted,
                "agent_name": "Aggregator",
                "parallel_agents": agent_names,
                "agent_results": result.agent_results,
                "duration_ms": result.duration_ms,
            }

        except Exception as e:
            logger.error("并行执行失败: step=%s error=%s", step.get("name", ""), e)
            return {
                "status": "error",
                "message": str(e),
                "agent_name": step.get("agent_name", "Aggregator"),
            }

    async def _execute_debate_round(
        self,
        execution: TaskExecution,
        step: dict[str, Any],
        user_message: str,
        session_id: str,
        user_id: str,
        session: SessionState | None,
        knowledge_base_id: str | None,
    ) -> dict[str, Any]:
        """执行 DEBATE 辩论步骤

        每轮辩论中，各 Agent 独立或基于其他 Agent 观点进行分析。
        第一轮独立分析，后续轮次基于其他 Agent 观点反驳/补充。

        Args:
            execution: 任务执行记录
            step: 步骤定义（包含 debate_agents, round_index, total_rounds 字段）
            user_message: 用户原始消息
            session_id: 会话ID
            user_id: 用户ID
            session: 会话状态
            knowledge_base_id: 知识库ID

        Returns:
            执行结果字典
        """
        try:
            from agent.teams.advanced_orchestration import DebateTeam, _extract_agent_response

            agent_names = step.get("debate_agents", ["KnowledgeAgent", "OfficeAssistant"])
            round_index = step.get("round_index", 0)
            total_rounds = step.get("total_rounds", 3)

            # 收集前置辩论轮次的结果作为上下文
            prev_positions: dict[str, str] = {}
            for cp in execution.checkpoints:
                if cp.step_type == StepType.DEBATE_ROUND and cp.status == StepStatus.COMPLETED and cp.output_data:
                    prev_positions_data = cp.output_data.get("positions", {})
                    if prev_positions_data:
                        prev_positions = prev_positions_data

            # 为每个 Agent 创建实例并执行当前轮次
            agents = []
            for name in agent_names:
                try:
                    agent = await create_domain_agent(name)
                    agents.append(agent)
                except Exception as e:
                    logger.warning("辩论轮次初始化 Agent %s 失败: %s", name, e)

            if not agents:
                return {
                    "status": "error",
                    "message": "所有辩论 Agent 均不可用",
                    "agent_name": "Judge",
                }

            positions: dict[str, str] = dict(prev_positions)

            if round_index == 0:
                # 第一轮：各 Agent 独立给出初始立场
                for agent in agents:
                    try:
                        result = await agent.run(task=f"请分析以下问题并给出你的观点: {user_message}")
                        positions[agent.name] = _extract_agent_response(result)
                    except Exception as e:
                        positions[agent.name] = f"分析失败: {e}"
            else:
                # 后续轮次：基于其他 Agent 观点反驳/补充
                for agent in agents:
                    other_views = {
                        name: pos for name, pos in positions.items() if name != agent.name
                    }
                    counter_prompt = (
                        f"原始问题: {user_message}\n\n"
                        f"其他观点:\n"
                        + "\n".join(f"【{name}】{view[:300]}" for name, view in other_views.items())
                        + f"\n\n请基于以上观点进行反驳或补充（第{round_index + 1}轮）。"
                    )
                    try:
                        result = await agent.run(task=counter_prompt)
                        positions[agent.name] = _extract_agent_response(result)
                    except Exception as e:
                        logger.warning("辩论第%d轮 Agent %s 失败: %s", round_index + 1, agent.name, e)

            # 格式化当前轮次结果
            round_label = "初始立场" if round_index == 0 else f"第{round_index + 1}轮辩论"
            formatted_parts = []
            for name, pos in positions.items():
                formatted_parts.append(f"**{name}**的观点:\n{pos}")
            formatted = f"## {round_label}\n\n" + "\n\n---\n\n".join(formatted_parts)

            return {
                "status": "success",
                "message": formatted,
                "agent_name": "Judge",
                "positions": positions,
                "round_index": round_index,
                "total_rounds": total_rounds,
            }

        except Exception as e:
            logger.error("辩论执行失败: step=%s error=%s", step.get("name", ""), e)
            return {
                "status": "error",
                "message": str(e),
                "agent_name": step.get("agent_name", "Judge"),
            }

    async def _execute_vote_step(
        self,
        execution: TaskExecution,
        step: dict[str, Any],
        user_message: str,
        session_id: str,
        user_id: str,
        session: SessionState | None,
        knowledge_base_id: str | None,
    ) -> dict[str, Any]:
        """执行 VOTE 投票步骤

        各 Agent 独立回答同一问题，多数决定最终结果。

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
            from agent.teams.advanced_orchestration import VoteTeam

            agent_names = step.get("vote_agents", ["KnowledgeAgent", "OfficeAssistant", "HRAgent"])
            team = VoteTeam(agent_names=agent_names)

            # 构建任务描述
            task_parts = [user_message]
            prev_results = self._collect_prev_results(execution)
            if prev_results:
                task_parts.append("[前置步骤结果]\n" + "\n".join(prev_results))
            task = "\n\n".join(task_parts)

            result = await team.run(task=task)

            # 格式化投票结果
            formatted = f"## 投票结果\n\n"
            formatted += f"**胜出选项**: {result.winner}\n"
            formatted += f"**置信度**: {result.confidence:.1%}\n\n"
            for name, vote in result.votes.items():
                formatted += f"- **{name}**: {vote}\n"

            return {
                "status": "success",
                "message": formatted,
                "agent_name": "Voter",
                "votes": result.votes,
                "vote_counts": result.vote_counts,
                "winner": result.winner,
                "confidence": result.confidence,
            }

        except Exception as e:
            logger.error("投票执行失败: step=%s error=%s", step.get("name", ""), e)
            return {
                "status": "error",
                "message": str(e),
                "agent_name": step.get("agent_name", "Voter"),
            }

    def _collect_prev_results(self, execution: TaskExecution) -> list[str]:
        """收集前置步骤的执行结果

        从检查点中提取已完成步骤的输出，用于构建后续步骤的上下文。

        Args:
            execution: 任务执行记录

        Returns:
            前置步骤结果列表
        """
        prev_results = []
        result_step_types = {
            StepType.AGENT_CALL,
            StepType.PARALLEL_EXEC,
            StepType.DEBATE_ROUND,
            StepType.VOTE_EXEC,
        }
        for cp in execution.checkpoints:
            if cp.step_type in result_step_types and cp.status == StepStatus.COMPLETED and cp.output_data:
                msg = cp.output_data.get("message", "")
                if msg:
                    prev_results.append(f"[{cp.agent_name}的结果] {msg}")
        return prev_results

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
                # resume() 内部会处理状态从 PAUSED 到 RUNNING 的转换
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

    async def _publish_step_event(
        self,
        execution: TaskExecution,
        event_name: str,
        checkpoint: StepCheckpoint,
    ) -> None:
        """发布步骤级别事件

        Args:
            execution: 任务执行记录
            event_name: 事件名称
            checkpoint: 步骤检查点
        """
        try:
            event_type_map = {
                "task_step_start": EventType.TASK_STEP_START,
                "task_step_complete": EventType.TASK_STEP_COMPLETE,
                "step_completed": EventType.STEP_COMPLETED,
                "step_failed": EventType.STEP_FAILED,
            }
            event_type = event_type_map.get(event_name, EventType.AGENT_START)

            step_data = {
                "event_name": event_name,
                "execution_id": execution.execution_id,
                "step_index": checkpoint.step_index,
                "step_name": checkpoint.step_name,
                "step_type": checkpoint.step_type.value,
                "agent_name": checkpoint.agent_name,
                "status": checkpoint.status.value,
                "current_step": checkpoint.step_index,
                "total_steps": len(execution.steps) + 1,
            }
            if checkpoint.error:
                step_data["error"] = checkpoint.error

            await publish_event(
                event_type,
                execution.session_id,
                step_data,
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
            # 已完成步骤附加输出结果，供前端展示步骤执行内容
            if cp.status in (StepStatus.COMPLETED, StepStatus.DEGRADED) and cp.output_data:
                msg = cp.output_data.get("message", "")
                if msg:
                    step_info["result"] = msg
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
    """获取全局任务编排引擎实例

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _task_execution_engine
    try:
        from agent.core.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_task_execution_engine() is not None:
            return ctx.get_task_execution_engine()
    except Exception:
        pass
    if _task_execution_engine is None:
        _task_execution_engine = TaskExecutionEngine()
    return _task_execution_engine
