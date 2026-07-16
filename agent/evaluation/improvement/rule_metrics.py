"""规则效果监控器

对应 spec 05 第 4.6 节 RuleMetricsCollector。

实时采集规则命中与误报数据，误报率回升时触发告警。

告警规则：
  - 上线后 7 天内日均误报率 > 5% -> 触发回滚评审
  - 单日误报数 > 50 -> 立即告警
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from agent.evaluation.improvement.models import RuleMetrics

logger = logging.getLogger(__name__)


class RuleMetricsCollector:
    """规则效果监控器

    实时采集规则命中与误报数据，误报率回升时触发告警。

    告警规则：
      - 上线后 7 天内日均误报率 > 5% -> 触发回滚评审
      - 单日误报数 > 50 -> 立即告警

    降级策略：PostgreSQL 不可用时使用内存存储。
    """

    # 告警阈值
    DAILY_FP_ALERT_THRESHOLD = 50       # 单日误报数告警阈值
    WEEKLY_AVG_FP_RATE_THRESHOLD = 0.05  # 7 天日均误报率告警阈值

    def __init__(self) -> None:
        """初始化规则效果监控器"""
        # 内存存储（降级方案）：rule_id -> [(timestamp, is_false_positive), ...]
        self._events: dict[str, list[tuple[float, bool]]] = {}
        # 按天聚合的指标：rule_id -> {date_str -> {hit_count, fp_count, last_hit_at}}
        self._daily_metrics: dict[str, dict[str, dict[str, Any]]] = {}

    async def record_hit(
        self,
        rule_id: str,
        is_false_positive: bool = False,
    ) -> None:
        """记录规则命中事件

        Args:
            rule_id: 规则 ID
            is_false_positive: 是否为误报
        """
        now = time.time()

        # 记录事件
        self._events.setdefault(rule_id, []).append((now, is_false_positive))

        # 更新按天聚合指标
        date_str = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if rule_id not in self._daily_metrics:
            self._daily_metrics[rule_id] = {}
        if date_str not in self._daily_metrics[rule_id]:
            self._daily_metrics[rule_id][date_str] = {
                "hit_count": 0,
                "false_positive_count": 0,
                "last_hit_at": 0.0,
            }

        daily = self._daily_metrics[rule_id][date_str]
        daily["hit_count"] += 1
        if is_false_positive:
            daily["false_positive_count"] += 1
        daily["last_hit_at"] = now

        logger.debug(
            "记录规则命中: rule_id=%s is_fp=%s daily_hits=%d daily_fps=%d",
            rule_id, is_false_positive, daily["hit_count"], daily["false_positive_count"],
        )

        # 检查是否需要立即告警
        if is_false_positive and daily["false_positive_count"] > self.DAILY_FP_ALERT_THRESHOLD:
            logger.warning(
                "规则单日误报数超阈值: rule_id=%s fp_count=%d threshold=%d",
                rule_id, daily["false_positive_count"], self.DAILY_FP_ALERT_THRESHOLD,
            )

    async def get_metrics(
        self,
        rule_id: str,
        window_days: int = 7,
    ) -> RuleMetrics:
        """获取规则效果指标

        Args:
            rule_id: 规则 ID
            window_days: 统计窗口（天）

        Returns:
            规则效果指标
        """
        total_hits = 0
        total_fps = 0
        last_hit_at = 0.0

        # 计算窗口范围内的日期
        now = datetime.now()
        window_dates = [
            (now - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(window_days)
        ]

        daily_data = self._daily_metrics.get(rule_id, {})
        for date_str in window_dates:
            daily = daily_data.get(date_str)
            if daily:
                total_hits += daily.get("hit_count", 0)
                total_fps += daily.get("false_positive_count", 0)
                if daily.get("last_hit_at", 0) > last_hit_at:
                    last_hit_at = daily["last_hit_at"]

        # 计算误报率
        false_positive_rate = total_fps / total_hits if total_hits > 0 else 0.0

        return RuleMetrics(
            rule_id=rule_id,
            hit_count=total_hits,
            false_positive_count=total_fps,
            false_positive_rate=round(false_positive_rate, 4),
            last_hit_at=last_hit_at,
        )

    async def check_rollback_needed(self, rule_id: str) -> tuple[bool, str]:
        """检查是否需要触发回滚

        告警条件：
          1. 7 天日均误报率 > 5%
          2. 单日误报数 > 50

        Args:
            rule_id: 规则 ID

        Returns:
            (是否需要回滚, 原因)
        """
        # 检查单日误报数
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        daily_data = self._daily_metrics.get(rule_id, {})
        today = daily_data.get(today_str)

        if today and today.get("false_positive_count", 0) > self.DAILY_FP_ALERT_THRESHOLD:
            return (
                True,
                f"单日误报数 {today['false_positive_count']} 超过阈值 {self.DAILY_FP_ALERT_THRESHOLD}",
            )

        # 检查 7 天日均误报率
        metrics = await self.get_metrics(rule_id, window_days=7)
        if (
            metrics.hit_count > 0
            and metrics.false_positive_rate > self.WEEKLY_AVG_FP_RATE_THRESHOLD
        ):
            return (
                True,
                f"7 天日均误报率 {metrics.false_positive_rate:.2%} 超过阈值 {self.WEEKLY_AVG_FP_RATE_THRESHOLD:.2%}",
            )

        return (False, "")

    async def get_all_metrics(self, window_days: int = 7) -> dict[str, RuleMetrics]:
        """获取所有规则的指标

        Args:
            window_days: 统计窗口（天）

        Returns:
            rule_id -> RuleMetrics 的映射
        """
        result: dict[str, RuleMetrics] = {}
        for rule_id in self._daily_metrics:
            result[rule_id] = await self.get_metrics(rule_id, window_days)
        return result
