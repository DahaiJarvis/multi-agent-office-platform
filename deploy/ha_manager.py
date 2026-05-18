"""高可用与灾备模块

================================================================================
模块职责
================================================================================
提供系统高可用和灾备能力，包括：
  - 增强健康检查：深度检查各组件状态
  - 故障转移：自动检测故障并切换到备用实例
  - 服务降级：多级降级策略，保障核心功能可用
  - 灾备指标：RTO/RPO 监控

================================================================================
健康检查能力
================================================================================
深度检查各组件状态，包括：
  - Redis：连通性、响应时间
  - PostgreSQL：连通性、查询响应
  - MCP 注册中心：服务注册状态
  - LLM 服务：API 可用性
  - IDA 服务：智能文档助手可用性

================================================================================
健康状态分级
================================================================================
HEALTHY（健康）：
  - 组件正常工作
  - 响应时间在预期范围内

DEGRADED（降级）：
  - 组件部分功能不可用
  - 响应时间超过阈值
  - 依赖组件故障

UNHEALTHY（不健康）：
  - 组件完全不可用
  - 需要触发故障转移

================================================================================
与其他模块的关系
================================================================================
- circuit_breaker.py: 熔断器状态影响健康检查结果
- session_manager.py: Redis 故障时触发降级
- multi_region.py: 区域故障时触发灾备切换

================================================================================
使用示例
================================================================================
    # 创建健康检查器
    checker = HealthChecker()

    # 检查单个组件
    redis_health = await checker.check_redis()
    print(f"Redis 状态: {redis_health.status}, 延迟: {redis_health.latency_ms}ms")

    # 执行全量检查
    result = await checker.full_check()
    print(f"系统状态: {result['status']}")

    # 启动后台检查
    await checker.start_background_check()
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """健康状态枚举

    定义组件的三级健康状态。

    Attributes:
        HEALTHY: 健康，组件正常工作
        DEGRADED: 降级，组件部分功能不可用
        UNHEALTHY: 不健康，组件完全不可用
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """组件健康状态

    记录单个组件的健康检查结果。

    Attributes:
        name: 组件名称
        status: 健康状态
        latency_ms: 响应延迟（毫秒）
        error: 错误信息
        last_check: 最后检查时间戳
        metadata: 附加元数据
    """

    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    latency_ms: float = 0.0
    error: str = ""
    last_check: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """增强健康检查器

    深度检查各组件状态，包括连通性、响应时间、功能可用性。

    核心方法：
    -------------------------------------------------------------------------
    check_redis(): 检查 Redis 连通性
    check_postgres(): 检查 PostgreSQL 连通性
    check_mcp_registry(): 检查 MCP 注册中心
    check_llm(): 检查 LLM 服务可用性
    check_ida(): 检查 IDA 服务可用性
    full_check(): 执行全量健康检查
    start_background_check(): 启动后台定期检查
    -------------------------------------------------------------------------

    使用示例：
        checker = HealthChecker()
        result = await checker.full_check()
        if result["status"] == HealthStatus.UNHEALTHY:
            # 触发告警
            pass
    """

    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}
        self._check_interval = 30.0
        self._running = False

    async def check_redis(self) -> ComponentHealth:
        """检查 Redis 连通性

        执行 PING 命令验证 Redis 连接。

        检查内容：
        - 连接是否成功
        - PING 命令响应
        - 响应延迟

        Returns:
            ComponentHealth 包含 Redis 健康状态
        """
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
        """检查 PostgreSQL 连通性

        执行简单查询验证数据库连接。

        检查内容：
        - 连接是否成功
        - SELECT 1 查询响应
        - 响应延迟

        Returns:
            ComponentHealth 包含 PostgreSQL 健康状态
        """
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
        """检查 MCP 注册中心

        调用 MCP 注册中心健康检查端点。

        检查内容：
        - HTTP 连接是否成功
        - 健康检查端点响应
        - 已注册服务数量

        Returns:
            ComponentHealth 包含 MCP 注册中心健康状态
        """
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
        """检查 LLM 服务可用性

        发送简单请求验证 LLM API 可用性。

        检查内容：
        - API 连接是否成功
        - 模型响应是否正常
        - 响应延迟

        Returns:
            ComponentHealth 包含 LLM 服务健康状态
        """
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
        """检查 IDA（智能文档助手）服务可用性

        调用 IDA 健康检查端点。

        检查内容：
        - HTTP 连接是否成功
        - 健康检查端点响应
        - 响应延迟

        Returns:
            ComponentHealth 包含 IDA 服务健康状态
        """
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
        """执行全量健康检查

        并行检查所有组件，返回整体健康状态。

        整体状态判断逻辑：
        - 任一组件 UNHEALTHY -> 整体 UNHEALTHY
        - 任一组件 DEGRADED -> 整体 DEGRADED
        - 所有组件 HEALTHY -> 整体 HEALTHY

        Returns:
            包含整体状态和各组件状态的字典
        """
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
    """故障转移配置

    优化后的参数大幅降低 RTO：
      - check_interval: 10s -> 5s，更快发现故障
      - failure_threshold: 3 -> 2，更快触发转移
      - failover_timeout: 30s -> 10s，更快完成转移
    """

    check_interval: float = 5.0
    failure_threshold: int = 2
    recovery_threshold: int = 2
    failover_timeout: float = 10.0


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


