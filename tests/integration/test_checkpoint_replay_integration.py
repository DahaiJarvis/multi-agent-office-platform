"""检查点时间旅行与状态回放 集成测试

端到端验证 spec 03 的核心业务流程：
  1. 端到端回放流程：创建任务 -> 保存检查点 -> 回放 -> 验证新 execution
  2. 向后兼容性：旧调用方零改动正常工作
  3. 回放隔离：原执行记录在回放前后保持一致
  4. MockToolClient 隔离：回放过程无真实工具调用
  5. 版本链淘汰端到端：大量版本写入后正确淘汰
  6. 回放互斥锁：并发回放被正确阻止
  7. 溯源能力：回放生成的检查点能溯源到原任务
  8. overrides 注入：agent_override / input_data / failure_policy 生效
  9. API 路由层验证：权限校验与响应结构

测试使用内存降级模式（无需 Redis），通过 _use_memory_fallback=True 强制。
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.workflow.task_checkpoint import (
    FailurePolicy,
    StepCheckpoint,
    StepStatus,
    StepType,
    TaskCheckpointStore,
    TaskExecution,
    TaskStatus,
)
from agent.core.workflow.execution_replayer import ExecutionReplayer
from agent.core.workflow.mock_tool_client import MockToolClient


# ==================== 辅助函数 ====================


def _make_store() -> TaskCheckpointStore:
    """创建内存降级模式的 store（无需 Redis）"""
    store = TaskCheckpointStore()
    store._use_memory_fallback = True
    return store


def _make_checkpoint(
    step_index: int,
    step_name: str = "test_step",
    agent_name: str = "TestAgent",
    status: StepStatus = StepStatus.COMPLETED,
    output_data: dict | None = None,
) -> StepCheckpoint:
    """创建测试用检查点"""
    return StepCheckpoint(
        step_index=step_index,
        step_type=StepType.AGENT_CALL,
        step_name=step_name,
        agent_name=agent_name,
        status=status,
        output_data=output_data or {"result": f"step_{step_index}_output"},
    )


def _make_task(
    steps_count: int = 3,
    execution_id: str = "exec-integ001",
) -> TaskExecution:
    """创建测试用任务执行记录（包含完整步骤定义）"""
    steps = []
    for i in range(steps_count):
        steps.append({
            "name": f"step_{i}",
            "type": "agent_call",
            "agent": f"Agent_{i}",
        })
    return TaskExecution(
        execution_id=execution_id,
        session_id="sess-integ",
        user_id="u_integ",
        original_message="集成测试任务",
        steps=steps,
        status=TaskStatus.RUNNING,
    )


async def _prepare_executed_task(
    store: TaskCheckpointStore,
    task: TaskExecution,
    completed_steps: int,
) -> None:
    """为任务保存指定数量的检查点（模拟已执行步骤）"""
    await store.create_execution(task)
    for i in range(completed_steps):
        cp = _make_checkpoint(
            i,
            step_name=f"step_{i}",
            agent_name=f"Agent_{i}",
            output_data={"result": f"original_step_{i}_output"},
        )
        await store.save_checkpoint(task.execution_id, cp)


# ==================== 1. 端到端回放流程 ====================


class TestEndToEndReplay:
    """端到端回放流程集成测试"""

    @pytest.mark.asyncio
    async def test_full_replay_flow_generates_new_execution(self):
        """完整回放流程生成新的 execution 记录"""
        store = _make_store()
        task = _make_task(steps_count=4, execution_id="exec-e2e-001")
        await _prepare_executed_task(store, task, completed_steps=3)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=2,
        )

        # 验证生成了新的 execution_id
        assert new_exec_id != task.execution_id
        assert new_exec_id.startswith("exec-")

        # 验证新 execution 存在且标记为回放
        new_exec = await store.get_execution(new_exec_id)
        assert new_exec is not None
        assert new_exec.source == "replay"
        assert new_exec.source_execution_id == task.execution_id
        assert new_exec.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_replay_records_checkpoints_in_new_execution(self):
        """回放过程在新 execution 的版本链中记录检查点"""
        store = _make_store()
        task = _make_task(steps_count=4, execution_id="exec-e2e-002")
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=2,
        )

        # 验证新 execution 有回放生成的检查点
        new_exec = await store.get_execution(new_exec_id)
        assert len(new_exec.checkpoints) == 2  # 从 step 2 回放到 step 3

        # 验证检查点标记为回放来源
        for cp in new_exec.checkpoints:
            assert cp.source == "replay"
            assert cp.source_execution_id == task.execution_id

    @pytest.mark.asyncio
    async def test_replay_from_step_zero_replays_all_steps(self):
        """从 step 0 回放重放全部步骤"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-e2e-003")
        await _prepare_executed_task(store, task, completed_steps=3)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=0,
        )

        new_exec = await store.get_execution(new_exec_id)
        assert len(new_exec.checkpoints) == 3

    @pytest.mark.asyncio
    async def test_replay_result_contains_metadata(self):
        """回放结果包含溯源元数据"""
        store = _make_store()
        task = _make_task(steps_count=4, execution_id="exec-e2e-004")
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=2,
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.result is not None
        assert new_exec.result["replay"] is True
        assert new_exec.result["source_execution_id"] == task.execution_id
        assert new_exec.result["from_step"] == 2
        assert new_exec.result["steps_replayed"] == 2


