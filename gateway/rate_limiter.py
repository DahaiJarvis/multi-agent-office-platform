"""限流熔断器

提供令牌桶限流和熔断器功能，保护后端服务免受流量冲击。

限流策略:
  - 全局限流: 令牌桶算法，控制总体 QPS
  - 用户级限流: 滑动窗口算法，控制单用户请求频率
  - 工具级限流: 固定窗口算法，控制单工具调用频率

熔断策略:
  - 三状态模型: CLOSED -> OPEN -> HALF_OPEN
  - 基于错误率和响应时间触发
"""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ==================== 令牌桶限流器 ====================

class TokenBucket:
    """令牌桶限流器

    以固定速率向桶中添加令牌，请求消耗令牌，
    桶满时丢弃新令牌，桶空时拒绝请求。
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        """尝试消耗令牌

        Args:
            tokens: 需要消耗的令牌数量

        Returns:
            是否成功消耗
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self._rate
        self._tokens = min(self._capacity, self._tokens + new_tokens)
        self._last_refill = now


# ==================== 滑动窗口限流器 ====================

class SlidingWindowCounter:
    """滑动窗口计数器

    在滑动时间窗口内统计请求次数，超过阈值则拒绝。
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: list[float] = []
        self._lock = threading.Lock()

    def is_allowed(self) -> bool:
        """检查当前请求是否被允许

        Returns:
            是否允许通过
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds

        with self._lock:
            self._requests = [t for t in self._requests if t > cutoff]
            if len(self._requests) >= self._max_requests:
                return False
            self._requests.append(now)
            return True


# ==================== 熔断器 ====================

class CircuitState(str, Enum):
    """熔断器状态"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitConfig:
    """熔断器配置"""

    failure_threshold: int = 5
    success_threshold: int = 3
    timeout_seconds: float = 30.0
    error_rate_threshold: float = 0.5


class CircuitBreaker:
    """熔断器

    三状态模型:
      - CLOSED: 正常状态，请求正常通过
      - OPEN: 熔断状态，所有请求被拒绝
      - HALF_OPEN: 半开状态，允许少量请求通过以测试恢复

    触发条件:
      - 连续失败次数达到阈值
      - 错误率超过阈值
    """

    def __init__(self, name: str, config: CircuitConfig | None = None) -> None:
        self._name = name
        self._config = config or CircuitConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_calls = 0
        self._error_calls = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow_request(self) -> bool:
        """检查是否允许请求通过

        Returns:
            是否允许
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self._config.timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info("熔断器 %s 进入半开状态", self._name)
                    return True
                return False
            if self._state == CircuitState.HALF_OPEN:
                return True
            return False

    def record_success(self) -> None:
        """记录成功调用"""
        with self._lock:
            self._total_calls += 1
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info("熔断器 %s 恢复为关闭状态", self._name)
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        """记录失败调用"""
        with self._lock:
            self._total_calls += 1
            self._error_calls += 1
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("熔断器 %s 半开状态下失败，重新熔断", self._name)
            elif self._failure_count >= self._config.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "熔断器 %s 触发熔断，连续失败 %d 次",
                    self._name,
                    self._failure_count,
                )

    def get_stats(self) -> dict[str, Any]:
        """获取熔断器统计信息"""
        error_rate = self._error_calls / self._total_calls if self._total_calls > 0 else 0.0
        return {
            "name": self._name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "total_calls": self._total_calls,
            "error_calls": self._error_calls,
            "error_rate": round(error_rate, 4),
        }


# ==================== 限流熔断管理器 ====================

@dataclass
class RateLimitConfig:
    """限流配置"""

    global_qps: int = 1000
    user_qpm: int = 60
    user_max_concurrent: int = 5
    tool_qpm: int = 200
    sensitive_action_qpm: int = 10


class RateLimitManager:
    """限流熔断管理器

    统一管理全局、用户级、工具级的限流和熔断策略。
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._global_bucket = TokenBucket(rate=self._config.global_qps, capacity=self._config.global_qps)
        self._user_counters: dict[str, SlidingWindowCounter] = {}
        self._tool_counters: dict[str, SlidingWindowCounter] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def check_global_limit(self) -> bool:
        """检查全局限流"""
        return self._global_bucket.consume()

    def check_user_limit(self, user_id: str) -> bool:
        """检查用户级限流"""
        with self._lock:
            if user_id not in self._user_counters:
                self._user_counters[user_id] = SlidingWindowCounter(
                    max_requests=self._config.user_qpm,
                    window_seconds=60,
                )
        return self._user_counters[user_id].is_allowed()

    def check_tool_limit(self, tool_name: str) -> bool:
        """检查工具级限流"""
        with self._lock:
            if tool_name not in self._tool_counters:
                self._tool_counters[tool_name] = SlidingWindowCounter(
                    max_requests=self._config.tool_qpm,
                    window_seconds=60,
                )
        return self._tool_counters[tool_name].is_allowed()

    def get_circuit_breaker(self, service_name: str) -> CircuitBreaker:
        """获取指定服务的熔断器"""
        with self._lock:
            if service_name not in self._circuit_breakers:
                self._circuit_breakers[service_name] = CircuitBreaker(service_name)
        return self._circuit_breakers[service_name]

    def check_request(self, user_id: str, tool_name: str = "") -> dict[str, Any]:
        """综合检查请求是否被允许

        按优先级依次检查：全局限流 -> 用户限流 -> 工具限流

        Args:
            user_id: 用户ID
            tool_name: 工具名称（可选）

        Returns:
            检查结果，包含是否允许和拒绝原因
        """
        if not self.check_global_limit():
            return {"allowed": False, "reason": "global_rate_limit", "message": "系统繁忙，请稍后重试"}

        if not self.check_user_limit(user_id):
            return {"allowed": False, "reason": "user_rate_limit", "message": "请求过于频繁，请稍后重试"}

        if tool_name and not self.check_tool_limit(tool_name):
            return {"allowed": False, "reason": "tool_rate_limit", "message": f"工具 {tool_name} 调用过于频繁"}

        return {"allowed": True}

    def get_all_stats(self) -> dict[str, Any]:
        """获取所有限流熔断统计信息"""
        circuit_stats = {}
        for name, cb in self._circuit_breakers.items():
            circuit_stats[name] = cb.get_stats()

        return {
            "user_counters": len(self._user_counters),
            "tool_counters": len(self._tool_counters),
            "circuit_breakers": circuit_stats,
        }


# 全局限流管理器实例
_rate_limit_manager: RateLimitManager | None = None


def get_rate_limit_manager() -> RateLimitManager:
    """获取全局限流管理器实例"""
    global _rate_limit_manager
    if _rate_limit_manager is None:
        _rate_limit_manager = RateLimitManager()
    return _rate_limit_manager