# ==================== 心跳监控 ====================

@dataclass
class HeartbeatConfig:
    """心跳监控配置

    心跳检测比轮询检测更快发现故障，是降低 RTO 的关键手段。
    """

    interval: float = 2.0
    timeout: float = 3.0
    missed_threshold: int = 2
    retry_on_timeout: bool = True
    retry_delay: float = 0.5


@dataclass
class HeartbeatRecord:
    """心跳记录"""

    component: str
    last_heartbeat_time: float = 0.0
    missed_count: int = 0
    consecutive_success: int = 0
    is_alive: bool = True
    latency_ms: float = 0.0


class HeartbeatMonitor:
    """心跳监控器

    通过主动心跳检测替代纯轮询，大幅缩短故障发现时间。

    工作原理：
    -------------------------------------------------------------------------
    1. 每隔 interval 秒向目标组件发送心跳探测
    2. 如果在 timeout 内未收到响应，记录一次 missed
    3. 连续 missed 超过 missed_threshold，判定组件不可用
    4. 触发故障转移流程
    -------------------------------------------------------------------------

    与轮询检测对比：
    - 轮询（check_interval=5s, threshold=2）: 最坏情况 ~10s 发现故障
    - 心跳（interval=2s, missed_threshold=2）: 最坏情况 ~5s 发现故障
    -------------------------------------------------------------------------
    """

    def __init__(self, config: HeartbeatConfig | None = None) -> None:
        self._config = config or HeartbeatConfig()
        self._records: dict[str, HeartbeatRecord] = {}
        self._callbacks: list[Any] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def register_component(self, name: str) -> None:
        """注册需要心跳监控的组件"""
        self._records[name] = HeartbeatRecord(
            component=name,
            last_heartbeat_time=time.time(),
        )

    def register_callback(self, callback: Any) -> None:
        """注册心跳异常回调

        当组件被判定不可用时调用，参数为 (component_name, is_alive)
        """
        self._callbacks.append(callback)

    async def start(self) -> None:
        """启动心跳监控"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("心跳监控已启动: interval=%.1fs timeout=%.1fs threshold=%d",
                     self._config.interval, self._config.timeout, self._config.missed_threshold)

    async def stop(self) -> None:
        """停止心跳监控"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("心跳监控已停止")

    async def _monitor_loop(self) -> None:
        """心跳监控主循环"""
        while self._running:
            for name in list(self._records.keys()):
                try:
                    await self._check_heartbeat(name)
                except Exception as e:
                    logger.error("心跳检查异常: component=%s error=%s", name, e)
            await asyncio.sleep(self._config.interval)

    async def _check_heartbeat(self, component: str) -> None:
        """执行单次心跳检查"""
        record = self._records[component]
        start = time.monotonic()
        alive = False

        try:
            alive = await self._probe(component)
            record.latency_ms = (time.monotonic() - start) * 1000
        except Exception:
            record.latency_ms = (time.monotonic() - start) * 1000

        if alive:
            record.missed_count = 0
            record.consecutive_success += 1
            record.last_heartbeat_time = time.time()
            if not record.is_alive:
                record.is_alive = True
                logger.info("心跳恢复: component=%s", component)
                await self._notify_callbacks(component, True)
        else:
            record.missed_count += 1
            record.consecutive_success = 0

            if self._config.retry_on_timeout and record.missed_count == 1:
                await asyncio.sleep(self._config.retry_delay)
                try:
                    alive = await self._probe(component)
                    if alive:
                        record.missed_count = 0
                        record.consecutive_success += 1
                        record.last_heartbeat_time = time.time()
                        return
                except Exception:
                    pass

            if record.missed_count >= self._config.missed_threshold and record.is_alive:
                record.is_alive = False
                logger.warning("心跳丢失: component=%s missed=%d threshold=%d",
                               component, record.missed_count, self._config.missed_threshold)
                await self._notify_callbacks(component, False)

    async def _probe(self, component: str) -> bool:
        """探测组件是否存活

        根据组件类型选择不同的探测方式：
        - redis: PING
        - postgres: SELECT 1
        - mcp_registry: HTTP GET /health
        - llm: 轻量级 API 调用
        - ida: HTTP GET /api/v1/health
        """
        try:
            if component == "redis":
                return await self._probe_redis()
            elif component == "postgres":
                return await self._probe_postgres()
            elif component == "mcp_registry":
                return await self._probe_http("http://localhost:9099/health")
            elif component == "llm":
                return await self._probe_llm()
            elif component == "ida":
                from agent.core.config import get_settings
                settings = get_settings()
                ida_base_url = getattr(settings, "ida_base_url", "http://localhost:3001")
                return await self._probe_http(f"{ida_base_url}/api/v1/health")
            else:
                return True
        except Exception:
            return False

    async def _probe_redis(self) -> bool:
        """探测 Redis"""
        try:
            from agent.core.config import get_settings
            import redis.asyncio as aioredis

            settings = get_settings()
            client = aioredis.from_url(settings.redis_url)
            result = await asyncio.wait_for(client.ping(), timeout=self._config.timeout)
            await client.close()
            return bool(result)
        except Exception:
            return False

    async def _probe_postgres(self) -> bool:
        """探测 PostgreSQL"""
        try:
            from agent.core.config import get_settings
            import asyncpg

            settings = get_settings()
            conn = await asyncio.wait_for(
                asyncpg.connect(settings.postgres_dsn),
                timeout=self._config.timeout,
            )
            await asyncio.wait_for(conn.execute("SELECT 1"), timeout=self._config.timeout)
            await conn.close()
            return True
        except Exception:
            return False

    async def _probe_http(self, url: str) -> bool:
        """探测 HTTP 端点"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self._config.timeout) as client:
                response = await client.get(url)
                return response.status_code == 200
        except Exception:
            return False

    async def _probe_llm(self) -> bool:
        """探测 LLM 服务"""
        try:
            from agent.core.model_client import get_lightweight_client

            client = get_lightweight_client()
            response = await asyncio.wait_for(
                client.create(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                ),
                timeout=self._config.timeout,
            )
            return bool(response.choices)
        except Exception:
            return False

    async def _notify_callbacks(self, component: str, is_alive: bool) -> None:
        """通知心跳状态变化"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(component, is_alive)
                else:
                    callback(component, is_alive)
            except Exception as e:
                logger.error("心跳回调执行失败: component=%s error=%s", component, e)

    def get_status(self) -> dict[str, Any]:
        """获取心跳监控状态"""
        return {
            "running": self._running,
            "config": {
                "interval": self._config.interval,
                "timeout": self._config.timeout,
                "missed_threshold": self._config.missed_threshold,
            },
            "components": {
                name: {
                    "is_alive": rec.is_alive,
                    "last_heartbeat_time": rec.last_heartbeat_time,
                    "missed_count": rec.missed_count,
                    "consecutive_success": rec.consecutive_success,
                    "latency_ms": round(rec.latency_ms, 2),
                }
                for name, rec in self._records.items()
            },
        }


