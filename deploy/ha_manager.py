"""高可用与灾备模块

提供健康检查增强、故障转移、服务降级等高可用能力。

核心能力:
  - 增强健康检查: 深度检查各组件状态（Redis/PG/MCP/LLM）
  - 故障转移: 自动检测故障并切换到备用实例
  - 服务降级: 多级降级策略，保障核心功能可用
  - 灾备指标: RTO/RPO 监控
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """健康状态"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """组件健康状态"""

    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    latency_ms: float = 0.0
    error: str = ""
    last_check: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """增强健康检查器

    深度检查各组件状态，包括连通性、响应时间、功能可用性。
    """

    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}
        self._check_interval = 30.0
        self._running = False

    async def check_redis(self) -> ComponentHealth:
        """检查 Redis 连通性"""
        health = ComponentHealth(name="redis", last_check=time.time())
        start = time.monotonic()
        try:
            from agent.core.config import get_settings
            import redis.asyncio as aioredis

            settings = get_settings()
            client = aioredis.from_url(settings.redis_url)
            await client.ping()
            await client.close()
            health.latency_ms = (time.monotonic() - start) * 1000
            health.status = HealthStatus.HEALTHY
        except ImportError:
            health.status = HealthStatus.DEGRADED
            health.error = "redis 库未安装"
        except Exception as e:
            health.status = HealthStatus.UNHEALTHY
            health.error = str(e)
            health.latency_ms = (time.monotonic() - start) * 1000

        self._components["redis"] = health
        return health

    async def check_postgres(self) -> ComponentHealth:
        """检查 PostgreSQL 连通性"""
        health = ComponentHealth(name="postgres", last_check=time.time())
        start = time.monotonic()
        try:
            from agent.core.config import get_settings
            import asyncpg

            settings = get_settings()
            conn = await asyncpg.connect(settings.postgres_dsn)
            await conn.execute("SELECT 1")
            await conn.close()
            health.latency_ms = (time.monotonic() - start) * 1000
            health.status = HealthStatus.HEALTHY
        except ImportError:
            health.status = HealthStatus.DEGRADED
            health.error = "asyncpg 库未安装"
        except Exception as e:
            health.status = HealthStatus.UNHEALTHY
            health.error = str(e)
            health.latency_ms = (time.monotonic() - start) * 1000

        self._components["postgres"] = health
        return health

    async def check_mcp_registry(self) -> ComponentHealth:
        """检查 MCP 注册中心"""
        health = ComponentHealth(name="mcp_registry", last_check=time.time())
        start = time.monotonic()
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get("http://localhost:9099/health")
                health.latency_ms = (time.monotonic() - start) * 1000
                if response.status_code == 200:
                    health.status = HealthStatus.HEALTHY
                    data = response.json()
                    health.metadata["registered_services"] = data.get("registered_services", 0)
                else:
                    health.status = HealthStatus.UNHEALTHY
                    health.error = f"HTTP {response.status_code}"
        except Exception as e:
            health.status = HealthStatus.UNHEALTHY
            health.error = str(e)
            health.latency_ms = (time.monotonic() - start) * 1000

        self._components["mcp_registry"] = health
        return health

    async def check_llm(self) -> ComponentHealth:
        """检查 LLM 服务可用性"""
        health = ComponentHealth(name="llm", last_check=time.time())
        start = time.monotonic()
        try:
            from agent.core.model_client import get_lightweight_client

            client = get_lightweight_client()
            response = await client.create(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            health.latency_ms = (time.monotonic() - start) * 1000
            if response.choices:
                health.status = HealthStatus.HEALTHY
            else:
                health.status = HealthStatus.DEGRADED
                health.error = "LLM 返回空响应"
        except Exception as e:
            health.status = HealthStatus.UNHEALTHY
            health.error = str(e)
            health.latency_ms = (time.monotonic() - start) * 1000

        self._components["llm"] = health
        return health

    async def check_ida(self) -> ComponentHealth:
        """检查 IDA（智能文档助手）服务可用性"""
        health = ComponentHealth(name="ida", last_check=time.time())
        start = time.monotonic()
        try:
            import httpx
            from agent.core.config import get_settings

            settings = get_settings()
            ida_base_url = getattr(settings, "ida_base_url", "http://localhost:3001")
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{ida_base_url}/api/v1/health")
                health.latency_ms = (time.monotonic() - start) * 1000
                if response.status_code == 200:
                    health.status = HealthStatus.HEALTHY
                else:
                    health.status = HealthStatus.DEGRADED
                    health.error = f"HTTP {response.status_code}"
        except Exception as e:
            health.status = HealthStatus.UNHEALTHY
            health.error = str(e)
            health.latency_ms = (time.monotonic() - start) * 1000

        self._components["ida"] = health
        return health

    async def full_check(self) -> dict[str, Any]:
        """执行全量健康检查"""
        checks = await asyncio.gather(
            self.check_redis(),
            self.check_postgres(),
            self.check_mcp_registry(),
            self.check_llm(),
            self.check_ida(),
            return_exceptions=True,
        )

        overall_status = HealthStatus.HEALTHY
        for check in checks:
            if isinstance(check, Exception):
                overall_status = HealthStatus.UNHEALTHY
                break
            if check.status == HealthStatus.UNHEALTHY:
                overall_status = HealthStatus.UNHEALTHY
            elif check.status == HealthStatus.DEGRADED and overall_status == HealthStatus.HEALTHY:
                overall_status = HealthStatus.DEGRADED

        return {
            "status": overall_status.value,
            "components": {
                name: {
                    "status": comp.status.value,
                    "latency_ms": round(comp.latency_ms, 2),
                    "error": comp.error,
                    "last_check": comp.last_check,
                    "metadata": comp.metadata,
                }
                for name, comp in self._components.items()
            },
            "timestamp": time.time(),
        }


# ==================== 故障转移 ====================

@dataclass
class FailoverConfig:
    """故障转移配置"""

    check_interval: float = 10.0
    failure_threshold: int = 3
    recovery_threshold: int = 2
    failover_timeout: float = 30.0


class FailoverManager:
    """故障转移管理器

    监控服务实例健康状态，自动切换到备用实例。
    """

    def __init__(self, config: FailoverConfig | None = None) -> None:
        self._config = config or FailoverConfig()
        self._primary_healthy = True
        self._failure_count = 0
        self._recovery_count = 0
        self._failover_active = False
        self._last_failover_time = 0.0

    @property
    def is_failover_active(self) -> bool:
        return self._failover_active

    def record_check_result(self, healthy: bool) -> dict[str, Any]:
        """记录健康检查结果并决定是否触发故障转移

        Args:
            healthy: 本次检查是否健康

        Returns:
            决策结果
        """
        if healthy:
            self._failure_count = 0
            if self._failover_active:
                self._recovery_count += 1
                if self._recovery_count >= self._config.recovery_threshold:
                    self._failover_active = False
                    self._recovery_count = 0
                    logger.info("主服务恢复，切回主服务")
                    return {"action": "switch_back", "reason": "primary_recovered"}
        else:
            self._recovery_count = 0
            self._failure_count += 1
            if not self._failover_active and self._failure_count >= self._config.failure_threshold:
                self._failover_active = True
                self._last_failover_time = time.time()
                logger.warning("主服务故障，切换到备用服务")
                return {"action": "failover", "reason": "primary_unhealthy"}

        return {"action": "none"}

    def get_status(self) -> dict[str, Any]:
        """获取故障转移状态"""
        return {
            "failover_active": self._failover_active,
            "failure_count": self._failure_count,
            "recovery_count": self._recovery_count,
            "last_failover_time": self._last_failover_time,
        }


# ==================== 降级策略 ====================

class DegradationLevel(str, Enum):
    """降级级别"""

    NORMAL = "normal"
    L1_LIGHT = "l1_light"
    L2_MEDIUM = "l2_medium"
    L3_HEAVY = "l3_heavy"
    L4_EXTREME = "l4_extreme"


DEGRADATION_RULES: dict[DegradationLevel, dict[str, Any]] = {
    DegradationLevel.NORMAL: {
        "description": "正常运行",
        "disabled_features": [],
    },
    DegradationLevel.L1_LIGHT: {
        "description": "单个 MCP 服务不可用",
        "disabled_features": ["unavailable_mcp_service"],
    },
    DegradationLevel.L2_MEDIUM: {
        "description": "LLM 服务响应超时",
        "disabled_features": ["unavailable_mcp_service", "complex_task"],
    },
    DegradationLevel.L3_HEAVY: {
        "description": "系统负载过高",
        "disabled_features": ["unavailable_mcp_service", "complex_task", "write_operations"],
    },
    DegradationLevel.L4_EXTREME: {
        "description": "核心存储故障",
        "disabled_features": ["unavailable_mcp_service", "complex_task", "write_operations", "session_persistence"],
    },
}


class DegradationManager:
    """服务降级管理器

    根据系统健康状态自动调整降级级别，
    并执行组件级别的降级回调（如切换存储后端、降级模型等）。
    """

    # 组件降级回调注册表
    # key: 组件名称, value: (on_degraded, on_recovered) 回调元组
    _degradation_handlers: dict[str, tuple[Any, Any]] = {}

    def __init__(self) -> None:
        self._level = DegradationLevel.NORMAL
        self._component_status: dict[str, HealthStatus] = {}

    @classmethod
    def register_handler(
        cls,
        component: str,
        on_degraded: Any,
        on_recovered: Any | None = None,
    ) -> None:
        """注册组件降级回调

        Args:
            component: 组件名称（如 redis、postgres、llm、ida）
            on_degraded: 降级时调用的异步回调
            on_recovered: 恢复时调用的异步回调（可选）
        """
        cls._degradation_handlers[component] = (on_degraded, on_recovered)

    @property
    def level(self) -> DegradationLevel:
        return self._level

    async def evaluate_and_act(self, health_status: dict[str, Any]) -> DegradationLevel:
        """评估降级级别并自动执行降级/恢复回调

        在 evaluate 的基础上，当检测到组件状态变化时，
        自动调用注册的降级回调（如切换存储后端）。

        Args:
            health_status: 健康检查结果

        Returns:
            当前降级级别
        """
        components = health_status.get("components", {})

        # 检测组件状态变化并触发回调
        for name, info in components.items():
            status_str = info.get("status", "healthy")
            new_status = HealthStatus(status_str) if status_str in (s.value for s in HealthStatus) else HealthStatus.HEALTHY
            old_status = self._component_status.get(name)

            if old_status != new_status:
                logger.info("组件状态变化: %s %s -> %s", name, old_status, new_status)
                self._component_status[name] = new_status

                handlers = self._degradation_handlers.get(name)
                if handlers:
                    on_degraded, on_recovered = handlers
                    try:
                        if new_status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED):
                            if on_degraded:
                                await on_degraded()
                                logger.info("已执行 %s 降级回调", name)
                        elif new_status == HealthStatus.HEALTHY and old_status is not None:
                            if on_recovered:
                                await on_recovered()
                                logger.info("已执行 %s 恢复回调", name)
                    except Exception as e:
                        logger.error("执行 %s 降级回调失败: %s", name, e)

        # 评估全局降级级别
        return self.evaluate(health_status)

    def evaluate(self, health_status: dict[str, Any]) -> DegradationLevel:
        """根据健康状态评估降级级别

        Args:
            health_status: 健康检查结果

        Returns:
            建议的降级级别
        """
        components = health_status.get("components", {})
        unhealthy = [n for n, c in components.items() if c.get("status") == "unhealthy"]
        degraded = [n for n, c in components.items() if c.get("status") == "degraded"]

        if "llm" in unhealthy:
            new_level = DegradationLevel.L2_MEDIUM
        elif "postgres" in unhealthy or "redis" in unhealthy:
            new_level = DegradationLevel.L4_EXTREME
        elif len(unhealthy) >= 2:
            new_level = DegradationLevel.L3_HEAVY
        elif len(unhealthy) >= 1:
            new_level = DegradationLevel.L1_LIGHT
        elif len(degraded) >= 2:
            new_level = DegradationLevel.L1_LIGHT
        else:
            new_level = DegradationLevel.NORMAL

        if new_level != self._level:
            logger.info("降级级别变更: %s -> %s", self._level.value, new_level.value)
            self._level = new_level

        return self._level

    def is_feature_enabled(self, feature: str) -> bool:
        """检查指定功能在当前降级级别下是否可用"""
        rules = DEGRADATION_RULES.get(self._level, {})
        disabled = rules.get("disabled_features", [])
        return feature not in disabled

    def get_status(self) -> dict[str, Any]:
        """获取降级状态"""
        rules = DEGRADATION_RULES.get(self._level, {})
        return {
            "level": self._level.value,
            "description": rules.get("description", ""),
            "disabled_features": rules.get("disabled_features", []),
            "component_status": {k: v.value for k, v in self._component_status.items()},
        }


# 全局实例
_health_checker: HealthChecker | None = None
_failover_manager: FailoverManager | None = None
_degradation_manager: DegradationManager | None = None


def get_health_checker() -> HealthChecker:
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


def get_failover_manager() -> FailoverManager:
    global _failover_manager
    if _failover_manager is None:
        _failover_manager = FailoverManager()
    return _failover_manager


def get_degradation_manager() -> DegradationManager:
    global _degradation_manager
    if _degradation_manager is None:
        _degradation_manager = DegradationManager()
    return _degradation_manager
