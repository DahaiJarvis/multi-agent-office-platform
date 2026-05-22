"""长任务管理与恢复

支持长任务的检查点保存和恢复执行，确保任务在中断后可从最近检查点继续。

核心能力：
  - 任务创建：定义多步骤长任务
  - 异步执行：通过消息队列异步执行，不阻塞主线程
  - 检查点保存：每完成一个步骤自动保存中间结果
  - 恢复执行：失败后可从最近检查点恢复
  - 任务取消：支持取消正在执行的任务
  - 进度查询：实时查询任务执行进度

存储结构（Redis）：
  - HASH long_task:{task_id} -> LongTask JSON
  - ZSET long_tasks:by_session:{session_id} -> score=created_at, member=task_id
  - TTL: 7 天
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


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCheckpoint(BaseModel):
    """任务检查点

    每完成一个步骤自动保存，用于恢复执行。

    Attributes:
        task_id: 关联的任务ID
        step_index: 当前步骤索引
        step_name: 当前步骤名称
        intermediate_result: 中间结果
        created_at: 创建时间
    """

    task_id: str
    step_index: int
    step_name: str
    intermediate_result: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class LongTask(BaseModel):
    """长任务定义

    Attributes:
        task_id: 任务唯一ID
        session_id: 关联会话ID
        user_id: 用户ID
        agent_name: 执行的 Agent 名称
        description: 任务描述
        status: 当前状态
        steps: 任务步骤定义列表
        current_step: 当前步骤索引
        checkpoints: 检查点列表
        result: 最终结果
        error: 错误信息
        created_at: 创建时间
        updated_at: 更新时间
    """

    task_id: str = Field(default_factory=lambda: f"lt-{uuid.uuid4().hex[:10]}")
    session_id: str = ""
    user_id: str = ""
    agent_name: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    steps: list[dict[str, Any]] = Field(default_factory=list)
    current_step: int = 0
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return self.model_dump()


class LongTaskManager:
    """长任务管理器

    提供长任务的创建、执行、检查点保存、恢复和取消能力。
    任务数据存储在 Redis 中，支持分布式部署。
    """

    TASK_KEY_PREFIX = "long_task:"
    SESSION_INDEX_PREFIX = "long_tasks:by_session:"
    TASK_TTL = 86400 * 7  # 7 天

    def __init__(self) -> None:
        self._redis: Any = None
        # 内存中的运行时任务引用（用于取消）
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端"""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                settings = get_settings()
                self._redis = aioredis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                )
            except Exception as e:
                logger.warning("长任务管理器 Redis 连接失败: %s", e)
        return self._redis

    async def create_task(self, task: LongTask) -> str:
        """创建长任务

        将任务信息存储到 Redis，并建立会话索引。

        Args:
            task: 长任务定义

        Returns:
            task_id
        """
        redis = await self._get_redis()
        if redis is None:
            logger.warning("Redis 不可用，长任务仅保存在内存中")
            return task.task_id

        try:
            task_key = f"{self.TASK_KEY_PREFIX}{task.task_id}"
            await redis.set(task_key, task.model_dump_json(), ex=self.TASK_TTL)

            # 建立会话索引
            if task.session_id:
                index_key = f"{self.SESSION_INDEX_PREFIX}{task.session_id}"
                await redis.zadd(index_key, {task.task_id: task.created_at})
                await redis.expire(index_key, self.TASK_TTL)

            logger.info("长任务已创建: task_id=%s agent=%s", task.task_id, task.agent_name)
        except Exception as e:
            logger.warning("长任务存储失败: %s", e)

        return task.task_id

    async def execute_task(self, task_id: str) -> None:
        """执行长任务（通过消息队列异步）

        将任务提交到消息队列，由 Worker 异步执行。

        Args:
            task_id: 任务ID
        """
        try:
            from agent.core.message_queue import enqueue, QueueName, TaskPriority

            await enqueue(
                queue=QueueName.TASK,
                task_type="long_task",
                payload={"task_id": task_id},
                priority=TaskPriority.NORMAL,
                max_retries=1,
            )
            logger.info("长任务已提交到队列: task_id=%s", task_id)
        except Exception as e:
            logger.error("长任务提交失败: task_id=%s error=%s", task_id, e)

    async def save_checkpoint(
        self,
        task_id: str,
        step_index: int,
        step_name: str,
        intermediate_result: dict[str, Any],
    ) -> None:
        """保存检查点

        每完成一个步骤自动调用，将中间结果持久化。

        Args:
            task_id: 任务ID
            step_index: 步骤索引
            step_name: 步骤名称
            intermediate_result: 中间结果
        """
        task = await self.get_task_status(task_id)
        if task is None:
            logger.warning("保存检查点失败: 任务 %s 不存在", task_id)
            return

        checkpoint = {
            "task_id": task_id,
            "step_index": step_index,
            "step_name": step_name,
            "intermediate_result": intermediate_result,
            "created_at": time.time(),
        }

        task.checkpoints.append(checkpoint)
        task.current_step = step_index + 1
        task.updated_at = time.time()

        await self._save_task(task)
        logger.info(
            "检查点已保存: task=%s step=%d/%d",
            task_id, step_index + 1, len(task.steps),
        )

    async def resume_task(self, task_id: str) -> None:
        """从最近检查点恢复执行

        读取最新的检查点，从下一个步骤继续执行。

        Args:
            task_id: 任务ID
        """
        task = await self.get_task_status(task_id)
        if task is None:
            logger.warning("恢复任务失败: 任务 %s 不存在", task_id)
            return

        if task.status not in (TaskStatus.FAILED, TaskStatus.PAUSED):
            logger.warning("恢复任务失败: 任务 %s 状态为 %s，无法恢复", task_id, task.status.value)
            return

        # 从最近检查点确定恢复位置
        if task.checkpoints:
            last_checkpoint = task.checkpoints[-1]
            task.current_step = last_checkpoint["step_index"] + 1
            logger.info(
                "从检查点恢复: task=%s step=%d",
                task_id, task.current_step,
            )
        else:
            task.current_step = 0
            logger.info("无检查点，从头开始: task=%s", task_id)

        task.status = TaskStatus.PENDING
        task.error = ""
        task.updated_at = time.time()
        await self._save_task(task)

        # 重新提交到队列执行
        await self.execute_task(task_id)

    async def get_task_status(self, task_id: str) -> LongTask | None:
        """查询任务状态

        Args:
            task_id: 任务ID

        Returns:
            LongTask 对象或 None
        """
        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            task_key = f"{self.TASK_KEY_PREFIX}{task_id}"
            data = await redis.get(task_key)
            if data is None:
                return None
            return LongTask.model_validate_json(data)
        except Exception as e:
            logger.warning("查询任务状态失败: %s", e)
            return None

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务

        取消正在执行的任务，并更新状态。

        Args:
            task_id: 任务ID

        Returns:
            是否取消成功
        """
        task = await self.get_task_status(task_id)
        if task is None:
            return False

        if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            logger.warning("取消任务失败: 任务 %s 状态为 %s", task_id, task.status.value)
            return False

        # 取消运行中的 asyncio.Task
        running_task = self._running_tasks.get(task_id)
        if running_task and not running_task.done():
            running_task.cancel()

        task.status = TaskStatus.CANCELLED
        task.updated_at = time.time()
        await self._save_task(task)

        logger.info("任务已取消: task_id=%s", task_id)
        return True

    async def list_session_tasks(self, session_id: str) -> list[LongTask]:
        """查询会话的所有长任务

        Args:
            session_id: 会话ID

        Returns:
            长任务列表
        """
        redis = await self._get_redis()
        if redis is None:
            return []

        try:
            index_key = f"{self.SESSION_INDEX_PREFIX}{session_id}"
            task_ids = await redis.zrange(index_key, 0, -1)

            tasks = []
            for tid in task_ids:
                task = await self.get_task_status(tid)
                if task:
                    tasks.append(task)
            return tasks
        except Exception as e:
            logger.warning("查询会话任务列表失败: %s", e)
            return []

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        """更新任务状态

        Args:
            task_id: 任务ID
            status: 新状态
            error: 错误信息
            result: 最终结果
        """
        task = await self.get_task_status(task_id)
        if task is None:
            return

        task.status = status
        task.error = error
        task.updated_at = time.time()
        if result is not None:
            task.result = result

        await self._save_task(task)

        # 发布事件总线事件
        try:
            from agent.core.event_bus import publish_event, EventType

            if status == TaskStatus.COMPLETED:
                await publish_event(EventType.AGENT_END, task.session_id, {
                    "agent_name": task.agent_name,
                    "task_id": task_id,
                    "status": "long_task_completed",
                    "steps_total": len(task.steps),
                    "steps_completed": task.current_step,
                })
            elif status == TaskStatus.FAILED:
                await publish_event(EventType.ERROR, task.session_id, {
                    "agent_name": task.agent_name,
                    "task_id": task_id,
                    "error": error,
                })
        except Exception:
            pass

    def register_running_task(self, task_id: str, asyncio_task: asyncio.Task) -> None:
        """注册运行中的 asyncio.Task（用于取消）

        Args:
            task_id: 任务ID
            asyncio_task: asyncio.Task 对象
        """
        self._running_tasks[task_id] = asyncio_task

    def unregister_running_task(self, task_id: str) -> None:
        """取消注册运行中的 asyncio.Task

        Args:
            task_id: 任务ID
        """
        self._running_tasks.pop(task_id, None)

    async def _save_task(self, task: LongTask) -> None:
        """保存任务到 Redis"""
        redis = await self._get_redis()
        if redis is None:
            return

        try:
            task_key = f"{self.TASK_KEY_PREFIX}{task.task_id}"
            await redis.set(task_key, task.model_dump_json(), ex=self.TASK_TTL)
        except Exception as e:
            logger.warning("保存任务失败: %s", e)


# ==================== 长任务执行器 ====================


async def execute_long_task_step(message: Any) -> Any:
    """长任务步骤执行处理器

    注册到消息队列的 Worker，处理 long_task 类型的消息。

    执行流程：
    1. 从 Redis 读取任务定义
    2. 从检查点确定恢复位置
    3. 逐步执行每个步骤
    4. 每步完成后保存检查点
    5. 全部完成后更新任务状态

    Args:
        message: 队列消息

    Returns:
        执行结果
    """
    manager = get_long_task_manager()
    task_id = message.payload.get("task_id", "")
    if not task_id:
        logger.error("长任务消息缺少 task_id")
        return {"error": "missing task_id"}

    task = await manager.get_task_status(task_id)
    if task is None:
        logger.error("长任务不存在: %s", task_id)
        return {"error": f"task {task_id} not found"}

    # 更新状态为运行中
    await manager.update_task_status(task_id, TaskStatus.RUNNING)

    # 创建 asyncio.Task 并注册（支持取消）
    asyncio_task = asyncio.current_task()
    if asyncio_task:
        manager.register_running_task(task_id, asyncio_task)

    try:
        start_step = task.current_step

        for i in range(start_step, len(task.steps)):
            step = task.steps[i]
            step_name = step.get("name", f"step_{i}")
            step_action = step.get("action", "")

            logger.info(
                "执行长任务步骤: task=%s step=%d/%d name=%s",
                task_id, i + 1, len(task.steps), step_name,
            )

            # 发布工具调用事件
            try:
                from agent.core.event_bus import publish_event, EventType
                await publish_event(EventType.TOOL_CALL, task.session_id, {
                    "task_id": task_id,
                    "step_index": i,
                    "step_name": step_name,
                    "action": step_action,
                })
            except Exception:
                pass

            # 执行步骤（通过 Agent 路由执行）
            try:
                from agent.teams.routing import route_and_execute

                result = await route_and_execute(
                    user_message=step_action,
                    session_id=task.session_id,
                    user_id=task.user_id,
                )

                step_result = {
                    "step_name": step_name,
                    "status": result.get("status", "unknown"),
                    "message": result.get("message", ""),
                }

                # 保存检查点
                await manager.save_checkpoint(
                    task_id, i, step_name, step_result,
                )

                # 发布工具结果事件
                try:
                    from agent.core.event_bus import publish_event, EventType
                    await publish_event(EventType.TOOL_RESULT, task.session_id, {
                        "task_id": task_id,
                        "step_index": i,
                        "step_name": step_name,
                        "success": result.get("status") == "success",
                    })
                except Exception:
                    pass

                # 步骤失败时中断
                if result.get("status") == "error":
                    await manager.update_task_status(
                        task_id, TaskStatus.FAILED,
                        error=f"步骤 {step_name} 执行失败: {result.get('message', '')}",
                    )
                    return {"error": f"step {step_name} failed"}

            except asyncio.CancelledError:
                await manager.update_task_status(task_id, TaskStatus.CANCELLED)
                return {"status": "cancelled"}

            except Exception as e:
                await manager.update_task_status(
                    task_id, TaskStatus.FAILED,
                    error=f"步骤 {step_name} 异常: {str(e)}",
                )
                return {"error": str(e)}

        # 所有步骤完成
        final_result = {
            "steps_total": len(task.steps),
            "steps_completed": len(task.steps),
            "last_checkpoint": task.checkpoints[-1] if task.checkpoints else None,
        }

        await manager.update_task_status(
            task_id, TaskStatus.COMPLETED, result=final_result,
        )

        logger.info("长任务完成: task=%s steps=%d", task_id, len(task.steps))
        return final_result

    finally:
        manager.unregister_running_task(task_id)


# ==================== 全局实例 ====================


_long_task_manager: LongTaskManager | None = None


def get_long_task_manager() -> LongTaskManager:
    """获取全局长任务管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _long_task_manager
    try:
        from agent.core.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_long_task_manager() is not None:
            return ctx.get_long_task_manager()
    except Exception:
        pass
    if _long_task_manager is None:
        _long_task_manager = LongTaskManager()
    return _long_task_manager
