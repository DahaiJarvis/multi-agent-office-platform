"""任务检查点存储与管理

为多Agent协作任务提供检查点保存和恢复能力，确保任务在中断后可从最近检查点继续。

核心能力：
  - 任务执行记录的创建、查询、更新
  - 步骤检查点的保存与读取
  - 中断任务扫描与标记
  - 心跳机制检测运行中的任务

存储结构（Redis）：
  - STRING task_exec:{execution_id} -> TaskExecution JSON, TTL 24h
  - STRING task_exec:session:{session_id} -> execution_id, TTL 24h
  - STRING task_exec:heartbeat:{execution_id} -> timestamp, TTL 60s
  - ZSET task_exec:running -> score=heartbeat_at, member=execution_id

与 long_task.py 的关系：
  long_task.py 管理显式定义的多步骤长任务（用户手动创建），
  本模块管理 Agent 协作任务的自动检查点（系统自动创建）。
  两者并行存在，互不影响。
"""

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    """步骤类型"""

    INTENT_CLASSIFY = "intent_classify"
    AGENT_CALL = "agent_call"
    REVIEW = "review"
    AGGREGATE = "aggregate"
    HUMAN_CONFIRM = "human_confirm"
    TOOL_CALL = "tool_call"
    PARALLEL_EXEC = "parallel_exec"
    DEBATE_ROUND = "debate_round"
    VOTE_EXEC = "vote_exec"