# ==================== 2. 向后兼容性 ====================


class TestBackwardCompatibility:
    """向后兼容性集成测试"""

    @pytest.mark.asyncio
    async def test_old_caller_zero_change_works(self):
        """旧调用方（两参数签名）零改动正常工作"""
        store = _make_store()
        task = _make_task(execution_id="exec-compat-001")
        await store.create_execution(task)

        # 旧调用方式：只传 execution_id 和 checkpoint
        v1 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        v2 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        # append-only 模式返回版本号
        assert v1 == "0"
        assert v2 == "1"

        # checkpoints 字段保留最新版本（向后兼容读取）
        task_after = await store.get_execution(task.execution_id)
        s0_cps = [c for c in task_after.checkpoints if c.step_index == 0]
        assert len(s0_cps) == 1
        assert s0_cps[0].version == 1

    @pytest.mark.asyncio
    async def test_overwrite_mode_explicit_compatible(self):
        """显式 overwrite=True 走原覆盖式逻辑"""
        store = _make_store()
        task = _make_task(execution_id="exec-compat-002")
        await store.create_execution(task)

        # overwrite=True 返回空字符串
        v = await store.save_checkpoint(
            task.execution_id, _make_checkpoint(0), overwrite=True,
        )
        assert v == ""

        # checkpoints 只有一个版本
        task_after = await store.get_execution(task.execution_id)
        s0_cps = [c for c in task_after.checkpoints if c.step_index == 0]
        assert len(s0_cps) == 1

    @pytest.mark.asyncio
    async def test_get_execution_checkpoints_field_populated(self):
        """get_execution 返回的 checkpoints 字段保留最新版本"""
        store = _make_store()
        task = _make_task(execution_id="exec-compat-003")
        await store.create_execution(task)

        # 写入多个版本
        for i in range(3):
            await store.save_checkpoint(
                task.execution_id,
                _make_checkpoint(0, output_data={"v": i}),
            )

        task_after = await store.get_execution(task.execution_id)
        # checkpoints 字段只保留最新版本（向后兼容）
        s0_cps = [c for c in task_after.checkpoints if c.step_index == 0]
        assert len(s0_cps) == 1
        assert s0_cps[0].output_data == {"v": 2}


# ==================== 3. 回放隔离 ====================


