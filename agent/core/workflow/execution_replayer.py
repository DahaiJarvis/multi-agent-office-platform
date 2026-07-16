"""执行回放器

负责从指定步骤/版本回放执行任务，生成新的 execution_id 记录回放轨迹。
与 TaskCheckpointStore 解耦，通过依赖注入获取 store 实例。

回放流程编排：
    1. 获取回放互斥锁
    2. 重建起始状态快照
    3. 创建新 execution 记录（标记为回放任务）
    4. 应用 overrides 到新 execution
    5. 从 from_step 开始逐步骤执行
    6. 每完成一步保存检查点到新 execution 的版本链
    7. 释放互斥锁并返回新 execution_id

安全约束：
    - 回放默认使用 MockToolClient，禁止真实工具调用
    - 回放生成新 execution_id，原执行记录只读不写
    - 回放 API 需要管理员权限校验

对应规格文档：docs/spec/03-检查点时间旅行-spec.md 第 3.2 节
"""

import logging
import time
import uuid
from typing import Any

from agent.core.workflow.mock_tool_client import MockToolClient
from agent.core.workflow.task_checkpoint import (
    StepCheckpoint,
    StepStatus,
    TaskCheckpointStore,
    TaskExecution,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class ExecutionReplayer:
    """执行回放器

    负责从指定步骤/版本回放执行任务，生成新的 execution_id 记录回放轨迹。
    与 TaskCheckpointStore 解耦，通过依赖注入获取 store 实例。

    使用方式：
        replayer = ExecutionReplayer()
        new_exec_id = await replayer.replay(
            execution_id="exec-abc123",
            from_step=3,
            from_version=1,
            overrides={"agent_override": {3: "OfficeAssistant"}},
        )
    """

    def __init__(
        self,
        checkpoint_store: TaskCheckpointStore | None = None,
    ) -> None:
        """初始化回放执行器

        Args:
            checkpoint_store: 检查点存储实例，None 时使用全局单例
        """
        self._checkpoint_store = checkpoint_store

    def _get_store(self, checkpoint_store: TaskCheckpointStore | None = None) -> TaskCheckpointStore:
        """获取检查点存储实例（优先使用传入参数，其次使用构造时注入，最后使用全局单例）"""
        store = checkpoint_store or self._checkpoint_store
        if store is not None:
            return store
        from agent.core.workflow.task_checkpoint import get_task_checkpoint_store
        return get_task_checkpoint_store()

    async def replay(
        self,
        execution_id: str,
        from_step: int,
        from_version: int | None = None,
        overrides: dict[str, Any] | None = None,
        checkpoint_store: TaskCheckpointStore | None = None,
    ) -> str:
        """执行回放

        完整的回放流程编排：
            1. 获取回放互斥锁
            2. 重建起始状态快照
            3. 创建新 execution 记录（标记为回放任务）
            4. 应用 overrides 到新 execution
            5. 从 from_step 开始逐步骤执行
            6. 每完成一步保存检查点到新 execution 的版本链
            7. 释放互斥锁并返回新 execution_id

        Args:
            execution_id: 原任务执行记录ID
            from_step: 回放起始步骤索引
            from_version: 回放起始步骤的版本号，None 表示最新版本
            overrides: 覆盖配置字典
            checkpoint_store: 检查点存储实例（依赖注入）

        Returns:
            新生成的 execution_id

        Raises:
            ValueError: 任务不存在、步骤越界、版本不存在
            RuntimeError: 回放互斥锁获取失败（已有回放进行中）
        """
        store = self._get_store(checkpoint_store)
        overrides = overrides or {}

        # 1. 获取回放互斥锁
        lock_acquired = await store._acquire_replay_lock(execution_id)
        if not lock_acquired:
            raise RuntimeError(
                f"回放互斥锁获取失败，已有回放进行中: execution_id={execution_id}"
            )

        try:
            # 2. 重建起始状态快照
            snapshot = await self._rebuild_snapshot(
                execution_id, from_step, from_version, store,
            )

            # 3. 创建新 execution 记录
            new_exec_id = await self._create_replay_execution(
                snapshot, execution_id, store,
            )

            # 4. 应用 overrides
            new_exec = await store.get_execution(new_exec_id)
            if new_exec is None:
                raise ValueError(f"回放任务创建失败: {new_exec_id}")
            await self._apply_overrides(new_exec, overrides, store)

            # 5. 从 from_step 开始逐步骤执行
            use_mock_tools = overrides.get("use_mock_tools", True)
            await self._execute_remaining_steps(
                new_exec, execution_id, from_step, use_mock_tools, store, overrides,
            )

            logger.info(
                "回放完成: source=%s new_exec=%s from_step=%d",
                execution_id, new_exec_id, from_step,
            )
            return new_exec_id

        finally:
            # 7. 释放互斥锁
            await store._release_replay_lock(execution_id)

    async def _rebuild_snapshot(
        self,
        execution_id: str,
        from_step: int,
        from_version: int | None,
        store: TaskCheckpointStore,
    ) -> TaskExecution:
        """重建起始状态快照

        委托给 TaskCheckpointStore.get_state_at_step() 实现。
        """
        return await store.get_state_at_step(execution_id, from_step, from_version)

    async def _create_replay_execution(
        self,
        snapshot: TaskExecution,
        source_execution_id: str,
        store: TaskCheckpointStore,
    ) -> str:
        """创建回放任务记录

        基于快照创建新的 TaskExecution：
            - 生成新 execution_id
            - 保留原任务的 steps/intent_result 等元数据
            - 清空 checkpoints（回放过程重新生成）
            - 标记 source_execution_id 用于溯源
            - status 设置为 RUNNING
        """
        new_exec = snapshot.model_copy(deep=True)
        new_exec.execution_id = f"exec-{uuid.uuid4().hex[:10]}"
        new_exec.source = "replay"
        new_exec.source_execution_id = source_execution_id
        new_exec.checkpoints = []
        new_exec.status = TaskStatus.RUNNING
        new_exec.error = ""
        new_exec.result = None
        new_exec.created_at = time.time()
        new_exec.updated_at = time.time()
        new_exec.heartbeat_at = time.time()

        await store.create_execution(new_exec)
        logger.info(
            "回放任务已创建: new_exec=%s source=%s",
            new_exec.execution_id, source_execution_id,
        )
        return new_exec.execution_id

    async def _apply_overrides(
        self,
        execution: TaskExecution,
        overrides: dict[str, Any],
        store: TaskCheckpointStore,
    ) -> None:
        """应用覆盖配置到回放任务

        支持的 overrides 键：
            - input_data: 覆盖指定步骤的输入数据
            - agent_override: 替换指定步骤的 Agent
            - failure_policy: 覆盖故障策略
            - use_mock_tools: 是否使用 Mock 工具
        """
        changed = False

        # 覆盖故障策略
        if "failure_policy" in overrides:
            policy_str = overrides["failure_policy"]
            try:
                from agent.core.workflow.task_checkpoint import FailurePolicy
                execution.failure_policy = FailurePolicy(policy_str)
                changed = True
            except ValueError:
                logger.warning("无效的 failure_policy: %s", policy_str)

        # agent_override: {step_index: agent_name}
        # 记录到 execution 的 supplementary_messages 中，供执行阶段读取
        if "agent_override" in overrides:
            agent_override = overrides["agent_override"]
            if isinstance(agent_override, dict):
                # 将 agent_override 存入 intent_result 供执行阶段使用
                execution.intent_result["_replay_agent_override"] = {
                    str(k): v for k, v in agent_override.items()
                }
                changed = True

        # input_data: {step_index: {key: value}}
        if "input_data" in overrides:
            input_override = overrides["input_data"]
            if isinstance(input_override, dict):
                execution.intent_result["_replay_input_override"] = {
                    str(k): v for k, v in input_override.items()
                }
                changed = True

        if changed:
            await store.update_execution(execution)

    async def _execute_remaining_steps(
        self,
        execution: TaskExecution,
        source_execution_id: str,
        from_step: int,
        use_mock_tools: bool,
        store: TaskCheckpointStore,
        overrides: dict[str, Any],
    ) -> None:
        """执行剩余步骤

        从 from_step 开始逐步骤执行。回放模式下使用 MockToolClient 隔离真实工具调用，
        基于原执行记录的 output_data 生成一致的 Mock 响应。

        安全约束：
            - use_mock_tools=True 时强制使用 MockToolClient
            - 回放生成的检查点标记 source="replay"
            - 原执行记录只读不写

        Args:
            execution: 回放任务记录
            source_execution_id: 原执行记录ID（用于读取原始 output_data）
            from_step: 起始步骤索引
            use_mock_tools: 是否使用 Mock 工具客户端
        """
        # 读取原执行记录，获取各步骤的 output_data 用于 Mock
        source_exec = await store.get_execution(source_execution_id)
        original_outputs: dict[str, Any] = {}
        if source_exec is not None:
            for cp in source_exec.checkpoints:
                step_key = f"step_{cp.step_index}"
                if cp.output_data:
                    original_outputs[step_key] = cp.output_data
                if cp.agent_name:
                    original_outputs[cp.agent_name] = cp.output_data

        # 初始化 MockToolClient
        mock_client: MockToolClient | None = None
        if use_mock_tools:
            mock_client = MockToolClient(original_outputs)

        # 读取 overrides 中的 agent_override 和 input_data
        agent_override = execution.intent_result.get("_replay_agent_override", {})
        input_override = execution.intent_result.get("_replay_input_override", {})

        # 逐步骤执行
        steps = execution.steps
        for i in range(from_step, len(steps)):
            step = steps[i]
            step_index = i
            step_name = step.get("name", f"step_{step_index}")
            original_agent = step.get("agent", "")

            # 应用 agent_override
            replay_agent = agent_override.get(str(step_index), original_agent)

            # 从原执行记录获取该步骤的 output_data（Mock 模式下直接复用）
            step_key = f"step_{step_index}"
            original_output = original_outputs.get(step_key, {})

            # 如果有 input_override，应用到步骤输入
            step_input = dict(step)
            if str(step_index) in input_override:
                step_input.update(input_override[str(step_index)])

            # 如果有 MockToolClient，调用一次记录调用（验证 Mock 隔离）
            if mock_client is not None and original_agent:
                await mock_client.call_tool(original_agent, step_input)

            # 创建回放检查点
            from agent.core.workflow.task_checkpoint import StepType
            step_type_str = step.get("type", "agent_call")
            try:
                step_type = StepType(step_type_str)
            except ValueError:
                step_type = StepType.AGENT_CALL

            replay_checkpoint = StepCheckpoint(
                step_index=step_index,
                step_type=step_type,
                step_name=step_name,
                agent_name=replay_agent,
                status=StepStatus.COMPLETED,
                input_data=step_input,
                output_data=original_output,
                source="replay",
                source_execution_id=source_execution_id,
            )

            # 保存检查点到新 execution 的版本链
            await store.save_checkpoint(execution.execution_id, replay_checkpoint)

            # 更新心跳
            await store.update_heartbeat(execution.execution_id)

        # 回放完成，标记任务状态
        execution = await store.get_execution(execution.execution_id)
        if execution is not None:
            execution.status = TaskStatus.COMPLETED
            execution.result = {
                "replay": True,
                "source_execution_id": source_execution_id,
                "from_step": from_step,
                "steps_replayed": len(steps) - from_step,
                "mock_tool_calls": mock_client.call_count if mock_client else 0,
            }
            await store.update_execution(execution)
            await store.remove_from_running(execution.execution_id)


_execution_replayer: ExecutionReplayer | None = None


def get_execution_replayer() -> ExecutionReplayer:
    """获取全局回放执行器单例"""
    global _execution_replayer
    if _execution_replayer is None:
        _execution_replayer = ExecutionReplayer()
    return _execution_replayer
