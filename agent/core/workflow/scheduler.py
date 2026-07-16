"""定时任务扫描器

作为消息队列的消费者运行，定期扫描 Redis ZSET 中到期的定时任务，
执行任务并通过渠道适配器推送结果。

同时包含业务指标聚合定时任务，每小时从 Prometheus 采集数据写入 Redis 缓存。
另外包含 Trace-Eval-Improve 闭环评估任务，每 24h 扫描失败 session。

运行流程：
  1. 每秒扫描 Redis ZSET 中到期的任务
  2. 到期任务 -> 调用 route_and_execute 执行
  3. 执行结果 -> 通过渠道适配器推送
  4. 更新 next_run_at
  5. 每小时聚合业务指标到 Redis 缓存
  6. 每 24h 执行 Trace-Eval-Improve 闭环评估（spec 04）
"""

import asyncio
import logging

from agent.core.workflow.message_queue import get_scheduled_task_manager, ScheduledTask

logger = logging.getLogger(__name__)

# 业务指标聚合间隔（秒）
ANALYTICS_AGGREGATE_INTERVAL = 3600

# Trace-Eval-Improve 闭环评估间隔（秒），默认 24h
EVAL_LOOP_INTERVAL = 86400


class SchedulerWorker:
    """定时任务扫描器

    定期扫描 Redis ZSET 中到期的任务，执行并推送结果。
    同时运行业务指标聚合和 Trace-Eval-Improve 闭环评估后台任务。
    """

    def __init__(self, poll_interval: float = 1.0) -> None:
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._analytics_task: asyncio.Task | None = None
        self._eval_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动扫描循环"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        self._analytics_task = asyncio.create_task(self._analytics_aggregate_loop())
        self._eval_task = asyncio.create_task(self._eval_loop())
        logger.info("定时任务扫描器已启动: poll_interval=%.1fs", self._poll_interval)

    async def stop(self) -> None:
        """停止扫描循环"""
        self._running = False
        for task in (self._task, self._analytics_task, self._eval_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("定时任务扫描器已停止")

    async def _poll_loop(self) -> None:
        """扫描循环"""
        while self._running:
            try:
                await self._poll_and_execute()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("定时任务扫描循环异常: %s", e)

            await asyncio.sleep(self._poll_interval)

    async def _poll_and_execute(self) -> None:
        """扫描到期任务并执行"""
        mgr = get_scheduled_task_manager()
        due_tasks = await mgr.poll_due_tasks()

        if not due_tasks:
            return

        logger.info("扫描到 %d 个到期定时任务", len(due_tasks))

        for task in due_tasks:
            try:
                await self._execute_task(task)
                await mgr.mark_task_executed(task.task_id)
            except Exception as e:
                logger.error(
                    "定时任务执行失败: id=%s name=%s error=%s",
                    task.task_id, task.name, e,
                )

    async def _execute_task(self, task: ScheduledTask) -> None:
        """执行定时任务

        1. 调用 route_and_execute 执行任务
        2. 通过渠道适配器推送结果

        Args:
            task: 定时任务定义
        """
        logger.info(
            "执行定时任务: id=%s name=%s agent=%s",
            task.task_id, task.name, task.agent_name,
        )

        result = await self._run_agent_task(task)

        if result:
            await self._push_result(task, result)

    async def _run_agent_task(self, task: ScheduledTask) -> str:
        """调用 Agent 执行任务

        Args:
            task: 定时任务定义

        Returns:
            Agent 执行结果
        """
        try:
            from agent.teams.routing import route_and_execute
            from agent.core.session.session_manager import get_session_manager

            # 创建临时会话
            session_mgr = await get_session_manager()
            session = await session_mgr.create_session(
                user_id=task.target_user or "scheduler",
                channel=task.channel,
            )

            # 执行任务
            result = await route_and_execute(
                user_message=task.task_prompt,
                session_id=session.session_id,
                user_id=task.target_user or "scheduler",
            )

            return result.get("message", "")

        except Exception as e:
            logger.error("Agent 执行定时任务失败: %s", e)
            return f"任务执行失败: {e}"

    async def _push_result(self, task: ScheduledTask, result: str) -> None:
        """推送执行结果到目标渠道

        Args:
            task: 定时任务定义
            result: 执行结果
        """
        try:
            from gateway.adapters.channel_adapter import push_notification

            await push_notification(
                channel=task.channel,
                user_id=task.target_user,
                message=result,
                title=f"定时任务: {task.name}",
            )
        except Exception as e:
            logger.warning("推送定时任务结果失败: channel=%s error=%s", task.channel, e)

    async def _analytics_aggregate_loop(self) -> None:
        """业务指标聚合循环

        每小时从 Prometheus 采集业务指标数据，聚合后写入 Redis 缓存，
        供业务分析 API 查询使用。
        """
        while self._running:
            try:
                await asyncio.sleep(ANALYTICS_AGGREGATE_INTERVAL)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            try:
                from observability.business_analytics import aggregate_daily_metrics
                await aggregate_daily_metrics()
            except Exception as e:
                logger.error("业务指标聚合失败: %s", e)

    async def _eval_loop(self) -> None:
        """Trace-Eval-Improve 闭环评估循环（新增，对应 spec 04 第 9.2 节）

        每 24h 执行一次 Trace-Eval-Improve 闭环评估：
          1. 扫描近 24h 的失败 session
          2. 对每个失败 session 执行回放、评估、归档
          3. 生成改进项（护栏规则候选）

        降级策略：
          - EvalScheduler 初始化失败时跳过本轮
          - 单轮评估异常不影响下一轮
        """
        logger.info("Trace-Eval-Improve 闭环评估循环已启动: interval=%ds", EVAL_LOOP_INTERVAL)

        while self._running:
            try:
                await asyncio.sleep(EVAL_LOOP_INTERVAL)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            try:
                await self._run_eval_scheduled()
            except Exception as e:
                logger.error("Trace-Eval-Improve 闭环评估失败: %s", e)

    async def _run_eval_scheduled(self) -> None:
        """执行一次 Trace-Eval-Improve 闭环评估

        延迟初始化 EvalScheduler，调用 run_scheduled 执行完整闭环。
        """
        try:
            from agent.evaluation.replay.eval_scheduler import EvalScheduler
            from observability.tracing import span_cache
            from agent.core.observability.feedback import get_feedback_service

            scheduler = EvalScheduler(
                span_cache=span_cache,
                feedback_service=get_feedback_service(),
            )
            await scheduler.run_scheduled()
        except Exception as e:
            logger.error("执行 Trace-Eval-Improve 闭环评估异常: %s", e)


# 全局调度器实例
_scheduler_worker: SchedulerWorker | None = None


def get_scheduler_worker() -> SchedulerWorker:
    """获取全局调度器实例"""
    global _scheduler_worker
    if _scheduler_worker is None:
        _scheduler_worker = SchedulerWorker()
    return _scheduler_worker