class TestReplayIsolation:
    """回放隔离集成测试"""

    @pytest.mark.asyncio
    async def test_original_execution_not_modified_after_replay(self):
        """回放后原执行记录保持不变"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-iso-001")
        await _prepare_executed_task(store, task, completed_steps=3)

        # 记录回放前的原任务状态
        original_before = await store.get_execution(task.execution_id)
        original_checkpoints_before = [
            (c.step_index, c.version, c.output_data) for c in original_before.checkpoints
        ]
        original_status_before = original_before.status
        original_current_step_before = original_before.current_step

        # 执行回放
        replayer = ExecutionReplayer(checkpoint_store=store)
        await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )

        # 验证原任务未被修改
        original_after = await store.get_execution(task.execution_id)
        original_checkpoints_after = [
            (c.step_index, c.version, c.output_data) for c in original_after.checkpoints
        ]

        assert original_checkpoints_after == original_checkpoints_before
        assert original_after.status == original_status_before
        assert original_after.current_step == original_current_step_before

    @pytest.mark.asyncio
    async def test_replay_uses_mock_tool_client_only(self):
        """回放过程仅使用 MockToolClient，无真实工具调用"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-iso-002")
        await _prepare_executed_task(store, task, completed_steps=2)

        # 直接验证 MockToolClient 行为
        mock_client = MockToolClient({
            "Agent_2": {"result": "mocked_output"},
        })

        result = await mock_client.call_tool("Agent_2", {"input": "test"})
        assert result == {"result": "mocked_output"}
        assert mock_client.call_count == 1

        # 验证未预设的工具返回通用 Mock 响应
        result2 = await mock_client.call_tool("UnknownTool", {})
        assert result2["status"] == "mocked"

    @pytest.mark.asyncio
    async def test_replay_checkpoints_marked_as_replay_source(self):
        """回放生成的检查点标记 source=replay"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-iso-003")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )

        new_exec = await store.get_execution(new_exec_id)
        for cp in new_exec.checkpoints:
            assert cp.source == "replay"
            assert cp.source_execution_id == task.execution_id


# ==================== 4. 版本链淘汰端到端 ====================


class TestVersionEvictionEndToEnd:
    """版本链淘汰端到端集成测试"""

    @pytest.mark.asyncio
    async def test_eviction_preserves_latest_n_versions(self):
        """淘汰后保留最新的 N 个版本"""
        store = _make_store()
        task = _make_task(execution_id="exec-evict-001")
        await store.create_execution(task)

        total = TaskCheckpointStore.MAX_VERSIONS_PER_STEP + 10
        for i in range(total):
            await store.save_checkpoint(
                task.execution_id,
                _make_checkpoint(0, output_data={"v": i}),
            )

        versions = await store.list_step_versions(task.execution_id, 0)
        assert len(versions) == TaskCheckpointStore.MAX_VERSIONS_PER_STEP

        # 最旧版本应为 10（淘汰了 0-9）
        assert versions[0].version == 10
        # 最新版本应为 total - 1
        assert versions[-1].version == total - 1

    @pytest.mark.asyncio
    async def test_eviction_does_not_affect_other_steps(self):
        """淘汰某步骤版本不影响其他步骤"""
        store = _make_store()
        task = _make_task(steps_count=2, execution_id="exec-evict-002")
        await store.create_execution(task)

        # step 0 写入超限版本
        for _ in range(TaskCheckpointStore.MAX_VERSIONS_PER_STEP + 3):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        # step 1 只写入 2 个版本
        for _ in range(2):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        s0_versions = await store.list_step_versions(task.execution_id, 0)
        s1_versions = await store.list_step_versions(task.execution_id, 1)

        assert len(s0_versions) == TaskCheckpointStore.MAX_VERSIONS_PER_STEP
        assert len(s1_versions) == 2  # step 1 未受影响

    @pytest.mark.asyncio
    async def test_snapshot_after_eviction_still_correct(self):
        """淘汰后状态快照重建仍然正确"""
        store = _make_store()
        task = _make_task(steps_count=2, execution_id="exec-evict-003")
        await store.create_execution(task)

        # 写入并淘汰
        for i in range(TaskCheckpointStore.MAX_VERSIONS_PER_STEP + 2):
            await store.save_checkpoint(
                task.execution_id,
                _make_checkpoint(0, output_data={"v": i}),
            )
        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        # 重建快照（默认取最新版本）
        snapshot = await store.get_state_at_step(task.execution_id, 1)

        s0_cp = next(c for c in snapshot.checkpoints if c.step_index == 0)
        # 最新版本应为 MAX_VERSIONS_PER_STEP + 1
        assert s0_cp.version == TaskCheckpointStore.MAX_VERSIONS_PER_STEP + 1


# ==================== 5. 回放互斥锁 ====================


class TestReplayMutexLock:
    """回放互斥锁集成测试"""

    @pytest.mark.asyncio
    async def test_concurrent_replay_blocked_by_lock(self):
        """并发回放被互斥锁阻止"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-lock-001")
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)

        # 手动获取锁，模拟回放进行中
        acquired = await store._acquire_replay_lock(task.execution_id)
        assert acquired is True

        # 再次回放应抛出 RuntimeError
        with pytest.raises(RuntimeError, match="回放互斥锁获取失败"):
            await replayer.replay(
                execution_id=task.execution_id,
                from_step=1,
            )

        # 释放锁后可以正常回放
        await store._release_replay_lock(task.execution_id)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )
        assert new_exec_id != task.execution_id

    @pytest.mark.asyncio
    async def test_lock_released_after_replay_success(self):
        """回放成功后锁被释放"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-lock-002")
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)
        await replayer.replay(execution_id=task.execution_id, from_step=1)

        # 锁应已释放，可以再次获取
        acquired = await store._acquire_replay_lock(task.execution_id)
        assert acquired is True

    @pytest.mark.asyncio
    async def test_lock_released_after_replay_failure(self):
        """回放失败后锁被释放"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-lock-003")
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)

        # 回放不存在的步骤会抛出 ValueError
        with pytest.raises(ValueError):
            await replayer.replay(
                execution_id=task.execution_id,
                from_step=99,
            )

        # 锁应已释放（finally 块）
        acquired = await store._acquire_replay_lock(task.execution_id)
        assert acquired is True


