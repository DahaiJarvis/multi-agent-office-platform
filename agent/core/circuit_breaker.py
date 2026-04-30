"""熔断器（Circuit Breaker）

保护系统免受级联故障，当下游服务连续失败时自动熔断，
避免无效请求继续消耗资源。

状态机：
  CLOSED  -> 正常状态，请求正常通过
  OPEN    -> 熔断状态，请求被直接拒绝
  HALF_OPEN -> 半开状态，允许少量请求探测恢复

使用方式：
    from agent.core.circuit_breaker import CircuitBreaker, get_circuit_breaker

    cb = get_circuit_breaker("mcp_knowledge")
    async with cb:
        result = await call_downstream_service()
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """熔断器状态"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConfig(BaseModel):
    """熔断器配置

    Attributes:
        failure_threshold: 连续失败次数达到此值时熔断
        recovery_timeout: 熔断后等待恢复的时间(秒)
        half_open_max_calls: 半开状态允许的最大探测请求数
        success_threshold: 半开状态连续成功次数达到此值时恢复
        timeout: 单次请求超时时间(秒)，0 表示不限制
    """

    failure_threshold: int = Field(default=5, ge=1, description="连续失败阈值")
    recovery_timeout: float = Field(default=30.0, ge=1.0, description="恢复等待时间(秒)")
    half_open_max_calls: int = Field(default=3, ge=1, description="半开状态最大探测数")
    success_threshold: int = Field(default=3, ge=1, description="半开状态恢复阈值")
    timeout: float = Field(default=0.0, ge=0.0, description="请求超时(秒)")


class CircuitBreakerStats(BaseModel):
    """熔断器统计信息"""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    total_calls: int = 0
    total_failures: int = 0
    total_rejections: int = 0
    last_failure_time: float = 0
    last_state_change_time: float = Field(default_factory=time.time)
    half_open_calls: int = 0


class CircuitOpenError(Exception):
    """熔断器打开异常"""

    def __init__(self, name: str, recovery_timeout: float) -> None:
        self.name = name
        self.recovery_timeout = recovery_timeout
        super().__init__(
            f"熔断器 [{name}] 已打开，预计 {recovery_timeout:.0f}s 后恢复"
        )


class CircuitBreaker:
    """熔断器

    基于连续失败计数的熔断器实现，支持三态转换：
    CLOSED -> OPEN -> HALF_OPEN -> CLOSED

    线程安全：使用 asyncio.Lock 保护状态变更。
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._total_calls = 0
        self._total_failures = 0
        self._total_rejections = 0
        self._last_state_change_time = time.time()
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        """获取当前状态（只读，不做状态转换）"""
        return self._state

    async def check_state(self) -> CircuitState:
        """检查当前状态，自动处理 OPEN -> HALF_OPEN 转换

        状态转换需要加锁保护，避免并发问题。
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self._config.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_calls = 0
                    self._success_count = 0
            return self._state

    @property
    def config(self) -> CircuitBreakerConfig:
        return self._config

    def get_stats(self) -> CircuitBreakerStats:
        """获取熔断器统计信息"""
        return CircuitBreakerStats(
            state=self._state,
            failure_count=self._failure_count,
            success_count=self._success_count,
            total_calls=self._total_calls,
            total_failures=self._total_failures,
            total_rejections=self._total_rejections,
            last_failure_time=self._last_failure_time,
            last_state_change_time=self._last_state_change_time,
            half_open_calls=self._half_open_calls,
        )

    async def _allow_request(self) -> bool:
        """判断是否允许请求通过"""
        current_state = await self.check_state()

        if current_state == CircuitState.CLOSED:
            return True

        if current_state == CircuitState.OPEN:
            self._total_rejections += 1
            return False

        if current_state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self._config.half_open_max_calls:
                self._half_open_calls += 1
                return True
            self._total_rejections += 1
            return False

        return False

    async def call(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """通过熔断器调用异步函数

        Args:
            fn: 异步函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数执行结果

        Raises:
            CircuitOpenError: 熔断器打开时
        """
        async with self._lock:
            allowed = await self._allow_request()
            if not allowed:
                raise CircuitOpenError(self._name, self._config.recovery_timeout)

            self._total_calls += 1

        try:
            if self._config.timeout > 0:
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=self._config.timeout)
            else:
                result = await fn(*args, **kwargs)

            await self._on_success()
            return result

        except Exception as e:
            await self._on_failure()
            raise

    async def _on_success(self) -> None:
        """成功回调"""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self) -> None:
        """失败回调"""
        async with self._lock:
            self._total_failures += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换"""
        old_state = self._state
        self._state = new_state
        self._last_state_change_time = time.time()

        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
        elif new_state == CircuitState.OPEN:
            self._half_open_calls = 0
            self._success_count = 0

        logger.info(
            "熔断器 [%s] %s -> %s (failures=%d, total=%d)",
            self._name, old_state.value, new_state.value,
            self._failure_count, self._total_calls,
        )

    async def record_success(self) -> None:
        """公开方法：记录一次成功（线程安全）

        供外部调用方在不通过 call() 方法的情况下记录成功，
        内部自动加锁保护状态变更。
        """
        await self._on_success()

    async def record_failure(self) -> None:
        """公开方法：记录一次失败（线程安全）

        供外部调用方在不通过 call() 方法的情况下记录失败，
        内部自动加锁保护状态变更。
        """
        await self._on_failure()

    async def reset(self) -> None:
        """手动重置熔断器到 CLOSED 状态"""
        async with self._lock:
            self._transition_to(CircuitState.CLOSED)

    async def trip(self) -> None:
        """手动触发熔断"""
        async with self._lock:
            self._last_failure_time = time.time()
            self._transition_to(CircuitState.OPEN)


# ==================== 全局熔断器管理 ====================

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    config: CircuitBreakerConfig | None = None,
) -> CircuitBreaker:
    """获取或创建命名熔断器

    Args:
        name: 熔断器名称（通常为服务名）
        config: 配置，首次创建时使用

    Returns:
        CircuitBreaker 实例
    """
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name=name, config=config)
    return _breakers[name]


def list_circuit_breakers() -> dict[str, CircuitBreakerStats]:
    """列出所有熔断器状态"""
    return {name: cb.get_stats() for name, cb in _breakers.items()}


async def reset_all_circuit_breakers() -> None:
    """重置所有熔断器"""
    for cb in _breakers.values():
        await cb.reset()
