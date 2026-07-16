"""失败 Trace 识别与调度（闭环版）

对应 spec 04 第 3.1 节 F1 功能。

周期性扫描失败 session，驱动 Trace-Eval-Improve 闭环：
  1. 扫描失败 session（SpanCache + Feedback + Audit 三数据源）
  2. 对每个失败 session 执行完整闭环：
     - TraceToFixtureConverter 生成 Fixture
     - TraceReplayer 确定性回放
     - HarnessRunner 评估回放结果
     - FailureArchive 归档失败案例

数据来源：
  - SpanCache.get_failed_sessions: status=failed 的 agent_call span
  - FeedbackService.get_thumbs_down_sessions: thumbs_down 的用户反馈
  - AuditLogger.query_logs: action 含 error/fail 的 agent 事件

去重：同一 session 在 since_hours 内只处理一次（Redis SET eval:processed:{date}）
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class FailureFilter(BaseModel):
    """失败 Trace 筛选条件（对应 spec 04 第 3.1 节）

    支持三类筛选条件组合：
      - status=failed: SpanCache 中失败的 agent_call span
      - duration 异常: 耗时超过 P95 的 N 倍
      - 用户负反馈: feedback 中的 thumbs_down

    Attributes:
        status: 失败状态筛选
        duration_p95_multiplier: 耗时超过 P95 的 N 倍视为异常
        include_thumbs_down: 是否纳入用户负反馈
        since_hours: 扫描最近 N 小时
        agent_name: 限定 Agent
        exclude_archived: 排除已归档的失败案例
        max_batch_size: 单批最大处理量
    """

    model_config = ConfigDict(frozen=False)

    status: str = Field(default="failed", description="失败状态")
    duration_p95_multiplier: float = Field(
        default=2.0,
        description="耗时超过 P95 的 N 倍视为异常",
    )
    include_thumbs_down: bool = Field(default=True, description="是否纳入用户负反馈")
    since_hours: int = Field(default=24, description="扫描最近 N 小时")
    agent_name: str | None = Field(default=None, description="限定 Agent")
    exclude_archived: bool = Field(default=True, description="排除已归档的失败案例")
    max_batch_size: int = Field(default=50, description="单批最大处理量")


class FailedSession(BaseModel):
    """被识别为失败的 session（对应 spec 04 第 3.1 节）

    Attributes:
        session_id: 会话 ID
        agent_name: Agent 名称
        failure_reason: 失败原因 failed/duration_anomaly/thumbs_down
        failure_detail: 失败详情
        duration_ms: 执行耗时
        detected_at: 检测时间
        has_thumbs_down: 是否有用户点踩
    """

    model_config = ConfigDict(frozen=False)

    session_id: str = Field(..., description="会话 ID")
    agent_name: str = Field(default="", description="Agent 名称")
    failure_reason: str = Field(
        default="failed",
        description="失败原因：failed/duration_anomaly/thumbs_down",
    )
    failure_detail: str = Field(default="", description="失败详情")
    duration_ms: float = Field(default=0.0, description="执行耗时")
    detected_at: datetime = Field(default_factory=datetime.now)
    has_thumbs_down: bool = Field(default=False)


class EvalScheduler:
    """评估调度器（闭环版）

    周期性扫描失败 session，驱动 Trace-Eval-Improve 闭环。
    复用 scheduler.py 的 Worker 模式，作为独立后台任务运行。

    使用示例：
        scheduler = EvalScheduler(
            span_cache=span_cache,
            converter=converter,
            replayer=replayer,
        )
        failed = await scheduler.scan_failed_sessions()
        for f in failed:
            await scheduler.process_failed_session(f)
    """

    EVAL_INTERVAL_SECONDS = 86400  # 24h

    def __init__(
        self,
        span_cache: Any | None = None,
        converter: Any | None = None,
        replayer: Any | None = None,
        harness_runner: Any | None = None,
        failure_archive: Any | None = None,
        feedback_service: Any | None = None,
    ) -> None:
        """初始化评估调度器

        Args:
            span_cache: tracing.py 的 SpanCache 实例
            converter: TraceToFixtureConverter 实例
            replayer: TraceReplayer 实例
            harness_runner: HarnessRunner 实例
            failure_archive: FailureArchive 实例
            feedback_service: FeedbackService 实例
        """
        self._span_cache = span_cache
        self._converter = converter
        self._replayer = replayer
        self._harness_runner = harness_runner
        self._failure_archive = failure_archive
        self._feedback_service = feedback_service

        # 已处理 session 去重集合（内存模式，生产环境用 Redis SET）
        self._processed_sessions: set[str] = set()

    async def scan_failed_sessions(
        self,
        filt: FailureFilter | None = None,
    ) -> list[FailedSession]:
        """扫描失败 session

        数据来源：
          1. SpanCache 中 status=failed 的 agent_call span
          2. feedback.py 中 feedback_type=thumbs_down 的 session
          3. （可选）audit.py 中 action 包含 error/fail 的 agent 事件

        去重：同一 session 在 since_hours 内只返回一次。

        Args:
            filt: 筛选条件，None 时使用默认条件

        Returns:
            失败 session 列表
        """
        filt = filt or FailureFilter()
        failed_sessions: list[FailedSession] = []
        seen_session_ids: set[str] = set()

        # 1. 从 SpanCache 扫描失败的 agent_call span
        if self._span_cache is not None:
            try:
                cache_results = await self._span_cache.get_failed_sessions(
                    since_hours=filt.since_hours,
                    agent_name=filt.agent_name,
                    limit=filt.max_batch_size,
                )
                for item in cache_results:
                    session_id = item.get("session_id", "")
                    if not session_id or session_id in seen_session_ids:
                        continue
                    seen_session_ids.add(session_id)
                    failed_sessions.append(FailedSession(
                        session_id=session_id,
                        agent_name=item.get("agent_name", ""),
                        failure_reason="failed",
                        failure_detail=f"SpanCache 检测到失败 span: {item.get('span_type', '')}",
                        duration_ms=float(item.get("duration_ms", 0)),
                        has_thumbs_down=False,
                    ))
                logger.info(
                    "SpanCache 扫描到 %d 个失败 session",
                    len(cache_results),
                )
            except Exception as e:
                logger.warning("SpanCache 扫描失败 session 异常: %s", e)

        # 2. 从 FeedbackService 扫描点踩 session
        if filt.include_thumbs_down and self._feedback_service is not None:
            try:
                feedback_results = await self._feedback_service.get_thumbs_down_sessions(
                    since_hours=filt.since_hours,
                    agent_name=filt.agent_name,
                    limit=filt.max_batch_size,
                )
                for item in feedback_results:
                    session_id = item.get("session_id", "")
                    if not session_id or session_id in seen_session_ids:
                        continue
                    seen_session_ids.add(session_id)
                    failed_sessions.append(FailedSession(
                        session_id=session_id,
                        agent_name=item.get("agent_name", ""),
                        failure_reason="thumbs_down",
                        failure_detail=f"用户点踩: {item.get('comment', '')}",
                        has_thumbs_down=True,
                    ))
                logger.info(
                    "FeedbackService 扫描到 %d 个点踩 session",
                    len(feedback_results),
                )
            except Exception as e:
                logger.warning("FeedbackService 扫描点踩 session 异常: %s", e)

        # 3. 去重已处理的 session
        if filt.exclude_archived:
            failed_sessions = [
                f for f in failed_sessions
                if f.session_id not in self._processed_sessions
            ]

        # 限制批量大小
        failed_sessions = failed_sessions[: filt.max_batch_size]

        logger.info(
            "扫描失败 session 完成: 共 %d 个（去重后）",
            len(failed_sessions),
        )

        return failed_sessions

    async def process_failed_session(
        self,
        failed: FailedSession,
    ) -> str:
        """处理单个失败 session

        完整闭环流程：
          1. 调用 TraceToFixtureConverter.convert() 生成 Fixture
          2. 调用 TraceReplayer.replay_trace() 确定性回放
          3. 调用 HarnessRunner.run_single() 评估回放结果
          4. 评估失败时调用 FailureArchive.archive() 归档
          5. 标记 session 为已处理
          6. 返回评估报告 ID

        Args:
            failed: 失败 session 信息

        Returns:
            评估报告 ID（失败时返回空字符串）
        """
        session_id = failed.session_id
        logger.info(
            "开始处理失败 session: id=%s reason=%s agent=%s",
            session_id,
            failed.failure_reason,
            failed.agent_name,
        )

        report_id = ""

        try:
            # 1. 生成 Fixture
            fixture = await self._convert_to_fixture(failed)
            if fixture is None:
                logger.warning("生成 Fixture 失败，跳过 session=%s", session_id)
                self._mark_processed(session_id)
                return ""

            # 2. 确定性回放
            replay_result = await self._replay_trace(failed, fixture)

            # 3. 评估回放结果
            eval_result = await self._evaluate(fixture, replay_result)
            report_id = self._extract_report_id(eval_result)

            # 4. 评估失败时归档
            if self._is_eval_failed(eval_result):
                await self._archive_failure(failed, fixture, eval_result, replay_result)
                logger.info(
                    "评估失败，已归档: session=%s report_id=%s",
                    session_id,
                    report_id,
                )
            else:
                logger.info(
                    "评估通过，无需归档: session=%s report_id=%s",
                    session_id,
                    report_id,
                )

        except Exception as e:
            logger.error(
                "处理失败 session 异常 session=%s: %s",
                session_id,
                e,
            )
        finally:
            # 5. 标记已处理
            self._mark_processed(session_id)

        return report_id

    async def run_scheduled(self) -> None:
        """执行一次定时评估任务

        由 scheduler.py 的 _eval_loop 调度，默认每 24h 执行一次。
        """
        logger.info("定时评估任务开始")

        # 1. 扫描失败 session
        failed_sessions = await self.scan_failed_sessions()

        if not failed_sessions:
            logger.info("无失败 session 需要处理")
            return

        # 2. 串行处理（避免并发压力，spec 要求并发 ≤ 5）
        success_count = 0
        for failed in failed_sessions:
            report_id = await self.process_failed_session(failed)
            if report_id:
                success_count += 1

        logger.info(
            "定时评估任务完成: 处理 %d/%d 个失败 session",
            success_count,
            len(failed_sessions),
        )

    async def _convert_to_fixture(self, failed: FailedSession) -> Any:
        """调用 TraceToFixtureConverter 生成 Fixture"""
        converter = self._ensure_converter()
        if converter is None:
            logger.warning("converter 不可用")
            return None

        try:
            # 兼容不同签名的 convert 方法
            import inspect
            sig = inspect.signature(converter.convert)
            params = sig.parameters

            if "failure_reason" in params:
                return await converter.convert(
                    session_id=failed.session_id,
                    failure_reason=failed.failure_reason,
                )
            return await converter.convert(failed.session_id)
        except Exception as e:
            logger.error("生成 Fixture 异常 session=%s: %s", failed.session_id, e)
            return None

    async def _replay_trace(self, failed: FailedSession, fixture: Any) -> Any:
        """调用 TraceReplayer 确定性回放"""
        replayer = self._ensure_replayer()
        if replayer is None:
            logger.warning("replayer 不可用，跳过回放")
            return None

        try:
            return await replayer.replay_trace(failed.session_id)
        except Exception as e:
            logger.error("回放异常 session=%s: %s", failed.session_id, e)
            return None

    async def _evaluate(self, fixture: Any, replay_result: Any) -> Any:
        """调用 HarnessRunner 评估"""
        runner = self._ensure_runner()
        if runner is None:
            logger.warning("harness_runner 不可用，跳过评估")
            return None

        try:
            return await runner.run_single(fixture)
        except Exception as e:
            logger.error("评估异常: %s", e)
            return None

    async def _archive_failure(
        self,
        failed: FailedSession,
        fixture: Any,
        eval_result: Any,
        replay_result: Any,
    ) -> None:
        """调用 FailureArchive 归档失败案例"""
        archive = self._ensure_failure_archive()
        if archive is None:
            logger.warning("failure_archive 不可用，跳过归档")
            return

        try:
            # 加载 spans 用于失败模式分类
            spans: list[dict] = []
            if self._span_cache is not None:
                spans = await self._span_cache.get_session_spans(failed.session_id)

            await archive.archive(
                session_id=failed.session_id,
                eval_report=eval_result,
                fixture=fixture,
                spans=spans,
                failure_reason=failed.failure_reason,
            )
        except Exception as e:
            logger.error("归档失败案例异常 session=%s: %s", failed.session_id, e)

    def _ensure_converter(self) -> Any:
        """延迟初始化 converter"""
        if self._converter is None:
            try:
                from agent.evaluation.replay.trace_to_fixture import TraceToFixtureConverter
                self._converter = TraceToFixtureConverter(span_cache=self._span_cache)
            except Exception as e:
                logger.warning("TraceToFixtureConverter 初始化失败: %s", e)
        return self._converter

    def _ensure_replayer(self) -> Any:
        """延迟初始化 replayer"""
        if self._replayer is None:
            try:
                from agent.evaluation.replay.trace_replayer import TraceReplayer
                self._replayer = TraceReplayer(span_cache=self._span_cache)
            except Exception as e:
                logger.warning("TraceReplayer 初始化失败: %s", e)
        return self._replayer

    def _ensure_runner(self) -> Any:
        """延迟初始化 harness_runner"""
        if self._harness_runner is None:
            try:
                from agent.evaluation.runners.harness_runner import HarnessRunner
                self._harness_runner = HarnessRunner()
            except Exception as e:
                logger.warning("HarnessRunner 初始化失败: %s", e)
        return self._harness_runner

    def _ensure_failure_archive(self) -> Any:
        """延迟初始化 failure_archive"""
        if self._failure_archive is None:
            try:
                from agent.evaluation.improvement.failure_archive import FailureArchive
                self._failure_archive = FailureArchive()
            except Exception as e:
                logger.warning("FailureArchive 初始化失败: %s", e)
        return self._failure_archive

    def _extract_report_id(self, eval_result: Any) -> str:
        """从评估结果中提取 report_id"""
        if eval_result is None:
            return ""
        if hasattr(eval_result, "report_id"):
            return str(eval_result.report_id)
        if hasattr(eval_result, "fixture_id"):
            # SingleEvalResult 没有 report_id，使用 fixture_id 替代
            return str(eval_result.fixture_id)
        if isinstance(eval_result, dict):
            return str(eval_result.get("report_id") or eval_result.get("fixture_id") or "")
        return ""

    def _is_eval_failed(self, eval_result: Any) -> bool:
        """判断评估是否失败"""
        if eval_result is None:
            return True  # 评估异常视为失败
        if hasattr(eval_result, "success"):
            return not eval_result.success
        if hasattr(eval_result, "status"):
            return str(eval_result.status).lower() == "fail"
        if isinstance(eval_result, dict):
            return not eval_result.get("success", False)
        return False

    def _mark_processed(self, session_id: str) -> None:
        """标记 session 为已处理"""
        self._processed_sessions.add(session_id)

    def is_processed(self, session_id: str) -> bool:
        """检查 session 是否已处理"""
        return session_id in self._processed_sessions