class StepStatus(str, Enum):
    """步骤状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_CONFIRM = "waiting_confirm"
    DEGRADED = "degraded"


class FailurePolicy(str, Enum):
    """故障策略

    STRICT: 任一步骤失败则暂停，等待人工决策
    RELAXED: 尽量继续，缺失部分标注说明
    MANUAL: 每次失败都询问用户
    """

    STRICT = "strict"
    RELAXED = "relaxed"
    MANUAL = "manual"


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StepCheckpoint(BaseModel):
    """步骤检查点

    每完成一个步骤自动保存，用于恢复执行。

    Attributes:
        step_index: 步骤索引
        step_type: 步骤类型
        step_name: 步骤名称
        agent_name: 执行的Agent名称
        status: 步骤状态
        input_data: 步骤输入
        output_data: 步骤输出
        error: 错误信息
        fallback_used: 使用的降级策略
        retry_count: 重试次数
        created_at: 创建时间
    """

    step_index: int
    step_type: StepType
    step_name: str
    agent_name: str = ""
    status: StepStatus = StepStatus.PENDING
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    fallback_used: str = ""
    retry_count: int = 0
    created_at: float = Field(default_factory=time.time)


class TaskExecution(BaseModel):
    """任务执行记录

    记录一次多Agent协作任务的完整执行过程，
    包括步骤规划、检查点、状态等信息。

    Attributes:
        execution_id: 执行记录唯一ID
        session_id: 关联会话ID
        user_id: 用户ID
        original_message: 用户原始消息
        intent_result: 意图分类结果
        collaboration_mode: 协作模式
        failure_policy: 故障策略
        steps: 预定义步骤列表
        current_step: 当前步骤索引
        checkpoints: 检查点列表
        status: 任务状态
        error: 错误信息
        result: 最终结果
        heartbeat_at: 心跳时间戳
        created_at: 创建时间
        updated_at: 更新时间
    """

    execution_id: str = Field(default_factory=lambda: f"exec-{uuid.uuid4().hex[:10]}")
    session_id: str = ""
    user_id: str = ""
    original_message: str = ""
    intent_result: dict[str, Any] = Field(default_factory=dict)
    collaboration_mode: str = ""
    failure_policy: FailurePolicy = FailurePolicy.RELAXED
    steps: list[dict[str, Any]] = Field(default_factory=list)
    current_step: int = 0
    checkpoints: list[StepCheckpoint] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    error: str = ""
    result: dict[str, Any] | None = None
    supplementary_messages: list[str] = Field(default_factory=list)
    heartbeat_at: float = Field(default_factory=time.time)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TaskCheckpointStore:
    """任务检查点存储管理器

    提供任务执行记录的CRUD操作、检查点保存、
    心跳更新和中断任务扫描能力。

    存储使用 Redis，通过统一连接管理器获取客户端。
    Redis不可用时降级为内存存储。
    """

    EXEC_KEY_PREFIX = "task_exec:"
    SESSION_INDEX_PREFIX = "task_exec:session:"
    HEARTBEAT_KEY_PREFIX = "task_exec:heartbeat:"
    RUNNING_ZSET_KEY = "task_exec:running"
    EXEC_TTL = 86400  # 24小时
    HEARTBEAT_TTL = 60  # 60秒
    HEARTBEAT_TIMEOUT = 120  # 心跳超时阈值(秒)

    def __init__(self) -> None:
        self._redis: Any = None
        self._memory_store: dict[str, str] = {}
        self._memory_session_index: dict[str, str] = {}
        self._use_memory_fallback: bool = False

    async def _get_redis(self) -> Any:
        """获取Redis客户端，不可用时降级为内存存储"""
        if self._use_memory_fallback:
            return None
        if self._redis is not None:
            try:
                await self._redis.ping()
                return self._redis
            except Exception:
                self._use_memory_fallback = True
                return None
        try:
            from agent.core.redis_manager import get_redis_client
            redis = await get_redis_client()
            if redis is None:
                self._use_memory_fallback = True
                return None
            self._redis = redis
            return self._redis
        except Exception as e:
            logger.warning("TaskCheckpointStore Redis不可用，启用内存降级: %s", e)
            self._use_memory_fallback = True
            return None

    async def create_execution(self, task: TaskExecution) -> str:
        """创建任务执行记录

        将任务信息存储到Redis，并建立会话索引。

        Args:
            task: 任务执行记录

        Returns:
            execution_id
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"{self.EXEC_KEY_PREFIX}{task.execution_id}"
                await redis.set(key, task.model_dump_json(), ex=self.EXEC_TTL)
                if task.session_id:
                    session_key = f"{self.SESSION_INDEX_PREFIX}{task.session_id}"
                    await redis.set(session_key, task.execution_id, ex=self.EXEC_TTL)
                await redis.zadd(self.RUNNING_ZSET_KEY, {task.execution_id: task.heartbeat_at})
                logger.info("任务执行记录已创建: execution_id=%s session=%s", task.execution_id, task.session_id)
            except Exception as e:
                logger.warning("任务执行记录存储失败: %s", e)
        else:
            self._memory_store[task.execution_id] = task.model_dump_json()
            if task.session_id:
                self._memory_session_index[task.session_id] = task.execution_id

        return task.execution_id

    async def get_execution(self, execution_id: str) -> TaskExecution | None:
        """获取任务执行记录

        Args:
            execution_id: 执行记录ID

        Returns:
            TaskExecution 或 None
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"{self.EXEC_KEY_PREFIX}{execution_id}"
                data = await redis.get(key)
                if data is None:
                    return None
                return TaskExecution.model_validate_json(data)
            except Exception as e:
                logger.warning("获取任务执行记录失败: %s", e)
                return None
        else:
            data = self._memory_store.get(execution_id)
            if data is None:
                return None
            return TaskExecution.model_validate_json(data)

    async def get_execution_by_session(self, session_id: str) -> TaskExecution | None:
        """通过会话ID获取任务执行记录

        Args:
            session_id: 会话ID

        Returns:
            TaskExecution 或 None
        """
        redis = await self._get_redis()
        execution_id = None
        if redis is not None:
            try:
                session_key = f"{self.SESSION_INDEX_PREFIX}{session_id}"
                execution_id = await redis.get(session_key)
            except Exception:
                return None
        else:
            execution_id = self._memory_session_index.get(session_id)

        if not execution_id:
            return None
        return await self.get_execution(execution_id)

    async def update_execution(self, task: TaskExecution) -> None:
        """更新任务执行记录

        Args:
            task: 更新后的TaskExecution
        """
        task.updated_at = time.time()
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"{self.EXEC_KEY_PREFIX}{task.execution_id}"
                await redis.set(key, task.model_dump_json(), ex=self.EXEC_TTL)
            except Exception as e:
                logger.warning("更新任务执行记录失败: %s", e)
        else:
            self._memory_store[task.execution_id] = task.model_dump_json()

    async def save_checkpoint(self, execution_id: str, checkpoint: StepCheckpoint) -> None:
        """保存步骤检查点

        将检查点追加到任务执行记录中，并更新当前步骤索引。

        Args:
            execution_id: 执行记录ID
            checkpoint: 步骤检查点
        """
        task = await self.get_execution(execution_id)
        if task is None:
            logger.warning("保存检查点失败: 任务 %s 不存在", execution_id)
            return

        # 移除同步骤的旧检查点，保留最新
        task.checkpoints = [c for c in task.checkpoints if c.step_index != checkpoint.step_index]
        task.checkpoints.append(checkpoint)
        task.current_step = checkpoint.step_index + 1
        task.updated_at = time.time()

        await self.update_execution(task)
        logger.info(
            "检查点已保存: execution=%s step=%d/%d status=%s",
            execution_id, checkpoint.step_index + 1, len(task.steps), checkpoint.status.value,
        )

    async def update_step_status(
        self,
        execution_id: str,
        step_index: int,
        status: StepStatus,
        error: str = "",
        output_data: dict[str, Any] | None = None,
    ) -> None:
        """更新步骤状态

        更新指定步骤的检查点状态。

        Args:
            execution_id: 执行记录ID
            step_index: 步骤索引
            status: 新状态
            error: 错误信息
            output_data: 输出数据
        """
        task = await self.get_execution(execution_id)
        if task is None:
            return

        for checkpoint in task.checkpoints:
            if checkpoint.step_index == step_index:
                checkpoint.status = status
                if error:
                    checkpoint.error = error
                if output_data is not None:
                    checkpoint.output_data = output_data
                break

        await self.update_execution(task)

    async def update_heartbeat(self, execution_id: str) -> None:
        """更新任务心跳

        用于检测任务是否仍在运行，心跳超时的任务将被标记为中断。

        Args:
            execution_id: 执行记录ID
        """
        now = time.time()
        redis = await self._get_redis()
        if redis is not None:
            try:
                hb_key = f"{self.HEARTBEAT_KEY_PREFIX}{execution_id}"
                await redis.set(hb_key, str(now), ex=self.HEARTBEAT_TTL)
                await redis.zadd(self.RUNNING_ZSET_KEY, {execution_id: now})
            except Exception:
                pass

        task = await self.get_execution(execution_id)
        if task is not None:
            task.heartbeat_at = now
            await self.update_execution(task)

    async def mark_interrupted(self, execution_id: str) -> None:
        """将任务标记为中断状态

        当检测到任务心跳超时时调用。

        Args:
            execution_id: 执行记录ID
        """
        task = await self.get_execution(execution_id)
        if task is None:
            return

        if task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.INTERRUPTED
            task.error = "任务执行中断（心跳超时）"
            task.updated_at = time.time()
            await self.update_execution(task)
            logger.info("任务已标记为中断: execution_id=%s current_step=%d", execution_id, task.current_step)

    async def scan_interrupted_tasks(self) -> list[TaskExecution]:
        """扫描中断的任务

        检查所有标记为running的任务，心跳超时的标记为interrupted。

        Returns:
            中断的任务列表
        """
        interrupted: list[TaskExecution] = []
        now = time.time()
        redis = await self._get_redis()

        if redis is not None:
            try:
                running_ids = await redis.zrange(self.RUNNING_ZSET_KEY, 0, -1)
                for eid in running_ids:
                    task = await self.get_execution(eid)
                    if task and task.status == TaskStatus.RUNNING:
                        if now - task.heartbeat_at > self.HEARTBEAT_TIMEOUT:
                            await self.mark_interrupted(eid)
                            interrupted.append(task)
            except Exception as e:
                logger.warning("扫描中断任务失败: %s", e)
        else:
            for eid, data in self._memory_store.items():
                try:
                    task = TaskExecution.model_validate_json(data)
                    if task.status == TaskStatus.RUNNING and now - task.heartbeat_at > self.HEARTBEAT_TIMEOUT:
                        await self.mark_interrupted(eid)
                        interrupted.append(task)
                except Exception:
                    pass

        return interrupted

    async def remove_from_running(self, execution_id: str) -> None:
        """从运行中任务集合移除

        任务完成、失败或取消时调用。

        Args:
            execution_id: 执行记录ID
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.zrem(self.RUNNING_ZSET_KEY, execution_id)
                hb_key = f"{self.HEARTBEAT_KEY_PREFIX}{execution_id}"
                await redis.delete(hb_key)
            except Exception:
                pass


# ==================== 全局实例 ====================

_task_checkpoint_store: TaskCheckpointStore | None = None


def get_task_checkpoint_store() -> TaskCheckpointStore:
    """获取全局任务检查点存储管理器"""
    global _task_checkpoint_store
    if _task_checkpoint_store is None:
        _task_checkpoint_store = TaskCheckpointStore()
    return _task_checkpoint_store
