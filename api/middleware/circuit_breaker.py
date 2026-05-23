"""熔断器中间件

从 gateway/rate_limiter.py 提取的熔断器实现，
用于 API 层面的服务熔断保护，防止级联故障。

三状态模型:
  - CLOSED: 正常状态，请求正常通过
  - OPEN: 熔断状态，所有请求被拒绝
  - HALF_OPEN: 半开状态，允许少量请求通过以测试恢复

触发条件:
  - 连续失败次数达到阈值
  - 错误率超过阈值
"""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


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

    def reset(self) -> None:
        """重置熔断器状态"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._total_calls = 0
            self._error_calls = 0
            self._last_failure_time = 0.0
            logger.info("熔断器 %s 已重置", self._name)


class CircuitBreakerManager:
    """熔断器管理器

    统一管理多个服务的熔断器实例。
    """

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_breaker(self, service_name: str, config: CircuitConfig | None = None) -> CircuitBreaker:
        """获取指定服务的熔断器

        Args:
            service_name: 服务名称
            config: 熔断器配置（仅首次创建时生效）

        Returns:
            CircuitBreaker 实例
        """
        with self._lock:
            if service_name not in self._breakers:
                self._breakers[service_name] = CircuitBreaker(service_name, config)
        return self._breakers[service_name]

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """获取所有熔断器统计信息"""
        return {name: cb.get_stats() for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        """重置所有熔断器"""
        for cb in self._breakers.values():
            cb.reset()


_circuit_breaker_manager: CircuitBreakerManager | None = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """获取全局熔断器管理器"""
    global _circuit_breaker_manager
    if _circuit_breaker_manager is None:
        _circuit_breaker_manager = CircuitBreakerManager()
    return _circuit_breaker_manager