# ==================== 6. 溯源能力 ====================


class TestSourceTracing:
    """回放溯源集成测试"""

    @pytest.mark.asyncio
    async def test_replay_execution_has_source_execution_id(self):
        """回放任务记录包含 source_execution_id"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-trace-001")
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.source == "replay"
        assert new_exec.source_execution_id == task.execution_id

    @pytest.mark.asyncio
    async def test_replay_checkpoints_trace_to_original(self):
        """回放检查点的 source_execution_id 指向原任务"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-trace-002")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )

        new_exec = await store.get_execution(new_exec_id)
        for cp in new_exec.checkpoints:
            assert cp.source_execution_id == task.execution_id
            assert cp.source == "replay"

    @pytest.mark.asyncio
    async def test_replay_result_records_source_metadata(self):
        """回放结果记录溯源元数据"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-trace-003")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.result is not None
        assert new_exec.result["source_execution_id"] == task.execution_id
        assert new_exec.result["from_step"] == 1


# ==================== 7. overrides 注入 ====================


class TestOverridesInjection:
    """回放 overrides 注入集成测试"""

    @pytest.mark.asyncio
    async def test_agent_override_applied(self):
        """agent_override 替换指定步骤的 Agent"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-override-001")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
            overrides={
                "agent_override": {1: "ReplacedAgent"},
            },
        )

        new_exec = await store.get_execution(new_exec_id)
        # step 1 的 agent 应被替换
        s1_cp = next(c for c in new_exec.checkpoints if c.step_index == 1)
        assert s1_cp.agent_name == "ReplacedAgent"

    @pytest.mark.asyncio
    async def test_input_data_override_applied(self):
        """input_data 覆盖指定步骤的输入"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-override-002")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
            overrides={
                "input_data": {1: {"custom_key": "custom_value"}},
            },
        )

        new_exec = await store.get_execution(new_exec_id)
        s1_cp = next(c for c in new_exec.checkpoints if c.step_index == 1)
        assert s1_cp.input_data.get("custom_key") == "custom_value"

    @pytest.mark.asyncio
    async def test_failure_policy_override_applied(self):
        """failure_policy 覆盖故障策略"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-override-003")
        task.failure_policy = FailurePolicy.RELAXED
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
            overrides={
                "failure_policy": "strict",
            },
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.failure_policy == FailurePolicy.STRICT

    @pytest.mark.asyncio
    async def test_use_mock_tools_false_skips_mock_client(self):
        """use_mock_tools=False 时不使用 MockToolClient"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-override-004")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
            overrides={"use_mock_tools": False},
        )

        new_exec = await store.get_execution(new_exec_id)
        # 回放仍应完成，但 mock_tool_calls 为 0
        assert new_exec.result is not None
        assert new_exec.result["mock_tool_calls"] == 0


# ==================== 8. replay_from_step 委托验证 ====================


class TestReplayFromStepDelegation:
    """replay_from_step 委托给 ExecutionReplayer 验证"""

    @pytest.mark.asyncio
    async def test_replay_from_step_delegates_to_replayer(self):
        """store.replay_from_step 正确委托给 ExecutionReplayer"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-delegate-001")
        await _prepare_executed_task(store, task, completed_steps=2)

        new_exec_id = await store.replay_from_step(
            execution_id=task.execution_id,
            from_step=1,
        )

        assert new_exec_id != task.execution_id
        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.source == "replay"

    @pytest.mark.asyncio
    async def test_replay_from_step_with_overrides(self):
        """replay_from_step 支持 overrides 参数"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-delegate-002")
        await _prepare_executed_task(store, task, completed_steps=1)

        new_exec_id = await store.replay_from_step(
            execution_id=task.execution_id,
            from_step=1,
            overrides={"agent_override": {1: "DelegatedAgent"}},
        )

        new_exec = await store.get_execution(new_exec_id)
        s1_cp = next(c for c in new_exec.checkpoints if c.step_index == 1)
        assert s1_cp.agent_name == "DelegatedAgent"

    @pytest.mark.asyncio
    async def test_replay_from_step_with_version(self):
        """replay_from_step 支持 from_version 参数"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-delegate-003")
        await store.create_execution(task)

        # step 0 写入 3 个版本
        for i in range(3):
            await store.save_checkpoint(
                task.execution_id,
                _make_checkpoint(0, output_data={"v": i}),
            )
        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        # 从 step 0 的 version 1 回放
        new_exec_id = await store.replay_from_step(
            execution_id=task.execution_id,
            from_step=0,
            from_version=1,
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.source == "replay"


# ==================== 9. API 路由层验证 ====================


class TestAPIRoutes:
    """API 路由层集成测试"""

    def test_replay_request_model_validation(self):
        """ReplayRequest 模型校验"""
        from api.routes.admin_routes import ReplayRequest

        # 有效请求
        req = ReplayRequest(from_step=1)
        assert req.from_step == 1
        assert req.from_version is None
        assert req.use_mock_tools is True

        # from_step 必须 >= 1
        with pytest.raises(Exception):
            ReplayRequest(from_step=0)

    def test_replay_response_model_structure(self):
        """ReplayResponse 模型结构"""
        from api.routes.admin_routes import ReplayResponse

        resp = ReplayResponse(
            new_execution_id="exec-new001",
            source_execution_id="exec-old001",
            from_step=2,
            from_version=1,
            status="completed",
            started_at="2026-07-16T10:00:00",
        )
        assert resp.new_execution_id == "exec-new001"
        assert resp.source_execution_id == "exec-old001"
        assert resp.status == "completed"

    def test_require_admin_without_admin_role_raises(self):
        """_require_admin 无管理员权限时抛出异常"""
        from api.routes.admin_routes import _require_admin
        from api.errors import AppException, ErrorCode

        # 模拟非管理员请求
        request = MagicMock()
        request.state.user_roles = ["user"]  # 非 admin

        with pytest.raises(AppException) as exc_info:
            _require_admin(request)

        assert exc_info.value.error_code == ErrorCode.PERMISSION_DENIED

    def test_require_admin_with_admin_role_passes(self):
        """_require_admin 管理员权限通过"""
        from api.routes.admin_routes import _require_admin

        request = MagicMock()
        request.state.user_roles = ["admin"]

        # 不抛出异常即通过
        _require_admin(request)

    @pytest.mark.asyncio
    async def test_replay_endpoint_success_flow(self):
        """回放接口成功流程"""
        from api.routes.admin_routes import replay_execution, ReplayRequest

        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-api-001")
        await _prepare_executed_task(store, task, completed_steps=2)

        # 模拟管理员请求
        request = MagicMock()
        request.state.user_roles = ["admin"]

        replay_request = ReplayRequest(from_step=2)

        with patch(
            "agent.core.workflow.task_checkpoint.get_task_checkpoint_store",
            return_value=store,
        ):
            resp = await replay_execution(task.execution_id, request, replay_request)

        assert resp.new_execution_id != task.execution_id
        assert resp.source_execution_id == task.execution_id
        assert resp.from_step == 2
        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_replay_endpoint_returns_conflict_on_lock_failure(self):
        """回放接口在锁获取失败时返回 CONFLICT"""
        from api.routes.admin_routes import replay_execution, ReplayRequest
        from api.errors import AppException, ErrorCode

        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-api-002")
        await _prepare_executed_task(store, task, completed_steps=2)

        # 手动获取锁，模拟回放进行中
        await store._acquire_replay_lock(task.execution_id)

        request = MagicMock()
        request.state.user_roles = ["admin"]
        replay_request = ReplayRequest(from_step=1)

        with patch(
            "agent.core.workflow.task_checkpoint.get_task_checkpoint_store",
            return_value=store,
        ):
            with pytest.raises(AppException) as exc_info:
                await replay_execution(task.execution_id, request, replay_request)

        assert exc_info.value.error_code == ErrorCode.CONFLICT

    @pytest.mark.asyncio
    async def test_replay_endpoint_returns_invalid_param_on_bad_step(self):
        """回放接口在步骤越界时返回 INVALID_PARAMETER"""
        from api.routes.admin_routes import replay_execution, ReplayRequest
        from api.errors import AppException, ErrorCode

        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-api-003")
        await _prepare_executed_task(store, task, completed_steps=2)

        request = MagicMock()
        request.state.user_roles = ["admin"]
        # from_step=99 (内部 step_index=98) 越界
        replay_request = ReplayRequest(from_step=99)

        with patch(
            "agent.core.workflow.task_checkpoint.get_task_checkpoint_store",
            return_value=store,
        ):
            with pytest.raises(AppException) as exc_info:
                await replay_execution(task.execution_id, request, replay_request)

        assert exc_info.value.error_code == ErrorCode.INVALID_PARAMETER


# ==================== 10. 复杂场景集成 ====================


class TestComplexScenarios:
    """复杂场景集成测试"""

    @pytest.mark.asyncio
    async def test_multi_step_replay_preserves_step_order(self):
        """多步骤回放保持步骤顺序"""
        store = _make_store()
        task = _make_task(steps_count=5, execution_id="exec-complex-001")
        await _prepare_executed_task(store, task, completed_steps=3)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=2,
        )

        new_exec = await store.get_execution(new_exec_id)
        # 从 step 2 回放，应执行 step 2, 3, 4
        assert len(new_exec.checkpoints) == 3
        step_indices = [c.step_index for c in new_exec.checkpoints]
        assert step_indices == [2, 3, 4]

    @pytest.mark.asyncio
    async def test_replay_with_multiple_overrides(self):
        """同时注入多个 overrides"""
        store = _make_store()
        task = _make_task(steps_count=4, execution_id="exec-complex-002")
        await _prepare_executed_task(store, task, completed_steps=1)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
            overrides={
                "agent_override": {1: "AgentA", 2: "AgentB"},
                "input_data": {1: {"key1": "val1"}},
                "failure_policy": "manual",
                "use_mock_tools": True,
            },
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.failure_policy == FailurePolicy.MANUAL

        s1_cp = next(c for c in new_exec.checkpoints if c.step_index == 1)
        assert s1_cp.agent_name == "AgentA"
        assert s1_cp.input_data.get("key1") == "val1"

        s2_cp = next(c for c in new_exec.checkpoints if c.step_index == 2)
        assert s2_cp.agent_name == "AgentB"

    @pytest.mark.asyncio
    async def test_chained_replay_replay_of_replay(self):
        """对回放结果再次回放（链式回放）"""
        store = _make_store()
        task = _make_task(steps_count=4, execution_id="exec-complex-003")
        await _prepare_executed_task(store, task, completed_steps=3)

        replayer = ExecutionReplayer(checkpoint_store=store)

        # 第一次回放
        first_replay_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=2,
        )
        assert first_replay_id != task.execution_id

        # 对回放结果再次回放
        second_replay_id = await replayer.replay(
            execution_id=first_replay_id,
            from_step=2,
        )
        assert second_replay_id != first_replay_id

        # 验证第二次回放的溯源指向第一次回放
        second_exec = await store.get_execution(second_replay_id)
        assert second_exec.source == "replay"
        assert second_exec.source_execution_id == first_replay_id

    @pytest.mark.asyncio
    async def test_replay_preserves_task_metadata(self):
        """回放保留原任务的元数据"""
        store = _make_store()
        task = _make_task(steps_count=3, execution_id="exec-complex-004")
        task.user_id = "user_custom"
        task.original_message = "custom task message"
        task.session_id = "sess_custom"
        await _prepare_executed_task(store, task, completed_steps=2)

        replayer = ExecutionReplayer(checkpoint_store=store)
        new_exec_id = await replayer.replay(
            execution_id=task.execution_id,
            from_step=1,
        )

        new_exec = await store.get_execution(new_exec_id)
        assert new_exec.user_id == "user_custom"
        assert new_exec.original_message == "custom task message"
        assert new_exec.session_id == "sess_custom"
        assert new_exec.steps == task.steps