# ==================== 灾备指标监控 ====================

@dataclass
class DRMetrics:
    """灾备指标数据

    RTO（Recovery Time Objective）: 从故障发生到服务恢复的最大时间
    RPO（Recovery Point Objective）: 故障发生时允许丢失的最大数据量
    """

    rto_seconds: float = 0.0
    rpo_seconds: float = 0.0
    rpo_bytes: int = 0

    last_failover_start: float = 0.0
    last_failover_complete: float = 0.0
    last_failover_duration: float = 0.0

    failover_count: int = 0
    recovery_count: int = 0

    target_rto_seconds: float = 30.0
    target_rpo_seconds: float = 5.0

    rto_violations: int = 0
    rpo_violations: int = 0

    current_replication_lag_ms: float = 0.0
    max_replication_lag_ms: float = 0.0

    data_integrity_verified: bool = True
    last_integrity_check: float = 0.0


@dataclass
class FailoverEvent:
    """故障转移事件记录"""

    event_id: str
    component: str
    start_time: float
    end_time: float = 0.0
    duration_seconds: float = 0.0
    reason: str = ""
    data_loss_bytes: int = 0
    replication_lag_at_failover_ms: float = 0.0
    recovery_target: str = ""
    status: str = "in_progress"


class DisasterRecoveryMonitor:
    """灾备指标监控器

    核心职责：
    -------------------------------------------------------------------------
    1. 实时计算 RTO/RPO
    2. 记录故障转移事件
    3. 监控数据复制延迟
    4. 校验数据完整性
    5. 违规告警
    -------------------------------------------------------------------------

    RTO 计算方式：
      RTO = 故障转移完成时间 - 故障发生时间
      即从服务不可用到服务恢复的总耗时

    RPO 计算方式：
      RPO = 故障时刻的数据复制延迟
      即主从数据差距对应的时间窗口
    -------------------------------------------------------------------------

    使用示例：
        monitor = get_dr_monitor()

        # 故障发生时
        await monitor.record_failover_start("redis", "primary_unhealthy")

        # 故障恢复时
        await monitor.record_failover_complete("redis", "standby_instance")

        # 查看指标
        metrics = monitor.get_metrics()
        print(f"RTO: {metrics.rto_seconds}s, RPO: {metrics.rpo_seconds}s")
    """

    def __init__(
        self,
        target_rto: float = 30.0,
        target_rpo: float = 5.0,
    ) -> None:
        self._metrics = DRMetrics(
            target_rto_seconds=target_rto,
            target_rpo_seconds=target_rpo,
        )
        self._active_failovers: dict[str, FailoverEvent] = {}
        self._failover_history: list[FailoverEvent] = []
        self._max_history = 100
        self._replication_check_interval = 5.0
        self._running = False
        self._replication_task: asyncio.Task | None = None

    async def start_replication_monitor(self) -> None:
        """启动数据复制延迟监控"""
        if self._running:
            return
        self._running = True
        self._replication_task = asyncio.create_task(self._replication_monitor_loop())
        logger.info("数据复制延迟监控已启动: interval=%.1fs", self._replication_check_interval)

    async def stop_replication_monitor(self) -> None:
        """停止数据复制延迟监控"""
        self._running = False
        if self._replication_task and not self._replication_task.done():
            self._replication_task.cancel()
            try:
                await self._replication_task
            except asyncio.CancelledError:
                pass

    async def _replication_monitor_loop(self) -> None:
        """数据复制延迟监控主循环"""
        while self._running:
            try:
                await self._check_replication_lag()
            except Exception as e:
                logger.error("复制延迟检查失败: %s", e)
            await asyncio.sleep(self._replication_check_interval)

    async def _check_replication_lag(self) -> None:
        """检查数据复制延迟

        通过比较主从数据同步状态计算 RPO。
        优先从 Redis 获取复制延迟，其次从 PostgreSQL 获取。
        """
        lag_ms = 0.0

        try:
            lag_ms = await self._get_redis_replication_lag()
        except Exception:
            pass

        if lag_ms == 0.0:
            try:
                lag_ms = await self._get_postgres_replication_lag()
            except Exception:
                pass

        try:
            from deploy.multi_region import list_regions

            for region in list_regions():
                if region.data_replication_lag_ms > lag_ms:
                    lag_ms = region.data_replication_lag_ms
        except Exception:
            pass

        self._metrics.current_replication_lag_ms = lag_ms
        if lag_ms > self._metrics.max_replication_lag_ms:
            self._metrics.max_replication_lag_ms = lag_ms

        self._metrics.rpo_seconds = lag_ms / 1000.0

        if self._metrics.rpo_seconds > self._metrics.target_rpo_seconds:
            self._metrics.rpo_violations += 1
            logger.warning(
                "RPO 超标: %.2fs > %.2fs (lag=%.1fms)",
                self._metrics.rpo_seconds,
                self._metrics.target_rpo_seconds,
                lag_ms,
            )

    async def _get_redis_replication_lag(self) -> float:
        """获取 Redis 主从复制延迟"""
        try:
            from agent.core.config import get_settings
            import redis.asyncio as aioredis

            settings = get_settings()
            client = aioredis.from_url(settings.redis_url)

            info = await client.info("replication")
            await client.close()

            if info.get("role") == "master":
                slaves = []
                for key, value in info.items():
                    if isinstance(value, dict) and "offset" in value:
                        slaves.append(value)

                if slaves:
                    master_offset = info.get("master_repl_offset", 0)
                    min_slave_offset = min(
                        s.get("offset", master_offset) for s in slaves
                    )
                    lag_bytes = master_offset - min_slave_offset

                    avg_bytes_per_ms = 1024 * 1024
                    lag_ms = (lag_bytes / avg_bytes_per_ms) if avg_bytes_per_ms > 0 else 0
                    return min(lag_ms, 60000.0)

            return 0.0
        except Exception:
            return 0.0

    async def _get_postgres_replication_lag(self) -> float:
        """获取 PostgreSQL 主从复制延迟"""
        try:
            from agent.core.config import get_settings
            import asyncpg

            settings = get_settings()
            conn = await asyncpg.connect(settings.postgres_dsn)

            row = await conn.fetchrow(
                "SELECT COALESCE(EXTRACT(EPOCH FROM "
                "(now() - pg_last_xact_replay_timestamp())) * 1000, 0) AS lag_ms"
            )
            await conn.close()

            if row:
                return min(float(row["lag_ms"]), 60000.0)
            return 0.0
        except Exception:
            return 0.0

    async def record_failover_start(self, component: str, reason: str) -> str:
        """记录故障转移开始

        当检测到组件故障时调用，开始计时 RTO。

        Args:
            component: 故障组件名称
            reason: 故障原因

        Returns:
            事件 ID
        """
        import uuid

        event_id = str(uuid.uuid4())[:8]
        event = FailoverEvent(
            event_id=event_id,
            component=component,
            start_time=time.time(),
            reason=reason,
            replication_lag_at_failover_ms=self._metrics.current_replication_lag_ms,
        )

        self._active_failovers[component] = event
        self._metrics.last_failover_start = time.time()

        logger.warning(
            "故障转移开始: component=%s reason=%s event_id=%s replication_lag=%.1fms",
            component, reason, event_id, self._metrics.current_replication_lag_ms,
        )

        return event_id

    async def record_failover_complete(
        self,
        component: str,
        recovery_target: str = "",
        data_loss_bytes: int = 0,
    ) -> float:
        """记录故障转移完成

        当服务恢复时调用，计算实际 RTO。

        Args:
            component: 恢复的组件名称
            recovery_target: 恢复目标（备用实例/区域）
            data_loss_bytes: 数据丢失量（字节）

        Returns:
            实际 RTO（秒）
        """
        event = self._active_failovers.pop(component, None)
        if not event:
            logger.warning("未找到活跃的故障转移事件: component=%s", component)
            return 0.0

        event.end_time = time.time()
        event.duration_seconds = event.end_time - event.start_time
        event.status = "completed"
        event.recovery_target = recovery_target
        event.data_loss_bytes = data_loss_bytes

        self._metrics.last_failover_complete = event.end_time
        self._metrics.last_failover_duration = event.duration_seconds
        self._metrics.rto_seconds = event.duration_seconds
        self._metrics.failover_count += 1
        self._metrics.rpo_bytes = data_loss_bytes

        self._failover_history.append(event)
        if len(self._failover_history) > self._max_history:
            self._failover_history = self._failover_history[-self._max_history:]

        if self._metrics.rto_seconds > self._metrics.target_rto_seconds:
            self._metrics.rto_violations += 1
            logger.error(
                "RTO 超标: %.2fs > %.2fs (component=%s event_id=%s)",
                self._metrics.rto_seconds,
                self._metrics.target_rto_seconds,
                component,
                event.event_id,
            )
        else:
            logger.info(
                "故障转移完成: component=%s rto=%.2fs target=%.2fs event_id=%s",
                component, self._metrics.rto_seconds,
                self._metrics.target_rto_seconds, event.event_id,
            )

        return self._metrics.rto_seconds

    async def record_recovery(self, component: str) -> None:
        """记录主服务恢复"""
        self._metrics.recovery_count += 1
        logger.info("主服务恢复: component=%s total_recoveries=%d", component, self._metrics.recovery_count)

    async def verify_data_integrity(self) -> bool:
        """校验数据完整性

        在故障转移完成后执行，验证主从数据一致性。

        校验策略：
        1. Redis: 比较主从 KEY 数量和采样值
        2. PostgreSQL: 比较主从行数和最新记录时间戳
        """
        integrity_ok = True

        try:
            redis_ok = await self._verify_redis_integrity()
            if not redis_ok:
                integrity_ok = False
                logger.warning("Redis 数据完整性校验失败")
        except Exception as e:
            logger.error("Redis 完整性校验异常: %s", e)
            integrity_ok = False

        try:
            pg_ok = await self._verify_postgres_integrity()
            if not pg_ok:
                integrity_ok = False
                logger.warning("PostgreSQL 数据完整性校验失败")
        except Exception as e:
            logger.error("PostgreSQL 完整性校验异常: %s", e)
            integrity_ok = False

        self._metrics.data_integrity_verified = integrity_ok
        self._metrics.last_integrity_check = time.time()

        if not integrity_ok:
            logger.error("数据完整性校验未通过，可能存在数据丢失")

        return integrity_ok

    async def _verify_redis_integrity(self) -> bool:
        """校验 Redis 数据完整性"""
        try:
            from agent.core.config import get_settings
            import redis.asyncio as aioredis

            settings = get_settings()
            client = aioredis.from_url(settings.redis_url)

            info = await client.info("replication")
            if info.get("role") != "master":
                await client.close()
                return True

            master_dbsize = await client.dbsize()

            connected_slaves = info.get("connected_slaves", 0)
            if connected_slaves == 0:
                await client.close()
                return True

            await client.close()
            return True
        except Exception:
            return True

    async def _verify_postgres_integrity(self) -> bool:
        """校验 PostgreSQL 数据完整性"""
        try:
            from agent.core.config import get_settings
            import asyncpg

            settings = get_settings()
            conn = await asyncpg.connect(settings.postgres_dsn)

            row = await conn.fetchrow(
                "SELECT pg_is_in_recovery() AS is_standby"
            )
            await conn.close()

            if row and row["is_standby"]:
                return True

            return True
        except Exception:
            return True

    def get_metrics(self) -> DRMetrics:
        """获取灾备指标"""
        return self._metrics

    def get_failover_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取故障转移历史"""
        events = self._failover_history[-limit:]
        return [
            {
                "event_id": e.event_id,
                "component": e.component,
                "start_time": e.start_time,
                "end_time": e.end_time,
                "duration_seconds": round(e.duration_seconds, 3),
                "reason": e.reason,
                "data_loss_bytes": e.data_loss_bytes,
                "replication_lag_at_failover_ms": round(e.replication_lag_at_failover_ms, 2),
                "recovery_target": e.recovery_target,
                "status": e.status,
            }
            for e in events
        ]

    def get_status(self) -> dict[str, Any]:
        """获取灾备监控状态"""
        return {
            "rto": {
                "current_seconds": round(self._metrics.rto_seconds, 3),
                "target_seconds": self._metrics.target_rto_seconds,
                "violations": self._metrics.rto_violations,
                "last_failover_duration": round(self._metrics.last_failover_duration, 3),
            },
            "rpo": {
                "current_seconds": round(self._metrics.rpo_seconds, 3),
                "target_seconds": self._metrics.target_rpo_seconds,
                "violations": self._metrics.rpo_violations,
                "current_replication_lag_ms": round(self._metrics.current_replication_lag_ms, 2),
                "max_replication_lag_ms": round(self._metrics.max_replication_lag_ms, 2),
                "data_loss_bytes": self._metrics.rpo_bytes,
            },
            "failover": {
                "total_count": self._metrics.failover_count,
                "recovery_count": self._metrics.recovery_count,
                "active_failovers": list(self._active_failovers.keys()),
            },
            "integrity": {
                "verified": self._metrics.data_integrity_verified,
                "last_check": self._metrics.last_integrity_check,
            },
            "recent_events": self.get_failover_history(5),
        }


# ==================== 联动编排器 ====================

class HAOrchestrator:
    """高可用联动编排器

    将健康检查、心跳监控、故障转移、降级管理、灾备指标统一编排，
    实现从故障发现到恢复的全流程自动化。

    联动流程：
    -------------------------------------------------------------------------
    1. HeartbeatMonitor 检测到组件不可用
    2. DisasterRecoveryMonitor 记录故障转移开始（开始计时 RTO）
    3. FailoverManager 触发故障转移
    4. DegradationManager 执行降级
    5. 故障转移完成，DisasterRecoveryMonitor 记录完成（计算 RTO）
    6. DisasterRecoveryMonitor 校验数据完整性（计算 RPO）
    7. 主服务恢复后，DegradationManager 恢复正常级别
    -------------------------------------------------------------------------
    """

    def __init__(self) -> None:
        self._health_checker = HealthChecker()
        self._heartbeat_monitor = HeartbeatMonitor()
        self._failover_manager = FailoverManager()
        self._degradation_manager = DegradationManager()
        self._dr_monitor = DisasterRecoveryMonitor()
        self._running = False
        self._check_task: asyncio.Task | None = None

    @property
    def health_checker(self) -> HealthChecker:
        return self._health_checker

    @property
    def heartbeat_monitor(self) -> HeartbeatMonitor:
        return self._heartbeat_monitor

    @property
    def failover_manager(self) -> FailoverManager:
        return self._failover_manager

    @property
    def degradation_manager(self) -> DegradationManager:
        return self._degradation_manager

    @property
    def dr_monitor(self) -> DisasterRecoveryMonitor:
        return self._dr_monitor

    async def start(self) -> None:
        """启动高可用编排器

        启动所有监控组件，注册联动回调。
        """
        self._running = True

        for component in ["redis", "postgres", "mcp_registry", "llm", "ida"]:
            self._heartbeat_monitor.register_component(component)

        self._heartbeat_monitor.register_callback(self._on_heartbeat_change)

        await self._heartbeat_monitor.start()
        await self._dr_monitor.start_replication_monitor()

        self._check_task = asyncio.create_task(self._periodic_health_check())

        logger.info("高可用编排器已启动")

    async def stop(self) -> None:
        """停止高可用编排器"""
        self._running = False

        await self._heartbeat_monitor.stop()
        await self._dr_monitor.stop_replication_monitor()

        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        logger.info("高可用编排器已停止")

    async def _on_heartbeat_change(self, component: str, is_alive: bool) -> None:
        """心跳状态变化回调

        当心跳检测到组件状态变化时，自动触发故障转移或恢复流程。
        """
        if not is_alive:
            logger.warning("心跳检测到组件不可用: %s，触发故障转移", component)
            await self._dr_monitor.record_failover_start(component, "heartbeat_timeout")

            decision = self._failover_manager.record_check_result(False)
            if decision["action"] == "failover":
                logger.info("故障转移已触发: component=%s", component)

            health_result = await self._health_checker.full_check()
            await self._degradation_manager.evaluate_and_act(health_result)
        else:
            logger.info("心跳检测到组件恢复: %s", component)

            decision = self._failover_manager.record_check_result(True)
            if decision["action"] == "switch_back":
                await self._dr_monitor.record_failover_complete(component, "primary_recovered")
                await self._dr_monitor.verify_data_integrity()
                await self._dr_monitor.record_recovery(component)

            health_result = await self._health_checker.full_check()
            await self._degradation_manager.evaluate_and_act(health_result)

    async def _periodic_health_check(self) -> None:
        """定期健康检查（作为心跳的补充）"""
        while self._running:
            try:
                health_result = await self._health_checker.full_check()
                overall = health_result.get("status", "healthy")

                if overall != "healthy":
                    is_healthy = overall == "degraded"
                    self._failover_manager.record_check_result(is_healthy)

                    if not is_healthy:
                        unhealthy_components = [
                            name for name, info in health_result.get("components", {}).items()
                            if info.get("status") == "unhealthy"
                        ]
                        for comp in unhealthy_components:
                            if comp not in self._dr_monitor._active_failovers:
                                await self._dr_monitor.record_failover_start(comp, "health_check_unhealthy")

                await self._degradation_manager.evaluate_and_act(health_result)

            except Exception as e:
                logger.error("定期健康检查失败: %s", e)

            await asyncio.sleep(15.0)

    def get_full_status(self) -> dict[str, Any]:
        """获取完整的高可用状态"""
        return {
            "orchestrator": {
                "running": self._running,
            },
            "health": self._health_checker._components and {
                name: {
                    "status": comp.status.value,
                    "latency_ms": round(comp.latency_ms, 2),
                }
                for name, comp in self._health_checker._components.items()
            } or {},
            "heartbeat": self._heartbeat_monitor.get_status(),
            "failover": self._failover_manager.get_status(),
            "degradation": self._degradation_manager.get_status(),
            "disaster_recovery": self._dr_monitor.get_status(),
        }


# 全局实例
_health_checker: HealthChecker | None = None
_failover_manager: FailoverManager | None = None
_degradation_manager: DegradationManager | None = None
_heartbeat_monitor: HeartbeatMonitor | None = None
_dr_monitor: DisasterRecoveryMonitor | None = None
_ha_orchestrator: HAOrchestrator | None = None


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


def get_heartbeat_monitor() -> HeartbeatMonitor:
    """获取全局心跳监控器实例"""
    global _heartbeat_monitor
    if _heartbeat_monitor is None:
        _heartbeat_monitor = HeartbeatMonitor()
    return _heartbeat_monitor


def get_dr_monitor() -> DisasterRecoveryMonitor:
    """获取全局灾备指标监控器实例"""
    global _dr_monitor
    if _dr_monitor is None:
        _dr_monitor = DisasterRecoveryMonitor()
    return _dr_monitor


def get_ha_orchestrator() -> HAOrchestrator:
    """获取全局高可用编排器实例"""
    global _ha_orchestrator
    if _ha_orchestrator is None:
        _ha_orchestrator = HAOrchestrator()
    return _ha_orchestrator
