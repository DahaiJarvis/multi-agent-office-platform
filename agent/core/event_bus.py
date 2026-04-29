"""事件总线

提供内部事件发布/订阅机制，支持前端实时展示 Agent 执行过程。

核心能力：
  - 事件发布：在关键节点发布事件（Agent 启动/结束、工具调用、护栏拦截等）
  - 事件订阅：按会话ID过滤订阅事件，支持 SSE 实时推送
  - 跨进程传播：基于 Redis Pub/Sub 实现分布式事件传播
  - 进程内低延迟：使用 asyncio.Queue 实现同进程内快速事件传递

与 Langfuse 追踪联动，事件发布同时记录到追踪系统。
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from enum import Enum
from typing import Any

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """事件类型"""

    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    GUARDRAIL_BLOCK = "guardrail_block"
    APPROVAL_PENDING = "approval_pending"
    CONTEXT_COMPACTION = "context_compaction"
    DEGRADATION = "degradation"
    INTENT_CLASSIFIED = "intent_classified"
    RETRY = "retry"
    ERROR = "error"


class Event:
    """事件对象

    Attributes:
        event_type: 事件类型
        session_id: 会话ID（用于过滤）
        data: 事件数据
        timestamp: 事件时间戳
    """

    __slots__ = ("event_type", "session_id", "data", "timestamp")

    def __init__(
        self,
        event_type: EventType,
        session_id: str,
        data: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> None:
        self.event_type = event_type
        self.session_id = session_id
        self.data = data or {}
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "event_type": self.event_type.value,
            "session_id": self.session_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Event":
        """从 JSON 字符串反序列化"""
        data = json.loads(raw)
        return cls(
            event_type=EventType(data["event_type"]),
            session_id=data["session_id"],
            data=data.get("data", {}),
            timestamp=data.get("timestamp", 0),
        )


class EventBus:
    """事件总线

    双层架构：
      - 进程内层：asyncio.Queue 实现低延迟事件传递
      - 跨进程层：Redis Pub/Sub 实现分布式事件传播

    订阅者通过 subscribe() 获取 AsyncGenerator，
    事件按 session_id 过滤后推送给订阅者。
    """

    # Redis Pub/Sub 频道名
    CHANNEL_NAME = "event_bus:events"

    # 订阅者队列最大长度
    SUBSCRIBER_QUEUE_SIZE = 1000

    def __init__(self) -> None:
        # 进程内订阅者：session_id -> list[asyncio.Queue]
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}
        # 全局订阅者（接收所有事件）
        self._global_subscribers: list[asyncio.Queue[Event]] = []
        # Redis 客户端
        self._redis: Any = None
        self._pubsub: Any = None
        # Redis 监听任务
        self._listener_task: asyncio.Task | None = None

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
                logger.warning("事件总线 Redis 连接失败: %s", e)
        return self._redis

    async def publish(self, event: Event) -> None:
        """发布事件

        同时推送到进程内订阅者和 Redis Pub/Sub。

        Args:
            event: 事件对象
        """
        # 1. 推送到进程内订阅者
        self._dispatch_local(event)

        # 2. 推送到 Redis Pub/Sub（跨进程传播）
        try:
            redis = await self._get_redis()
            if redis:
                await redis.publish(self.CHANNEL_NAME, event.to_json())
        except Exception as e:
            logger.debug("事件发布到 Redis 失败: %s", e)

        # 3. 联动 Langfuse 追踪
        self._trace_event(event)

    def _dispatch_local(self, event: Event) -> None:
        """分发事件到进程内订阅者"""
        # 按 session_id 分发
        queues = self._subscribers.get(event.session_id, [])
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("订阅者队列已满，丢弃事件: %s", event.event_type.value)

        # 全局订阅者
        for queue in self._global_subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("全局订阅者队列已满，丢弃事件: %s", event.event_type.value)

    async def subscribe(
        self,
        session_id: str,
        event_types: list[EventType] | None = None,
    ) -> AsyncGenerator[Event, None]:
        """订阅事件（按会话ID过滤）

        创建一个 asyncio.Queue 作为订阅者缓冲区，
        通过 AsyncGenerator 逐个产出过滤后的事件。

        Args:
            session_id: 会话ID
            event_types: 感兴趣的事件类型列表（None 表示所有类型）

        Yields:
            过滤后的 Event 对象
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(
            maxsize=self.SUBSCRIBER_QUEUE_SIZE,
        )

        if session_id not in self._subscribers:
            self._subscribers[session_id] = []
        self._subscribers[session_id].append(queue)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if event_types and event.event_type not in event_types:
                        continue
                    yield event
                except asyncio.TimeoutError:
                    # 发送心跳，保持 SSE 连接活跃
                    yield Event(
                        event_type=EventType.AGENT_START,
                        session_id=session_id,
                        data={"heartbeat": True},
                    )
        finally:
            if session_id in self._subscribers:
                self._subscribers[session_id].remove(queue)
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]

    async def start_redis_listener(self) -> None:
        """启动 Redis Pub/Sub 监听器

        监听跨进程发布的事件，并分发到本地订阅者。
        """
        redis = await self._get_redis()
        if redis is None:
            return

        try:
            self._pubsub = redis.pubsub()
            await self._pubsub.subscribe(self.CHANNEL_NAME)

            async def _listen() -> None:
                async for message in self._pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        event = Event.from_json(message["data"])
                        self._dispatch_local(event)
                    except Exception as e:
                        logger.debug("Redis 事件解析失败: %s", e)

            self._listener_task = asyncio.create_task(_listen())
            logger.info("事件总线 Redis 监听器已启动")
        except Exception as e:
            logger.warning("事件总线 Redis 监听器启动失败: %s", e)

    async def stop_redis_listener(self) -> None:
        """停止 Redis Pub/Sub 监听器"""
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None

        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(self.CHANNEL_NAME)
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

        logger.info("事件总线 Redis 监听器已停止")

    def _trace_event(self, event: Event) -> None:
        """联动 Langfuse 追踪"""
        try:
            from observability.tracing import langfuse_tracer

            if event.event_type == EventType.TOOL_CALL:
                langfuse_tracer.trace_tool_call(
                    trace_id=event.session_id,
                    tool_name=event.data.get("tool_name", ""),
                    tool_input=event.data.get("tool_input", {}),
                    tool_output=None,
                    duration_ms=event.data.get("duration_ms", 0),
                    status="running",
                )
            elif event.event_type == EventType.TOOL_RESULT:
                langfuse_tracer.trace_tool_call(
                    trace_id=event.session_id,
                    tool_name=event.data.get("tool_name", ""),
                    tool_input={},
                    tool_output=event.data.get("tool_output"),
                    duration_ms=event.data.get("duration_ms", 0),
                    status="success" if event.data.get("success", True) else "error",
                )
            elif event.event_type == EventType.CONTEXT_COMPACTION:
                langfuse_tracer.trace_context_compaction(
                    trace_id=event.session_id,
                    original_tokens=event.data.get("original_tokens", 0),
                    compacted_tokens=event.data.get("compacted_tokens", 0),
                    strategy=event.data.get("strategy", "summarize"),
                )
        except Exception:
            pass


# ==================== 便捷发布函数 ====================


async def publish_event(
    event_type: EventType,
    session_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    """便捷的事件发布函数

    Args:
        event_type: 事件类型
        session_id: 会话ID
        data: 事件数据
    """
    bus = get_event_bus()
    event = Event(event_type=event_type, session_id=session_id, data=data)
    await bus.publish(event)


async def subscribe_events(
    session_id: str,
    event_types: list[EventType] | None = None,
) -> AsyncGenerator[Event, None]:
    """便捷的事件订阅函数

    Args:
        session_id: 会话ID
        event_types: 感兴趣的事件类型列表

    Yields:
        过滤后的 Event 对象
    """
    bus = get_event_bus()
    async for event in bus.subscribe(session_id, event_types):
        yield event


# ==================== 全局实例 ====================


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """获取全局事件总线实例"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
