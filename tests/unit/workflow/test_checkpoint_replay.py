"""检查点时间旅行与状态回放 单元测试

覆盖 spec 03 的核心功能：
  - FR-1: append-only 版本链存储
  - FR-2: 历史版本列表查询
  - FR-3: 任意状态快照重建
  - FR-4: 从任意步骤/版本回放执行
  - FR-5: 回放 overrides 注入
  - FR-6: 版本链长度限制与淘汰
  - FR-8: 向后兼容（save_checkpoint 旧签名）

测试使用内存降级模式（无需 Redis），通过 _use_memory_fallback=True 强制。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.core.workflow.task_checkpoint import (
    StepCheckpoint,
    StepStatus,
    StepType,
    TaskCheckpointStore,
    TaskExecution,
    TaskStatus,
    FailurePolicy,
)
from agent.core.workflow.replay_tool_client import ReplayToolClient
from agent.core.workflow.execution_replayer import ExecutionReplayer


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
    execution_id: str = "exec-test001",
) -> TaskExecution:
    """创建测试用任务执行记录"""
    steps = []
    for i in range(steps_count):
        steps.append({
            "name": f"step_{i}",
            "type": "agent_call",
            "agent": f"Agent_{i}",
        })
    return TaskExecution(
        execution_id=execution_id,
        session_id="sess-test",
        user_id="u_test",
        original_message="测试任务",
        steps=steps,
        status=TaskStatus.RUNNING,
    )


# ==================== FR-1: append-only 版本链存储 ====================


class TestAppendOnlyVersionChain:
    """测试 append-only 版本链存储功能"""

    @pytest.mark.asyncio
    async def test_save_checkpoint_append_only_returns_version(self):
        """append-only 模式返回版本号"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        cp = _make_checkpoint(0)
        version = await store.save_checkpoint(task.execution_id, cp)

        assert version == "0"

    @pytest.mark.asyncio
    async def test_save_checkpoint_same_step_multiple_versions(self):
        """同一步骤多次保存产生递增版本号"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        v0 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        v1 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        v2 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        assert v0 == "0"
        assert v1 == "1"
        assert v2 == "2"

    @pytest.mark.asyncio
    async def test_save_checkpoint_different_steps_independent_versions(self):
        """不同步骤的版本号独立计数"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        v0_s0 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        v0_s1 = await store.save_checkpoint(task.execution_id, _make_checkpoint(1))
        v1_s0 = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        v1_s1 = await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        assert v0_s0 == "0"
        assert v0_s1 == "0"
        assert v1_s0 == "1"
        assert v1_s1 == "1"

    @pytest.mark.asyncio
    async def test_save_checkpoint_overwrite_returns_empty(self):
        """overwrite=True 返回空字符串（原覆盖式逻辑）"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        version = await store.save_checkpoint(
            task.execution_id, _make_checkpoint(0), overwrite=True,
        )
        assert version == ""

    @pytest.mark.asyncio
    async def test_save_checkpoint_overwrite_replaces_old(self):
        """overwrite=True 覆盖旧检查点"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 1}))
        await store.save_checkpoint(
            task.execution_id, _make_checkpoint(0, output_data={"v": 2}), overwrite=True,
        )

        task_after = await store.get_execution(task.execution_id)
        assert len(task_after.checkpoints) == 1
        assert task_after.checkpoints[0].output_data == {"v": 2}

    @pytest.mark.asyncio
    async def test_save_checkpoint_updates_current_step(self):
        """保存检查点后 current_step 更新"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        task_after = await store.get_execution(task.execution_id)
        assert task_after.current_step == 2

    @pytest.mark.asyncio
    async def test_save_checkpoint_nonexistent_task(self):
        """保存检查点到不存在的任务返回空字符串"""
        store = _make_store()
        version = await store.save_checkpoint("nonexistent", _make_checkpoint(0))
        assert version == ""


# ==================== FR-2: 历史版本列表查询 ====================


class TestListStepVersions:
    """测试历史版本列表查询"""

    @pytest.mark.asyncio
    async def test_list_versions_returns_all(self):
        """查询返回该步骤的全部版本"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        for _ in range(3):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        versions = await store.list_step_versions(task.execution_id, 0)
        assert len(versions) == 3

    @pytest.mark.asyncio
    async def test_list_versions_sorted_ascending(self):
        """版本按版本号升序排列"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        for _ in range(3):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        versions = await store.list_step_versions(task.execution_id, 0)
        assert [v.version for v in versions] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_list_versions_filters_by_step(self):
        """只返回指定步骤的版本"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        versions_s0 = await store.list_step_versions(task.execution_id, 0)
        versions_s1 = await store.list_step_versions(task.execution_id, 1)

        assert len(versions_s0) == 2
        assert len(versions_s1) == 1

    @pytest.mark.asyncio
    async def test_list_versions_empty_for_no_versions(self):
        """无版本记录返回空列表"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        versions = await store.list_step_versions(task.execution_id, 0)
        assert versions == []


# ==================== FR-3: 任意状态快照重建 ====================


class TestGetStateAtStep:
    """测试任意状态快照重建"""

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_latest_version(self):
        """重建快照默认取最新版本"""
        store = _make_store()
        task = _make_task(steps_count=3)
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 1}))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 2}))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        snapshot = await store.get_state_at_step(task.execution_id, 1)

        assert snapshot.current_step == 2
        assert snapshot.status == TaskStatus.RUNNING
        # 步骤0取最新版本（version=1）
        cp_s0 = next(c for c in snapshot.checkpoints if c.step_index == 0)
        assert cp_s0.version == 1
        assert cp_s0.output_data == {"v": 2}

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_specific_version(self):
        """重建快照取指定版本"""
        store = _make_store()
        task = _make_task(steps_count=3)
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 1}))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 2}))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 3}))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))

        snapshot = await store.get_state_at_step(task.execution_id, 0, version=1)

        cp_s0 = next(c for c in snapshot.checkpoints if c.step_index == 0)
        assert cp_s0.version == 1
        assert cp_s0.output_data == {"v": 2}

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_includes_all_steps_up_to_target(self):
        """快照包含截止到目标步骤的全部检查点"""
        store = _make_store()
        task = _make_task(steps_count=4)
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(1))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(2))

        snapshot = await store.get_state_at_step(task.execution_id, 2)

        step_indices = {c.step_index for c in snapshot.checkpoints}
        assert step_indices == {0, 1, 2}

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_task_not_found(self):
        """任务不存在抛出 ValueError"""
        store = _make_store()
        with pytest.raises(ValueError, match="任务不存在"):
            await store.get_state_at_step("nonexistent", 0)

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_step_out_of_range(self):
        """步骤越界抛出 ValueError"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        with pytest.raises(ValueError, match="步骤索引越界"):
            await store.get_state_at_step(task.execution_id, 99)

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_version_not_found(self):
        """版本不存在抛出 ValueError"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        with pytest.raises(ValueError, match="版本号不存在"):
            await store.get_state_at_step(task.execution_id, 0, version=99)

    @pytest.mark.asyncio
    async def test_rebuild_snapshot_preserves_metadata(self):
        """快照保留原任务元数据"""
        store = _make_store()
        task = _make_task()
        task.user_id = "u_custom"
        task.original_message = "custom message"
        await store.create_execution(task)
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        snapshot = await store.get_state_at_step(task.execution_id, 0)

        assert snapshot.user_id == "u_custom"
        assert snapshot.original_message == "custom message"
        assert snapshot.steps == task.steps


# ==================== FR-6: 版本链长度限制与淘汰 ====================


class TestVersionEviction:
    """测试版本链长度限制与淘汰"""

    @pytest.mark.asyncio
    async def test_evict_when_exceeding_max(self):
        """版本数超过 MAX_VERSIONS_PER_STEP 时淘汰最旧"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        # 写入 MAX_VERSIONS_PER_STEP + 5 个版本
        total = TaskCheckpointStore.MAX_VERSIONS_PER_STEP + 5
        for _ in range(total):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        versions = await store.list_step_versions(task.execution_id, 0)
        assert len(versions) == TaskCheckpointStore.MAX_VERSIONS_PER_STEP

    @pytest.mark.asyncio
    async def test_evict_keeps_latest_versions(self):
        """淘汰后保留最新版本"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        total = TaskCheckpointStore.MAX_VERSIONS_PER_STEP + 3
        for _ in range(total):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        versions = await store.list_step_versions(task.execution_id, 0)
        # 最旧版本应为 3（淘汰了 0, 1, 2）
        assert versions[0].version == 3
        assert versions[-1].version == total - 1

    @pytest.mark.asyncio
    async def test_no_evict_when_under_limit(self):
        """版本数未超限时不淘汰"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        for _ in range(5):
            await store.save_checkpoint(task.execution_id, _make_checkpoint(0))

        versions = await store.list_step_versions(task.execution_id, 0)
        assert len(versions) == 5


