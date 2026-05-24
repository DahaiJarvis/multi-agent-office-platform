"""应用上下文管理

统一管理所有核心组件的生命周期，替代分散的模块级全局变量单例。
提供集中化的初始化、获取和关闭机制，确保组件创建顺序可控、资源释放完整。
"""

import asyncio
import logging
from types import TracebackType

logger = logging.getLogger(__name__)


class AppContext:
    """应用上下文

    集中管理所有核心服务实例，统一初始化和关闭流程。
    使用方式：
      - 启动时调用 initialize() 按顺序初始化所有组件
      - 运行时通过 get_xxx() 获取已初始化的组件实例
      - 关闭时调用 shutdown() 按逆序释放所有资源
    """

    def __init__(self) -> None:
        self._session_manager = None
        self._event_bus = None
        self._approval_flow_manager = None
        self._human_confirm_manager = None
        self._token_budget_manager = None
        self._feedback_service = None
        self._long_task_manager = None
        self._scheduled_task_manager = None
        self._task_checkpoint_store = None
        self._task_execution_engine = None
        self._pool_manager = None
        self._scheduler_worker = None
        self._audit_flush_task: asyncio.Task | None = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def initialize(self) -> None:
        """按依赖顺序初始化所有核心组件"""
        if self._initialized:
            return

        from agent.core.infrastructure.config import get_settings
        settings = get_settings()

        from agent.core.session.session_manager import SessionManager
        self._session_manager = SessionManager()
        try:
            from deploy.ha_manager import DegradationManager
            DegradationManager.register_handler(
                "redis",
                on_degraded=self._session_manager.switch_to_memory_fallback,
                on_recovered=self._session_manager.switch_to_redis,
            )
        except Exception:
            pass
        logger.info("会话管理器初始化完成")

        from agent.core.infrastructure.event_bus import EventBus
        self._event_bus = EventBus()
        logger.info("事件总线初始化完成")

        from agent.core.workflow.approval_flow import ApprovalFlowManager
        self._approval_flow_manager = ApprovalFlowManager()
        logger.info("审批流管理器初始化完成")

        from agent.core.workflow.human_confirm import HumanConfirmManager
        self._human_confirm_manager = HumanConfirmManager()
        logger.info("人工确认管理器初始化完成")

        from agent.core.model.token_budget import TokenBudgetManager
        self._token_budget_manager = TokenBudgetManager()
        logger.info("Token预算管理器初始化完成")

        from agent.core.observability.feedback import FeedbackService
        self._feedback_service = FeedbackService()
        logger.info("反馈服务初始化完成")

        from agent.core.workflow.long_task import LongTaskManager
        self._long_task_manager = LongTaskManager()
        logger.info("长任务管理器初始化完成")

        from agent.core.workflow.message_queue import ScheduledTaskManager
        self._scheduled_task_manager = ScheduledTaskManager()
        logger.info("定时任务管理器初始化完成")

        from agent.core.workflow.task_checkpoint import TaskCheckpointStore
        self._task_checkpoint_store = TaskCheckpointStore()
        logger.info("任务检查点存储初始化完成")

        from agent.teams.task_execution_engine import TaskExecutionEngine
        self._task_execution_engine = TaskExecutionEngine()
        logger.info("任务编排引擎初始化完成")

        try:
            from agent.core.performance.connection_pool import ConnectionPoolManager
            self._pool_manager = ConnectionPoolManager(redis_url=settings.redis_url)
            await self._pool_manager.initialize()
            logger.info("连接池管理器初始化完成")
        except Exception as e:
            logger.warning("连接池管理器初始化失败（非致命）: %s", e)

        try:
            from agent.core.workflow.scheduler import get_scheduler_worker
            self._scheduler_worker = get_scheduler_worker()
            await self._scheduler_worker.start()
            logger.info("定时任务扫描器已启动")
        except Exception as e:
            logger.warning("定时任务扫描器启动失败（非致命）: %s", e)

        try:
            await self._event_bus.start_redis_listener()
            logger.info("事件总线 Redis 监听器已启动")
        except Exception as e:
            logger.warning("事件总线 Redis 监听器启动失败（非致命）: %s", e)

        try:
            from agent.core.workflow.message_queue import register_long_task_handler
            await register_long_task_handler()
            logger.info("长任务处理器已注册")
        except Exception as e:
            logger.warning("长任务处理器注册失败（非致命）: %s", e)

        self._audit_flush_task = asyncio.create_task(self._audit_flush_loop())

        self._initialized = True
        logger.info("应用上下文初始化完成")

    async def shutdown(self) -> None:
        """按逆序关闭所有核心组件，释放资源"""
        if not self._initialized:
            return

        if self._audit_flush_task:
            self._audit_flush_task.cancel()
            try:
                await self._audit_flush_task
            except asyncio.CancelledError:
                pass

        if self._scheduler_worker:
            try:
                await self._scheduler_worker.stop()
            except Exception:
                pass

        if self._event_bus:
            try:
                await self._event_bus.stop_redis_listener()
            except Exception:
                pass

        try:
            from agent.core.observability.audit import get_audit_logger
            audit = get_audit_logger()
            await audit.flush_buffer()
        except Exception:
            pass

        if self._session_manager:
            await self._session_manager.close()

        try:
            from agent.core.mcp.mcp_integration import close_all_connections
            await close_all_connections()
        except Exception:
            pass

        if self._pool_manager:
            try:
                await self._pool_manager.shutdown()
            except Exception:
                pass

        self._initialized = False
        logger.info("应用上下文已关闭")

    async def _audit_flush_loop(self) -> None:
        """审计日志后台定时刷新"""
        from agent.core.observability.audit import get_audit_logger
        while True:
            try:
                await asyncio.sleep(5)
                audit = get_audit_logger()
                await audit.flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("审计日志刷新失败: %s", e)

    def get_session_manager(self):
        return self._session_manager

    def get_event_bus(self):
        return self._event_bus

    def get_approval_flow_manager(self):
        return self._approval_flow_manager

    def get_human_confirm_manager(self):
        return self._human_confirm_manager

    def get_token_budget_manager(self):
        return self._token_budget_manager

    def get_feedback_service(self):
        return self._feedback_service

    def get_long_task_manager(self):
        return self._long_task_manager

    def get_scheduled_task_manager(self):
        return self._scheduled_task_manager

    def get_task_checkpoint_store(self):
        return self._task_checkpoint_store

    def get_task_execution_engine(self):
        return self._task_execution_engine

    def get_pool_manager(self):
        return self._pool_manager


_app_context: AppContext | None = None


def get_app_context() -> AppContext:
    """获取全局应用上下文"""
    global _app_context
    if _app_context is None:
        _app_context = AppContext()
    return _app_context
