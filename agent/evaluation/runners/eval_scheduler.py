"""评估调度器

注意：本文件与 agent/evaluation/replay/eval_scheduler.py 同名但职责不同。
- 本文件（runners/eval_scheduler.py）：spec 01 评估调度，定时扫描失败 trace 触发评估
- replay/eval_scheduler.py：spec 04 闭环调度，驱动 Trace-Eval-Improve 完整闭环

定时扫描失败 trace，触发评估与 fixture 转化。
对应 spec 文档 3.11 节。

调度策略：
  - 定时扫描近 N 小时的失败 trace
  - 对每个失败 trace 执行转化与评估
  - 记录结果并通知
"""

import asyncio
import logging
from typing import Any

from agent.evaluation.fixtures.fixture_schema import Fixture

logger = logging.getLogger(__name__)


class EvalScheduler:
    """评估调度器

    定时扫描失败 trace，触发评估与 fixture 转化。

    使用示例：
        scheduler = EvalScheduler()
        # 执行一次定时任务
        await scheduler.run_scheduled()
    """

    def __init__(
        self,
        converter: Any = None,  # TraceToFixtureConverter | None
        runner: Any = None,     # HarnessRunner | None
        span_cache: Any = None,
    ) -> None:
        """初始化

        Args:
            converter: TraceToFixtureConverter 实例，None 时延迟创建
            runner: HarnessRunner 实例，None 时延迟创建
            span_cache: tracing.py 的 SpanCache 实例，None 时不可用
        """
        self._converter = converter
        self._runner = runner
        self._span_cache = span_cache

    def _ensure_converter(self) -> Any:
        """延迟初始化 converter"""
        if self._converter is None:
            try:
                from agent.evaluation.replay.trace_to_fixture import TraceToFixtureConverter
                self._converter = TraceToFixtureConverter()
            except Exception as e:
                logger.warning("TraceToFixtureConverter 初始化失败: %s", e)
        return self._converter

    def _ensure_runner(self) -> Any:
        """延迟初始化 runner"""
        if self._runner is None:
            try:
                from agent.evaluation.runners.harness_runner import HarnessRunner
                self._runner = HarnessRunner()
            except Exception as e:
                logger.warning("HarnessRunner 初始化失败: %s", e)
        return self._runner

    async def scan_failed_traces(
        self,
        window_hours: int = 24,
        max_count: int = 50,
    ) -> list[str]:
        """扫描近 N 小时的失败 trace

        筛选条件：status=failed / duration 异常 / 用户负反馈

        Args:
            window_hours: 时间窗口（小时）
            max_count: 最大处理数量

        Returns:
            失败 session_id 列表
        """
        if self._span_cache is None:
            logger.warning("SpanCache 未配置，无法扫描失败 trace")
            return []

        try:
            # 尝试调用 SpanCache 的失败 trace 检索方法
            if hasattr(self._span_cache, "get_failed_traces"):
                failed_sessions = await self._span_cache.get_failed_traces(
                    window_hours=window_hours,
                    max_count=max_count,
                )
                logger.info(
                    "扫描失败 trace: 窗口 %dh, 找到 %d 个",
                    window_hours, len(failed_sessions),
                )
                return failed_sessions
            else:
                logger.warning("SpanCache 不支持 get_failed_traces 方法")
                return []
        except Exception as e:
            logger.error("扫描失败 trace 异常: %s", e)
            return []

    async def process_failed_trace(self, session_id: str) -> Fixture | None:
        """处理单个失败 trace

        流程：
            1. 转 fixture
            2. 加入评估数据集
            3. 运行评估
            4. 记录结果

        Args:
            session_id: 失败 session ID

        Returns:
            生成的 fixture（失败返回 None）
        """
        converter = self._ensure_converter()
        if converter is None:
            logger.error("converter 不可用，无法处理 session=%s", session_id)
            return None

        try:
            # 1. 转 fixture
            fixture = await converter.convert(session_id)
            logger.info("失败 trace 转化成功: session=%s -> fixture=%s", session_id, fixture.fixture_id)

            # 2. 运行评估（可选）
            runner = self._ensure_runner()
            if runner is not None:
                result = await runner.run_single(fixture)
                logger.info(
                    "新 fixture 评估: %s success=%s score=%.2f",
                    fixture.fixture_id, result.success,
                    result.judge_result.overall_score if result.judge_result else 0.0,
                )

            return fixture

        except Exception as e:
            logger.error("处理失败 trace 异常 session=%s: %s", session_id, e)
            return None

    async def run_scheduled(self) -> None:
        """执行一次定时评估任务

        由 scheduler.py 调度，默认每日执行。
        """
        logger.info("定时评估任务开始")

        # 1. 扫描失败 trace
        failed_sessions = await self.scan_failed_traces(
            window_hours=24,
            max_count=50,
        )

        if not failed_sessions:
            logger.info("无失败 trace 需要处理")
            return

        # 2. 逐个处理（串行避免并发压力）
        success_count = 0
        for session_id in failed_sessions:
            fixture = await self.process_failed_trace(session_id)
            if fixture is not None:
                success_count += 1

        logger.info(
            "定时评估任务完成: 处理 %d/%d 个失败 trace",
            success_count, len(failed_sessions),
        )