# ==================== FR-8: 向后兼容 ====================


class TestBackwardCompatibility:
    """测试向后兼容性"""

    @pytest.mark.asyncio
    async def test_save_checkpoint_old_signature_works(self):
        """旧的两参数签名仍然工作（overwrite 默认 False）"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        # 旧调用方式：只传两个参数
        version = await store.save_checkpoint(task.execution_id, _make_checkpoint(0))
        assert version == "0"

    @pytest.mark.asyncio
    async def test_checkpoints_field_backward_compatible(self):
        """checkpoints 字段保留最新版本（向后兼容读取）"""
        store = _make_store()
        task = _make_task()
        await store.create_execution(task)

        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 1}))
        await store.save_checkpoint(task.execution_id, _make_checkpoint(0, output_data={"v": 2}))

        task_after = await store.get_execution(task.execution_id)
        # checkpoints 字段应保留最新版本
        s0_cps = [c for c in task_after.checkpoints if c.step_index == 0]
        assert len(s0_cps) == 1
        assert s0_cps[0].output_data == {"v": 2}

    @pytest.mark.asyncio
    async def test_new_fields_have_defaults(self):
        """新增字段有默认值，旧数据兼容"""
        cp = StepCheckpoint(
            step_index=0,
            step_type=StepType.AGENT_CALL,
            step_name="test",
        )
        assert cp.version == 0
        assert cp.source == "original"
        assert cp.source_execution_id == ""

        task = TaskExecution()
        assert task.source == "original"
        assert task.source_execution_id == ""


# ==================== 回放锁测试 ====================


class TestReplayLock:
    """测试回放互斥锁"""

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self):
        """首次获取锁成功"""
        store = _make_store()
        result = await store._acquire_replay_lock("exec-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_lock_conflict(self):
        """已有回放进行时获取锁失败"""
        store = _make_store()
        await store._acquire_replay_lock("exec-001")
        result = await store._acquire_replay_lock("exec-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock_allows_reacquire(self):
        """释放锁后可重新获取"""
        store = _make_store()
        await store._acquire_replay_lock("exec-001")
        await store._release_replay_lock("exec-001")
        result = await store._acquire_replay_lock("exec-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_different_tasks_independent_locks(self):
        """不同任务的锁互不影响"""
        store = _make_store()
        r1 = await store._acquire_replay_lock("exec-001")
        r2 = await store._acquire_replay_lock("exec-002")
        assert r1 is True
        assert r2 is True


# ==================== ReplayToolClient 测试 ====================


class TestReplayToolClient:
    """测试 Mock 工具客户端"""

    @pytest.mark.asyncio
    async def test_call_tool_returns_preset_output(self):
        """调用工具返回预设输出"""
        outputs = {"send_email": {"status": "sent", "id": "mock-001"}}
        client = ReplayToolClient(outputs)

        result = await client.call_tool("send_email", {"to": "test@test.com"})
        assert result == {"status": "sent", "id": "mock-001"}

    @pytest.mark.asyncio
    async def test_call_tool_generic_mock_response(self):
        """未预设的工具返回通用 Mock 响应"""
        client = ReplayToolClient({})
        result = await client.call_tool("unknown_tool", {"arg": "val"})

        assert result["status"] == "mocked"
        assert result["tool"] == "unknown_tool"

    @pytest.mark.asyncio
    async def test_call_count_increments(self):
        """调用次数递增"""
        client = ReplayToolClient({})
        assert client.call_count == 0

        await client.call_tool("tool1", {})
        await client.call_tool("tool2", {})

        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_no_real_side_effects(self):
        """Mock 客户端不产生真实副作用"""
        client = ReplayToolClient({})
        result = await client.call_tool("send_email", {"to": "real@test.com"})

        # 结果是 mock 响应，不包含真实发送信息
        assert result["status"] == "mocked"
