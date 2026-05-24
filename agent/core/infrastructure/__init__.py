"""基础设施模块

提供系统运行所需的基础能力：配置管理、连接管理、并发原语、事件机制、插件架构。
"""

from agent.core.infrastructure.config import Settings, get_settings
from agent.core.infrastructure.redis_manager import get_redis_client, close_redis_client
from agent.core.infrastructure.async_utils import schedule_async_task, get_persist_ttl_seconds
from agent.core.infrastructure.circuit_breaker import CircuitBreaker, get_circuit_breaker
from agent.core.infrastructure.distributed_lock import DistributedLock, distributed_lock
from agent.core.infrastructure.event_bus import (
    EventType,
    Event,
    EventBus,
    get_event_bus,
    publish_event,
    subscribe_events,
)
from agent.core.infrastructure.plugin_system import (
    PluginManifest,
    PluginInstance,
    register_plugin,
    unregister_plugin,
    enable_plugin,
    disable_plugin,
    execute_hooks,
    list_plugins,
    get_plugin,
)

__all__ = [
    "Settings",
    "get_settings",
    "get_redis_client",
    "close_redis_client",
    "schedule_async_task",
    "get_persist_ttl_seconds",
    "CircuitBreaker",
    "get_circuit_breaker",
    "DistributedLock",
    "distributed_lock",
    "EventType",
    "Event",
    "EventBus",
    "get_event_bus",
    "publish_event",
    "subscribe_events",
    "PluginManifest",
    "PluginInstance",
    "register_plugin",
    "unregister_plugin",
    "enable_plugin",
    "disable_plugin",
    "execute_hooks",
    "list_plugins",
    "get_plugin",
]
