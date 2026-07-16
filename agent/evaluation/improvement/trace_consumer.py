"""失败 Trace 消费器

对应 spec 05 第 4.7 节 FailureTraceConsumer。

从 04 号 spec 的失败队列消费失败 Trace，标准化为失败案例后
交给 FailurePatternClassifier 分类。

失败来源：
  1. 04 号 spec 的 eval_scheduler 产出的失败 Trace
  2. feedback.py 收到的点踩反馈对应的 session
  3. tracing.py 中 status=failed 的 Span
"""

import logging
from typing import Any

from agent.evaluation.improvement.failure_pattern import (
    ClassificationResult,
    FailurePattern,
    FailurePatternClassifier,
)

logger = logging.getLogger(__name__)


class FailureTraceConsumer:
    """失败 Trace 消费器

    从 04 号 spec 的失败队列消费失败 Trace，标准化为失败案例后
    交给 FailurePatternClassifier 分类。

    失败来源：
      1. 04 号 spec 的 eval_scheduler 产出的失败 Trace
      2. feedback.py 收到的点踩反馈对应的 session
      3. tracing.py 中 status=failed 的 Span

    使用示例：
        classifier = FailurePatternClassifier()
        consumer = FailureTraceConsumer(classifier)
        result = await consumer.consume({
            "trace_id": "session-xxx",
            "failure_reason": "注入攻击",
            "spans": [...],
        })
        if result and result.pattern == FailurePattern.UNKNOWN:
            # 转人工分析
            ...
    """

    def __init__(self, classifier: FailurePatternClassifier) -> None:
        """初始化失败 Trace 消费器

        Args:
            classifier: 失败模式分类器
        """
        self._classifier = classifier

    async def consume(
        self,
        failure_event: dict[str, Any],
    ) -> ClassificationResult | None:
        """消费一个失败事件

        Args:
            failure_event: 失败事件，包含 trace_id / session_id / failure_reason / spans

        Returns:
            分类结果，消费失败时返回 None
        """
        trace_id = failure_event.get("trace_id") or failure_event.get("session_id", "")
        failure_reason = failure_event.get("failure_reason", "")
        spans = failure_event.get("spans", [])

        if not spans:
            logger.warning("失败事件无 spans，跳过: trace_id=%s", trace_id)
            return None

        logger.info(
            "消费失败事件: trace_id=%s reason=%s spans=%d",
            trace_id, failure_reason[:100], len(spans),
        )

        try:
            # 分类失败模式
            result = await self._classifier.classify_detailed(spans, failure_reason)

            logger.info(
                "分类结果: trace_id=%s pattern=%s confidence=%.2f",
                trace_id, result.pattern.value, result.confidence,
            )

            # unknown 模式标记转人工
            if result.pattern == FailurePattern.UNKNOWN:
                logger.info("失败模式未识别，转人工分析: trace_id=%s", trace_id)

            return result

        except Exception as e:
            logger.error("消费失败事件异常: trace_id=%s error=%s", trace_id, e)
            return None

    async def consume_from_feedback(self, session_id: str) -> ClassificationResult | None:
        """从用户负反馈触发消费

        从 feedback.py 收到的点踩反馈对应的 session 提取失败 Trace 并分类。

        Args:
            session_id: 会话 ID

        Returns:
            分类结果，消费失败时返回 None
        """
        logger.info("从用户负反馈触发消费: session_id=%s", session_id)

        try:
            # 从 SpanCache 获取 session 的 spans
            spans = await self._get_session_spans(session_id)
            if not spans:
                logger.warning("从 SpanCache 获取 spans 为空: session_id=%s", session_id)
                return None

            # 构造失败事件
            failure_event = {
                "trace_id": session_id,
                "session_id": session_id,
                "failure_reason": "用户点踩反馈",
                "spans": spans,
            }

            return await self.consume(failure_event)

        except Exception as e:
            logger.error("从负反馈消费失败: session_id=%s error=%s", session_id, e)
            return None

    async def consume_batch(
        self,
        failure_events: list[dict[str, Any]],
    ) -> list[ClassificationResult]:
        """批量消费失败事件

        Args:
            failure_events: 失败事件列表

        Returns:
            分类结果列表（消费失败的事件不包含在结果中）
        """
        results: list[ClassificationResult] = []
        for event in failure_events:
            result = await self.consume(event)
            if result is not None:
                results.append(result)
        return results

    async def _get_session_spans(self, session_id: str) -> list[dict[str, Any]]:
        """从 SpanCache 获取 session 的 spans

        Args:
            session_id: 会话 ID

        Returns:
            span 列表
        """
        try:
            from observability.tracing import SpanCache

            cache = SpanCache()
            spans = await cache.get_session_spans(session_id)
            return spans or []
        except Exception as e:
            logger.warning("获取 session spans 失败: session_id=%s error=%s", session_id, e)
            return []

    def should_route_to_human(self, result: ClassificationResult) -> bool:
        """判断是否需要转人工分析

        confidence < 0.7 或 pattern=unknown 时转人工。

        Args:
            result: 分类结果

        Returns:
            是否需要转人工
        """
        return result.pattern == FailurePattern.UNKNOWN or result.confidence < 0.7
