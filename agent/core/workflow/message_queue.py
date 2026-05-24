"""消息队列系统

支持异步任务处理、Agent 协作和流量削峰。

实现方式：
  - 基于 Redis 的消息队列（生产环境推荐）
  - 基于内存的队列（开发/测试环境降级）

核心能力：
  - 任务队列：异步执行耗时操作（文档处理、批量审批等）
  - Agent 协作队列：Agent 间的消息传递与编排
  - 流量削峰：请求速率控制与平滑处理
  - 延迟任务：支持定时/延迟执行
  - 死信队列：失败任务自动归档与重试

与 Dify 的队列系统、Coze 的工作流引擎对齐。
"""

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from agent.core.infrastructure.config import get_settings

logger = logging.getLogger(__name__)


class QueueName(str, Enum):
    """队列名称"""

    TASK = "queue:task"
    AGENT_COLLAB = "queue:agent_collab"
    NOTIFICATION = "queue:notification"
    DEAD_LETTER = "queue:dead_letter"


class TaskPriority(int, Enum):
    """任务优先级"""

    LOW = 0
    NORMAL = 5
    HIGH = 10
    URGENT = 20


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"


class QueueMessage(BaseModel):
    """队列消息"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    queue: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str = ""
    result: Any = None
    delay_seconds: float = 0
    scheduled_at: float | None = None


class QueueStats(BaseModel):
    """队列统计"""

    queue_name: str
    pending_count: int = 0
    running_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    dead_count: int = 0
    avg_latency_ms: float = 0


# ==================== 内存队列实现 ====================


class InMemoryQueueBackend:
    """基于内存的队列后端

    适用于开发/测试环境，不支持持久化。
    """

    def __init__(self):
        self._queues: dict[str, list[QueueMessage]] = {}
        self._running: dict[str, QueueMessage] = {}
        self._completed: dict[str, list[QueueMessage]] = {}
        self._dead: dict[str, list[QueueMessage]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, message: QueueMessage) -> str:
        async with self._lock:
            queue = message.queue
            if queue not in self._queues:
                self._queues[queue] = []
            self._queues[queue].append(message)
            self._queues[queue].sort(key=lambda m: m.priority, reverse=True)
        return message.id

    async def dequeue(self, queue: str, timeout: float = 5.0) -> QueueMessage | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            async with self._lock:
                if queue in self._queues and self._queues[queue]:
                    now = time.time()
                    for i, msg in enumerate(self._queues[queue]):
                        if msg.scheduled_at and now < msg.scheduled_at:
                            continue
                        message = self._queues[queue].pop(i)
                        message.status = TaskStatus.RUNNING
                        message.started_at = time.time()
                        self._running[message.id] = message
                        return message
            await asyncio.sleep(0.1)
        return None

    async def complete(self, message_id: str, result: Any = None) -> None:
        async with self._lock:
            message = self._running.pop(message_id, None)
            if message:
                message.status = TaskStatus.COMPLETED
                message.completed_at = time.time()
                message.result = result
                if message.queue not in self._completed:
                    self._completed[message.queue] = []
                self._completed[message.queue].append(message)

    async def fail(self, message_id: str, error: str) -> None:
        async with self._lock:
            message = self._running.pop(message_id, None)
            if not message:
                return

            message.retry_count += 1
            message.error = error

            if message.retry_count < message.max_retries:
                message.status = TaskStatus.RETRYING
                message.scheduled_at = time.time() + (2 ** message.retry_count) * 5
                if message.queue not in self._queues:
                    self._queues[message.queue] = []
                self._queues[message.queue].append(message)
                self._queues[message.queue].sort(key=lambda m: m.priority, reverse=True)
            else:
                message.status = TaskStatus.DEAD
                if message.queue not in self._dead:
                    self._dead[message.queue] = []
                self._dead[message.queue].append(message)

    async def get_stats(self, queue: str) -> QueueStats:
        async with self._lock:
            pending = len(self._queues.get(queue, []))
            running = sum(1 for m in self._running.values() if m.queue == queue)
            completed_list = self._completed.get(queue, [])
            dead_list = self._dead.get(queue, [])

            latencies = []
            for msg in completed_list[-100:]:
                if msg.started_at and msg.completed_at:
                    latencies.append((msg.completed_at - msg.started_at) * 1000)

            avg_latency = sum(latencies) / len(latencies) if latencies else 0

            return QueueStats(
                queue_name=queue,
                pending_count=pending,
                running_count=running,
                completed_count=len(completed_list),
                failed_count=sum(1 for m in completed_list if m.status == TaskStatus.FAILED),
                dead_count=len(dead_list),
                avg_latency_ms=avg_latency,
            )

    async def get_message(self, message_id: str) -> QueueMessage | None:
        if message_id in self._running:
            return self._running[message_id]
        for queue_messages in self._queues.values():
            for msg in queue_messages:
                if msg.id == message_id:
                    return msg
        for queue_messages in self._completed.values():
            for msg in queue_messages:
                if msg.id == message_id:
                    return msg
        return None


# ==================== Redis 队列实现 ====================


class RedisQueueBackend:
    """基于 Redis 的队列后端

    生产环境推荐，支持持久化和分布式消费。
    """

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            except ImportError:
                logger.warning("redis 包未安装，请运行: pip install redis")
                raise
        return self._redis

    async def enqueue(self, message: QueueMessage) -> str:
        redis = await self._get_redis()
        key = f"mq:{message.queue}"
        data = message.model_dump_json()
        score = message.priority + (1e10 - time.time())
        await redis.zadd(key, {data: score})
        return message.id

    async def dequeue(self, queue: str, timeout: float = 5.0) -> QueueMessage | None:
        redis = await self._get_redis()
        key = f"mq:{queue}"
        deadline = time.time() + timeout

        while time.time() < deadline:
            now = time.time()
            items = await redis.zrangebyscore(key, "-inf", "+inf", start=0, num=10)
            for item in items:
                try:
                    message = QueueMessage.model_validate_json(item)
                    if message.scheduled_at and now < message.scheduled_at:
                        continue
                    removed = await redis.zrem(key, item)
                    if removed:
                        message.status = TaskStatus.RUNNING
                        message.started_at = time.time()
                        await redis.hset(f"mq:running:{queue}", message.id, message.model_dump_json())
                        return message
                except Exception:
                    await redis.zrem(key, item)
            await asyncio.sleep(0.1)

        return None

    async def complete(self, message_id: str, result: Any = None) -> None:
        redis = await self._get_redis()
        async for queue_name in QueueName:
            data = await redis.hget(f"mq:running:{queue_name.value}", message_id)
            if data:
                message = QueueMessage.model_validate_json(data)
                message.status = TaskStatus.COMPLETED
                message.completed_at = time.time()
                message.result = result
                await redis.hdel(f"mq:running:{queue_name.value}", message_id)
                await redis.lpush(f"mq:completed:{queue_name.value}", message.model_dump_json())
                break

    async def fail(self, message_id: str, error: str) -> None:
        redis = await self._get_redis()
        async for queue_name in QueueName:
            data = await redis.hget(f"mq:running:{queue_name.value}", message_id)
            if data:
                message = QueueMessage.model_validate_json(data)
                message.retry_count += 1
                message.error = error

                await redis.hdel(f"mq:running:{queue_name.value}", message_id)

                if message.retry_count < message.max_retries:
                    message.status = TaskStatus.RETRYING
                    message.scheduled_at = time.time() + (2 ** message.retry_count) * 5
                    score = message.priority + (1e10 - time.time())
                    await redis.zadd(f"mq:{message.queue}", {message.model_dump_json(): score})
                else:
                    message.status = TaskStatus.DEAD
                    await redis.lpush(f"mq:dead:{message.queue}", message.model_dump_json())
                break

    async def get_stats(self, queue: str) -> QueueStats:
        redis = await self._get_redis()
        pending = await redis.zcard(f"mq:{queue}")
        running = await redis.hlen(f"mq:running:{queue}")
        completed_count = await redis.llen(f"mq:completed:{queue}")
        dead_count = await redis.llen(f"mq:dead:{queue}")

        return QueueStats(
            queue_name=queue,
            pending_count=pending,
            running_count=running,
            completed_count=completed_count,
            dead_count=dead_count,
        )

    async def get_message(self, message_id: str) -> QueueMessage | None:
        redis = await self._get_redis()
        async for queue_name in QueueName:
            data = await redis.hget(f"mq:running:{queue_name.value}", message_id)
            if data:
                return QueueMessage.model_validate_json(data)
        return None


# ==================== 队列管理器 ====================


_backend: InMemoryQueueBackend | RedisQueueBackend | None = None
_workers: dict[str, list[asyncio.Task]] = {}
_handlers: dict[str, Callable[[QueueMessage], Coroutine]] = {}
_running = False


def _init_backend() -> InMemoryQueueBackend | RedisQueueBackend:
    """初始化队列后端"""
    settings = get_settings()
    redis_url = getattr(settings, "redis_url", "")

    if redis_url:
        try:
            backend = RedisQueueBackend(redis_url)
            logger.info("消息队列使用 Redis 后端: %s", redis_url)
            return backend
        except Exception as e:
            logger.warning("Redis 后端初始化失败，降级到内存后端: %s", e)

    logger.info("消息队列使用内存后端")
    return InMemoryQueueBackend()


def get_backend() -> InMemoryQueueBackend | RedisQueueBackend:
    """获取队列后端"""
    global _backend
    if _backend is None:
        _backend = _init_backend()
    return _backend


async def enqueue(
    queue: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    priority: int = TaskPriority.NORMAL,
    delay_seconds: float = 0,
    max_retries: int = 3,
) -> str:
    """入队

    Args:
        queue: 队列名称
        task_type: 任务类型
        payload: 任务数据
        priority: 优先级
        delay_seconds: 延迟秒数
        max_retries: 最大重试次数

    Returns:
        消息 ID
    """
    backend = get_backend()
    message = QueueMessage(
        queue=queue,
        task_type=task_type,
        payload=payload or {},
        priority=priority,
        delay_seconds=delay_seconds,
        max_retries=max_retries,
    )
    if delay_seconds > 0:
        message.scheduled_at = time.time() + delay_seconds

    msg_id = await backend.enqueue(message)
    logger.debug("消息入队: queue=%s type=%s id=%s", queue, task_type, msg_id)
    return msg_id


async def register_handler(
    queue: str,
    task_type: str,
    handler: Callable[[QueueMessage], Coroutine],
) -> None:
    """注册任务处理器"""
    handler_key = f"{queue}:{task_type}"
    _handlers[handler_key] = handler
    logger.info("任务处理器已注册: %s", handler_key)


async def start_workers(queue: str, concurrency: int = 3) -> None:
    """启动队列消费者

    Args:
        queue: 队列名称
        concurrency: 并发消费者数量
    """
    global _running
    _running = True

    if queue not in _workers:
        _workers[queue] = []

    for i in range(concurrency):
        task = asyncio.create_task(_consume_loop(queue, i))
        _workers[queue].append(task)

    logger.info("队列消费者已启动: queue=%s concurrency=%d", queue, concurrency)


async def stop_workers() -> None:
    """停止所有消费者"""
    global _running
    _running = False

    for queue, tasks in _workers.items():
        for task in tasks:
            task.cancel()
        logger.info("队列消费者已停止: queue=%s", queue)
    _workers.clear()


async def _consume_loop(queue: str, worker_id: int) -> None:
    """消费循环"""
    backend = get_backend()
    logger.info("消费者启动: queue=%s worker=%d", queue, worker_id)

    while _running:
        try:
            message = await backend.dequeue(queue, timeout=2.0)
            if not message:
                continue

            handler_key = f"{queue}:{message.task_type}"
            handler = _handlers.get(handler_key)

            if not handler:
                generic_key = f"{queue}:*"
                handler = _handlers.get(generic_key)

            if not handler:
                await backend.fail(message.id, f"无处理器: {message.task_type}")
                continue

            try:
                result = await handler(message)
                await backend.complete(message.id, result)
                logger.debug("任务完成: id=%s type=%s", message.id, message.task_type)
            except Exception as e:
                logger.error("任务执行失败: id=%s type=%s error=%s", message.id, message.task_type, e)
                await backend.fail(message.id, str(e))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("消费循环异常: queue=%s worker=%d error=%s", queue, worker_id, e)
            await asyncio.sleep(1)


async def get_queue_stats(queue: str) -> QueueStats:
    """获取队列统计"""
    backend = get_backend()
    return await backend.get_stats(queue)


async def get_task_status(message_id: str) -> QueueMessage | None:
    """获取任务状态"""
    backend = get_backend()
    return await backend.get_message(message_id)


# ==================== 流量削峰 ====================


class RateLimiter:
    """滑动窗口速率限制器

    用于 API 请求的流量削峰，防止突发流量压垮后端。
    """

    def __init__(self, max_requests: int = 100, window_seconds: float = 60.0):
        self._max_requests = max_requests
        self._window = window_seconds
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """尝试获取一个请求配额

        Returns:
            True 表示允许请求，False 表示被限流
        """
        async with self._lock:
            now = time.time()
            cutoff = now - self._window
            self._timestamps = [t for t in self._timestamps if t > cutoff]

            if len(self._timestamps) >= self._max_requests:
                return False

            self._timestamps.append(now)
            return True

    async def wait_and_acquire(self, max_wait: float = 30.0) -> bool:
        """等待并获取配额

        Args:
            max_wait: 最大等待时间（秒）

        Returns:
            True 表示获取成功，False 表示超时
        """
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if await self.acquire():
                return True
            await asyncio.sleep(0.5)
        return False


# 全局限流器实例
_api_rate_limiter: RateLimiter | None = None


def get_api_rate_limiter() -> RateLimiter:
    """获取 API 全局限流器"""
    global _api_rate_limiter
    if _api_rate_limiter is None:
        settings = get_settings()
        max_rps = getattr(settings, "rate_limit_rpm", 60)
        _api_rate_limiter = RateLimiter(max_requests=max_rps, window_seconds=60.0)
    return _api_rate_limiter


# ==================== 定时任务 ====================


class ScheduledTask(BaseModel):
    """定时任务定义

    支持两种触发类型：
      - cron: 按 Cron 表达式定时执行
      - interval: 按固定间隔执行

    存储结构：
      - ZSET scheduler:tasks -> score=next_run_at, member=task_id
      - HASH scheduler:task:{task_id} -> ScheduledTask JSON
    """

    task_id: str = Field(default_factory=lambda: f"sched-{uuid.uuid4().hex[:10]}")
    name: str = Field(..., description="任务名称")
    trigger_type: str = Field(..., description="触发类型: cron / interval")
    trigger_value: str = Field(..., description="Cron 表达式 或 间隔秒数")
    agent_name: str = Field(default="", description="执行的 Agent")
    task_prompt: str = Field(default="", description="任务描述")
    channel: str = Field(default="web", description="推送渠道 (web / wecom / dingtalk)")
    target_user: str = Field(default="", description="推送目标用户")
    tenant_id: str = Field(default="", description="租户ID")
    enabled: bool = Field(default=True)
    last_run_at: float = Field(default=0)
    next_run_at: float = Field(default=0)
    created_at: float = Field(default_factory=time.time)


class ScheduledTaskManager:
    """定时任务管理器

    基于 Redis ZSET 实现定时任务调度：
      - ZSET scheduler:tasks 按 next_run_at 排序
      - HASH scheduler:task:{task_id} 存储任务详情
    """

    SCHEDULER_ZSET_KEY = "scheduler:tasks"
    TASK_KEY_PREFIX = "scheduler:task:"

    def __init__(self) -> None:
        self._redis = None

    async def _get_redis(self):
        """获取 Redis 连接"""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                settings = get_settings()
                self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            except Exception as e:
                logger.warning("ScheduledTaskManager Redis 连接失败: %s", e)
        return self._redis

    async def schedule_task(self, task: ScheduledTask) -> str:
        """注册定时任务

        Args:
            task: 定时任务定义

        Returns:
            task_id
        """
        redis = await self._get_redis()
        if redis is None:
            return ""

        # 计算首次执行时间
        if task.next_run_at == 0:
            task.next_run_at = self._calculate_next_run(task)

        # 存储任务详情
        task_key = f"{self.TASK_KEY_PREFIX}{task.task_id}"
        await redis.set(task_key, task.model_dump_json(), ex=86400 * 30)

        # 添加到 ZSET（按 next_run_at 排序）
        await redis.zadd(self.SCHEDULER_ZSET_KEY, {task.task_id: task.next_run_at})

        logger.info(
            "定时任务已注册: id=%s name=%s type=%s next_run=%d",
            task.task_id, task.name, task.trigger_type, task.next_run_at,
        )
        return task.task_id

    async def cancel_scheduled_task(self, task_id: str) -> bool:
        """取消定时任务

        Args:
            task_id: 任务ID

        Returns:
            是否成功取消
        """
        redis = await self._get_redis()
        if redis is None:
            return False

        # 从 ZSET 移除
        removed = await redis.zrem(self.SCHEDULER_ZSET_KEY, task_id)
        # 删除任务详情
        task_key = f"{self.TASK_KEY_PREFIX}{task_id}"
        await redis.delete(task_key)

        if removed:
            logger.info("定时任务已取消: id=%s", task_id)
        return removed > 0

    async def update_scheduled_task(self, task_id: str, **updates) -> bool:
        """更新定时任务

        Args:
            task_id: 任务ID
            **updates: 需要更新的字段

        Returns:
            是否成功更新
        """
        redis = await self._get_redis()
        if redis is None:
            return False

        task_key = f"{self.TASK_KEY_PREFIX}{task_id}"
        data = await redis.get(task_key)
        if data is None:
            return False

        task = ScheduledTask.model_validate_json(data)
        for key, value in updates.items():
            if hasattr(task, key):
                setattr(task, key, value)

        # 如果更新了触发配置，重新计算下次执行时间
        if "trigger_type" in updates or "trigger_value" in updates:
            task.next_run_at = self._calculate_next_run(task)

        await redis.set(task_key, task.model_dump_json(), ex=86400 * 30)

        # 更新 ZSET 中的 score
        if task.enabled:
            await redis.zadd(self.SCHEDULER_ZSET_KEY, {task_id: task.next_run_at})
        else:
            await redis.zrem(self.SCHEDULER_ZSET_KEY, task_id)

        logger.info("定时任务已更新: id=%s fields=%s", task_id, list(updates.keys()))
        return True

    async def list_scheduled_tasks(self, tenant_id: str = "") -> list[ScheduledTask]:
        """列出定时任务

        Args:
            tenant_id: 按租户过滤（可选）

        Returns:
            定时任务列表
        """
        redis = await self._get_redis()
        if redis is None:
            return []

        task_ids = await redis.zrange(self.SCHEDULER_ZSET_KEY, 0, -1)
        tasks: list[ScheduledTask] = []

        for task_id in task_ids:
            task_key = f"{self.TASK_KEY_PREFIX}{task_id}"
            data = await redis.get(task_key)
            if data:
                try:
                    task = ScheduledTask.model_validate_json(data)
                    if tenant_id and task.tenant_id != tenant_id:
                        continue
                    tasks.append(task)
                except Exception:
                    continue

        return tasks

    async def get_scheduled_task(self, task_id: str) -> ScheduledTask | None:
        """获取定时任务详情

        Args:
            task_id: 任务ID

        Returns:
            ScheduledTask 或 None
        """
        redis = await self._get_redis()
        if redis is None:
            return None

        task_key = f"{self.TASK_KEY_PREFIX}{task_id}"
        data = await redis.get(task_key)
        if data is None:
            return None

        try:
            return ScheduledTask.model_validate_json(data)
        except Exception:
            return None

    async def poll_due_tasks(self) -> list[ScheduledTask]:
        """扫描到期任务

        从 ZSET 中获取 score <= now 的任务。

        Returns:
            到期的定时任务列表
        """
        redis = await self._get_redis()
        if redis is None:
            return []

        now = time.time()
        due_ids = await redis.zrangebyscore(self.SCHEDULER_ZSET_KEY, "-inf", now)

        due_tasks: list[ScheduledTask] = []
        for task_id in due_ids:
            task = await self.get_scheduled_task(task_id)
            if task and task.enabled:
                due_tasks.append(task)

        return due_tasks

    async def mark_task_executed(self, task_id: str) -> None:
        """标记任务已执行，更新下次执行时间

        Args:
            task_id: 任务ID
        """
        redis = await self._get_redis()
        if redis is None:
            return

        task_key = f"{self.TASK_KEY_PREFIX}{task_id}"
        data = await redis.get(task_key)
        if data is None:
            return

        task = ScheduledTask.model_validate_json(data)
        task.last_run_at = time.time()
        task.next_run_at = self._calculate_next_run(task)

        await redis.set(task_key, task.model_dump_json(), ex=86400 * 30)
        await redis.zadd(self.SCHEDULER_ZSET_KEY, {task_id: task.next_run_at})

    def _calculate_next_run(self, task: ScheduledTask) -> float:
        """计算下次执行时间

        Args:
            task: 定时任务

        Returns:
            下次执行时间戳
        """
        now = time.time()

        if task.trigger_type == "interval":
            try:
                interval_seconds = int(task.trigger_value)
                return now + interval_seconds
            except (ValueError, TypeError):
                return now + 3600  # 默认1小时

        if task.trigger_type == "cron":
            # 简化实现：解析 cron 表达式中的分钟和小时
            # 格式: "MM HH" 或 "MM HH * * *"
            try:
                parts = task.trigger_value.strip().split()
                if len(parts) >= 2:
                    minute = int(parts[0])
                    hour = int(parts[1])
                    from datetime import datetime, timedelta
                    dt = datetime.now().replace(second=0, microsecond=0)
                    target = dt.replace(minute=minute % 60, hour=hour % 24)
                    if target <= dt:
                        target += timedelta(days=1)
                    return target.timestamp()
            except (ValueError, TypeError):
                pass
            return now + 86400  # 默认24小时

        return now + 3600


# 全局定时任务管理器
_scheduled_task_manager: ScheduledTaskManager | None = None


def get_scheduled_task_manager() -> ScheduledTaskManager:
    """获取全局定时任务管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _scheduled_task_manager
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_scheduled_task_manager() is not None:
            return ctx.get_scheduled_task_manager()
    except Exception:
        pass
    if _scheduled_task_manager is None:
        _scheduled_task_manager = ScheduledTaskManager()
    return _scheduled_task_manager


async def register_long_task_handler() -> None:
    """注册长任务处理器到消息队列

    将 long_task 类型的消息路由到长任务执行器。
    需要在应用启动时调用。
    """
    from agent.core.workflow.long_task import execute_long_task_step

    await register_handler(QueueName.TASK, "long_task", execute_long_task_step)
    logger.info("长任务处理器已注册到消息队列")
