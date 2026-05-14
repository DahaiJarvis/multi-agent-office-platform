"""熔断器（Circuit Breaker）

================================================================================
模块职责
================================================================================
保护系统免受级联故障，当下游服务连续失败时自动熔断，
避免无效请求继续消耗资源。

================================================================================
熔断器状态机
================================================================================
三态模型：
  -------------------------------------------------------------------------
  CLOSED（关闭）：
    - 正常状态，请求正常通过
    - 连续失败次数达到 failure_threshold 时转换为 OPEN

  OPEN（打开）：
    - 熔断状态，请求被直接拒绝
    - 等待 recovery_timeout 后转换为 HALF_OPEN

  HALF_OPEN（半开）：
    - 探测状态，允许少量请求探测下游服务是否恢复
    - 连续成功次数达到 success_threshold 时转换为 CLOSED
    - 任意失败立即转换为 OPEN
  -------------------------------------------------------------------------

================================================================================
使用场景
================================================================================
- MCP 服务调用保护（知识库检索、邮件发送等）
- 外部 API 调用保护
- 数据库连接保护

================================================================================
与其他模块的关系
================================================================================
- execution_controller.py: 执行任务前检查熔断器状态
- mcp_integration.py: MCP 服务调用时使用熔断器保护
- ha_manager.py: 高可用管理器监控熔断器状态

================================================================================
使用示例
================================================================================
    from agent.core.circuit_breaker import CircuitBreaker, get_circuit_breaker

    # 获取熔断器
    cb = get_circuit_breaker("mcp_knowledge")

    # 方式 1：使用 call() 方法包装调用
    result = await cb.call(call_downstream_service, arg1, arg2)

    # 方式 2：手动记录成功/失败
    try:
        result = await call_downstream_service()
        await cb.record_success()
    except Exception:
        await cb.record_failure()
        raise
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """熔断器状态枚举

    定义熔断器的三种状态。

    状态说明：
    -------------------------------------------------------------------------
    CLOSED: 关闭状态
      - 正常工作状态，所有请求正常通过
      - 记录失败次数，达到阈值时转换为 OPEN

    OPEN: 打开状态
      - 熔断状态，所有请求被直接拒绝
      - 等待 recovery_timeout 后转换为 HALF_OPEN

    HALF_OPEN: 半开状态
      - 探测状态，允许有限数量的请求通过
      - 用于探测下游服务是否已恢复
    -------------------------------------------------------------------------
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConfig(BaseModel):
    """熔断器配置

    配置参数说明：
    -------------------------------------------------------------------------
    failure_threshold: 连续失败阈值
      - 连续失败次数达到此值时熔断
      - 默认值：5 次

    recovery_timeout: 恢复等待时间（秒）
      - 熔断后等待此时间后进入 HALF_OPEN 状态
      - 默认值：30 秒

    half_open_max_calls: 半开状态最大探测数
      - HALF_OPEN 状态下允许的最大请求数
      - 默认值：3 次

    success_threshold: 半开状态恢复阈值
      - HALF_OPEN 状态下连续成功次数达到此值时恢复为 CLOSED
      - 默认值：3 次

    timeout: 单次请求超时时间（秒）
      - 0 表示不限制
      - 默认值：0
    -------------------------------------------------------------------------

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
    """熔断器统计信息

    用于监控和告警，包含熔断器的运行状态和统计数据。

    Attributes:
        state: 当前状态
        failure_count: 当前连续失败次数
        success_count: 当前连续成功次数
        total_calls: 总调用次数
        total_failures: 总失败次数
        total_rejections: 总拒绝次数（熔断时）
        last_failure_time: 最后一次失败时间
        last_state_change_time: 最后一次状态变更时间
        half_open_calls: 半开状态下的探测次数
    """

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
    """熔断器打开异常

    当熔断器处于 OPEN 状态时，请求被拒绝时抛出此异常。

    Attributes:
        name: 熔断器名称
        recovery_timeout: 预计恢复等待时间
    """

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

    核心方法：
    -------------------------------------------------------------------------
    call(fn, *args, **kwargs): 通过熔断器调用异步函数
      - 自动判断是否允许请求
      - 自动记录成功/失败
      - 支持超时控制

    record_success(): 手动记录成功
    record_failure(): 手动记录失败
    reset(): 手动重置为 CLOSED 状态
    trip(): 手动触发熔断
    get_stats(): 获取统计信息
    -------------------------------------------------------------------------

    使用示例：
        cb = CircuitBreaker("mcp_knowledge", CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=30.0,
        ))

        # 方式 1：使用 call() 方法
        result = await cb.call(fetch_data, "query")

        # 方式 2：手动控制
        try:
            result = await fetch_data("query")
            await cb.record_success()
        except Exception:
            await cb.record_failure()
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

        如果熔断器处于 OPEN 状态且已超过 recovery_timeout，
        自动转换为 HALF_OPEN 状态。

        状态转换需要加锁保护，避免并发问题。

        Returns:
            当前熔断器状态
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
        """获取熔断器统计信息

        用于监控和告警。

        Returns:
            CircuitBreakerStats 包含状态和统计数据
        """
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
        """判断是否允许请求通过

        内部方法，根据当前状态判断是否允许请求。

        判断逻辑：
        - CLOSED: 允许
        - OPEN: 拒绝
        - HALF_OPEN: 限制数量允许

        Returns:
            True: 允许请求
            False: 拒绝请求
        """
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

        这是熔断器的核心方法，封装了完整的熔断逻辑：
          1. 检查熔断器状态
          2. 判断是否允许请求
          3. 执行请求（支持超时）
          4. 记录成功/失败

        Args:
            fn: 异步函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数执行结果

        Raises:
            CircuitOpenError: 熔断器打开时
            Exception: 函数执行失败时

        使用示例：
            result = await cb.call(fetch_data, "query", timeout=10)
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
        """成功回调

        内部方法，请求成功时调用。

        状态转换逻辑：
        - HALF_OPEN: 增加成功计数，达到阈值时恢复为 CLOSED
        - CLOSED: 重置失败计数
        """
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self) -> None:
        """失败回调

        内部方法，请求失败时调用。

        状态转换逻辑：
        - HALF_OPEN: 立即转换为 OPEN
        - CLOSED: 增加失败计数，达到阈值时转换为 OPEN
        """
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
        """状态转换

        内部方法，执行状态转换并重置相关计数器。

        Args:
            new_state: 目标状态
        """
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

        使用场景：
        - 手动控制熔断器状态
        - 与其他组件集成时记录结果
        """
        await self._on_success()

    async def record_failure(self) -> None:
        """公开方法：记录一次失败（线程安全）

        供外部调用方在不通过 call() 方法的情况下记录失败，
        内部自动加锁保护状态变更。

        使用场景：
        - 手动控制熔断器状态
        - 与其他组件集成时记录结果
        """
        await self._on_failure()

    async def reset(self) -> None:
        """手动重置熔断器到 CLOSED 状态

        用于运维干预，强制恢复熔断器。

        使用场景：
        - 下游服务已确认恢复
        - 紧急情况下强制恢复服务
        """
        async with self._lock:
            self._transition_to(CircuitState.CLOSED)

    async def trip(self) -> None:
        """手动触发熔断

        用于运维干预，强制熔断。

        使用场景：
        - 下游服务已确认故障
        - 紧急情况下保护系统
        """
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
