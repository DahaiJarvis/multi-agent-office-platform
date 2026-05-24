"""异步工具函数

提供同步上下文中调度异步任务的公共能力，
统一处理事件循环检测和任务调度逻辑。
同时提供持久化 TTL 等公共配置的统一获取入口。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


def schedule_async_task(coro, task_name: str = "异步任务") -> None:
    """在同步上下文中调度异步任务（发后即忘）

    尝试在当前运行中的事件循环创建后台任务。
    如果没有运行中的事件循环，记录警告并跳过，
    不使用 asyncio.run() 回退（避免在应用事件循环外创建新循环导致连接池等资源不可用）。

    Args:
        coro: 协程对象
        task_name: 任务名称，用于日志标识
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        logger.warning(
            "%s 调度跳过: 无运行中的事件循环，持久化操作未执行",
            task_name,
        )
    except Exception as e:
        logger.warning("%s 调度失败: %s", task_name, e)


def get_persist_ttl_seconds() -> int:
    """获取持久化数据的 Redis TTL（秒）

    从全局配置读取 persist_ttl_days，转换为秒数。
    默认 90 天。

    Returns:
        TTL 秒数
    """
    try:
        from agent.core.config import get_settings
        settings = get_settings()
        return settings.persist_ttl_days * 86400
    except Exception:
        return 90 * 86400
