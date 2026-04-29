"""性能基准与 SLA 管理

性能基准数据和 SLA 承诺是企业信任的基础。

能力：
  - 性能指标采集：API 延迟、吞吐量、错误率
  - SLA 定义：可用性、响应时间、错误率承诺
  - SLA 监控：实时监控 SLA 达标情况
  - 基准测试：自动化性能基准测试
  - 报告生成：SLA 合规报告
"""

import logging
import statistics
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SLATier(str, Enum):
    """SLA 层级"""

    STANDARD = "standard"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class MetricType(str, Enum):
    """指标类型"""

    LATENCY_P50 = "latency_p50"
    LATENCY_P90 = "latency_p90"
    LATENCY_P99 = "latency_p99"
    THROUGHPUT = "throughput"
    ERROR_RATE = "error_rate"
    AVAILABILITY = "availability"
    UPTIME = "uptime"


class SLADefinition(BaseModel):
    """SLA 定义

    包含性能承诺和预算额度，SLA 层级越高，
    预算额度越大，可用的模型层级越高。
    """

    tier: SLATier
    availability_target: float = Field(default=99.9, description="可用性目标 (%)")
    latency_p50_target_ms: float = Field(default=200, description="P50 延迟目标 (ms)")
    latency_p90_target_ms: float = Field(default=500, description="P90 延迟目标 (ms)")
    latency_p99_target_ms: float = Field(default=1000, description="P99 延迟目标 (ms)")
    error_rate_target: float = Field(default=0.1, description="错误率目标 (%)")
    throughput_target_rps: float = Field(default=100, description="吞吐量目标 (请求/秒)")

    support_response_hours: int = Field(default=8, description="支持响应时间 (小时)")
    incident_resolution_hours: int = Field(default=24, description="故障恢复时间 (小时)")

    # 预算额度
    user_daily_budget: int = Field(default=500000, description="用户日 Token 预算")
    session_budget: int = Field(default=100000, description="会话 Token 预算")
    tenant_daily_budget: int = Field(default=5000000, description="租户日 Token 预算")
    agent_per_call_budget: int = Field(default=50000, description="Agent 单次调用 Token 预算")
    max_model_tier: str = Field(default="max", description="最高可用模型层级")


class MetricSample(BaseModel):
    """指标采样"""

    metric_type: MetricType
    value: float
    timestamp: float = Field(default_factory=time.time)
    labels: dict[str, str] = Field(default_factory=dict)


class SLAStatus(BaseModel):
    """SLA 状态"""

    tier: SLATier
    is_compliant: bool = True
    current_availability: float = 0.0
    current_latency_p50_ms: float = 0.0
    current_latency_p90_ms: float = 0.0
    current_latency_p99_ms: float = 0.0
    current_error_rate: float = 0.0
    current_throughput_rps: float = 0.0
    violations: list[dict[str, Any]] = Field(default_factory=list)
    last_updated: float = Field(default_factory=time.time)


class BenchmarkResult(BaseModel):
    """基准测试结果"""

    name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    latency_min_ms: float = 0
    latency_max_ms: float = 0
    latency_mean_ms: float = 0
    latency_p50_ms: float = 0
    latency_p90_ms: float = 0
    latency_p99_ms: float = 0
    throughput_rps: float = 0
    error_rate: float = 0
    duration_seconds: float = 0
    timestamp: float = Field(default_factory=time.time)


# ==================== SLA 定义 ====================

_SLA_DEFINITIONS: dict[SLATier, SLADefinition] = {
    SLATier.STANDARD: SLADefinition(
        tier=SLATier.STANDARD,
        availability_target=99.5,
        latency_p50_target_ms=500,
        latency_p90_target_ms=1000,
        latency_p99_target_ms=2000,
        error_rate_target=0.5,
        throughput_target_rps=50,
        support_response_hours=24,
        incident_resolution_hours=48,
        user_daily_budget=100000,
        session_budget=30000,
        tenant_daily_budget=1000000,
        agent_per_call_budget=20000,
        max_model_tier="turbo",
    ),
    SLATier.PROFESSIONAL: SLADefinition(
        tier=SLATier.PROFESSIONAL,
        availability_target=99.9,
        latency_p50_target_ms=200,
        latency_p90_target_ms=500,
        latency_p99_target_ms=1000,
        error_rate_target=0.1,
        throughput_target_rps=200,
        support_response_hours=8,
        incident_resolution_hours=24,
        user_daily_budget=500000,
        session_budget=100000,
        tenant_daily_budget=5000000,
        agent_per_call_budget=50000,
        max_model_tier="plus",
    ),
    SLATier.ENTERPRISE: SLADefinition(
        tier=SLATier.ENTERPRISE,
        availability_target=99.99,
        latency_p50_target_ms=100,
        latency_p90_target_ms=200,
        latency_p99_target_ms=500,
        error_rate_target=0.01,
        throughput_target_rps=1000,
        support_response_hours=1,
        incident_resolution_hours=4,
        user_daily_budget=2000000,
        session_budget=500000,
        tenant_daily_budget=20000000,
        agent_per_call_budget=100000,
        max_model_tier="max",
    ),
}


def get_sla_definition(tier: SLATier) -> SLADefinition:
    """获取 SLA 定义"""
    return _SLA_DEFINITIONS[tier]


def list_sla_definitions() -> list[SLADefinition]:
    """列出所有 SLA 定义"""
    return list(_SLA_DEFINITIONS.values())


def get_budget_for_tier(tier: SLATier) -> dict[str, Any]:
    """获取 SLA 层级对应的预算额度

    Args:
        tier: SLA 层级

    Returns:
        预算额度字典
    """
    definition = _SLA_DEFINITIONS[tier]
    return {
        "tier": tier.value,
        "user_daily_budget": definition.user_daily_budget,
        "session_budget": definition.session_budget,
        "tenant_daily_budget": definition.tenant_daily_budget,
        "agent_per_call_budget": definition.agent_per_call_budget,
        "max_model_tier": definition.max_model_tier,
    }


def get_budget_config_for_tier(tier: SLATier) -> Any:
    """根据 SLA 层级生成 BudgetConfig

    将 SLA 层级的预算额度映射为 TokenBudgetManager 可用的配置。

    Args:
        tier: SLA 层级

    Returns:
        BudgetConfig 实例
    """
    from agent.core.token_budget import BudgetConfig

    definition = _SLA_DEFINITIONS[tier]
    return BudgetConfig(
        user_daily_budget=definition.user_daily_budget,
        session_budget=definition.session_budget,
        tenant_daily_budget=definition.tenant_daily_budget,
        agent_per_call_budget=definition.agent_per_call_budget,
        enable_auto_downgrade=True,
    )


def get_max_model_tier(tier: SLATier) -> str:
    """获取 SLA 层级允许的最高模型层级

    在路由选择模型时，需检查用户 SLA 层级是否允许使用该模型。
    例如 STANDARD 层级只能使用 turbo 模型。

    Args:
        tier: SLA 层级

    Returns:
        最高可用模型层级名称
    """
    definition = _SLA_DEFINITIONS[tier]
    return definition.max_model_tier


# ==================== 指标采集 ====================

_latency_samples: list[float] = []
_error_count: int = 0
_total_count: int = 0
_start_time: float = time.time()


def record_latency(duration_ms: float) -> None:
    """记录延迟采样"""
    global _total_count
    _latency_samples.append(duration_ms)
    _total_count += 1

    if len(_latency_samples) > 10000:
        _latency_samples[:] = _latency_samples[-5000:]


def record_error() -> None:
    """记录错误"""
    global _error_count, _total_count
    _error_count += 1
    _total_count += 1


def get_current_metrics() -> dict[str, float]:
    """获取当前指标"""
    if not _latency_samples:
        return {
            "latency_p50_ms": 0,
            "latency_p90_ms": 0,
            "latency_p99_ms": 0,
            "latency_mean_ms": 0,
            "latency_min_ms": 0,
            "latency_max_ms": 0,
            "error_rate": 0,
            "throughput_rps": 0,
            "total_requests": _total_count,
        }

    sorted_samples = sorted(_latency_samples)
    n = len(sorted_samples)

    return {
        "latency_p50_ms": sorted_samples[int(n * 0.5)],
        "latency_p90_ms": sorted_samples[int(n * 0.9)],
        "latency_p99_ms": sorted_samples[min(int(n * 0.99), n - 1)],
        "latency_mean_ms": statistics.mean(sorted_samples),
        "latency_min_ms": sorted_samples[0],
        "latency_max_ms": sorted_samples[-1],
        "error_rate": (_error_count / _total_count * 100) if _total_count > 0 else 0,
        "throughput_rps": _total_count / max(time.time() - _start_time, 1),
        "total_requests": _total_count,
    }


# ==================== SLA 监控 ====================


def check_sla_compliance(tier: SLATier = SLATier.PROFESSIONAL) -> SLAStatus:
    """检查 SLA 合规性

    Args:
        tier: SLA 层级

    Returns:
        SLAStatus
    """
    definition = _SLA_DEFINITIONS[tier]
    metrics = get_current_metrics()

    violations: list[dict[str, Any]] = []

    p50 = metrics.get("latency_p50_ms", 0)
    p90 = metrics.get("latency_p90_ms", 0)
    p99 = metrics.get("latency_p99_ms", 0)
    error_rate = metrics.get("error_rate", 0)
    throughput = metrics.get("throughput_rps", 0)

    if p50 > definition.latency_p50_target_ms:
        violations.append({
            "metric": "latency_p50",
            "target": definition.latency_p50_target_ms,
            "actual": p50,
            "unit": "ms",
        })

    if p90 > definition.latency_p90_target_ms:
        violations.append({
            "metric": "latency_p90",
            "target": definition.latency_p90_target_ms,
            "actual": p90,
            "unit": "ms",
        })

    if p99 > definition.latency_p99_target_ms:
        violations.append({
            "metric": "latency_p99",
            "target": definition.latency_p99_target_ms,
            "actual": p99,
            "unit": "ms",
        })

    if error_rate > definition.error_rate_target:
        violations.append({
            "metric": "error_rate",
            "target": definition.error_rate_target,
            "actual": error_rate,
            "unit": "%",
        })

    if throughput > 0 and throughput < definition.throughput_target_rps * 0.8:
        violations.append({
            "metric": "throughput",
            "target": definition.throughput_target_rps,
            "actual": throughput,
            "unit": "rps",
            "note": "低于目标的80%",
        })

    return SLAStatus(
        tier=tier,
        is_compliant=len(violations) == 0,
        current_latency_p50_ms=p50,
        current_latency_p90_ms=p90,
        current_latency_p99_ms=p99,
        current_error_rate=error_rate,
        current_throughput_rps=throughput,
        current_availability=100 - error_rate,
        violations=violations,
    )


# ==================== 基准测试 ====================


async def run_benchmark(
    name: str = "api_benchmark",
    concurrent_users: int = 10,
    total_requests: int = 100,
    target_url: str = "http://localhost:8000/api/v1/agent/chat",
) -> BenchmarkResult:
    """运行性能基准测试

    Args:
        name: 测试名称
        concurrent_users: 并发用户数
        total_requests: 总请求数
        target_url: 目标 URL

    Returns:
        BenchmarkResult
    """
    import asyncio
    import httpx

    latencies: list[float] = []
    errors = 0
    successes = 0

    start = time.time()

    async def _single_request(client: httpx.AsyncClient) -> None:
        nonlocal errors, successes
        req_start = time.time()
        try:
            response = await client.post(
                target_url,
                json={"message": "benchmark test", "user_id": "bench_user"},
                timeout=30.0,
            )
            elapsed = (time.time() - req_start) * 1000
            latencies.append(elapsed)

            if response.status_code == 200:
                successes += 1
            else:
                errors += 1
        except Exception:
            elapsed = (time.time() - req_start) * 1000
            latencies.append(elapsed)
            errors += 1

    async with httpx.AsyncClient(timeout=30.0) as client:
        semaphore = asyncio.Semaphore(concurrent_users)

        async def _limited_request() -> None:
            async with semaphore:
                await _single_request(client)

        tasks = [_limited_request() for _ in range(total_requests)]
        await asyncio.gather(*tasks)

    duration = time.time() - start

    if not latencies:
        return BenchmarkResult(name=name, total_requests=total_requests, duration_seconds=duration)

    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)

    return BenchmarkResult(
        name=name,
        total_requests=total_requests,
        successful_requests=successes,
        failed_requests=errors,
        latency_min_ms=sorted_latencies[0],
        latency_max_ms=sorted_latencies[-1],
        latency_mean_ms=statistics.mean(sorted_latencies),
        latency_p50_ms=sorted_latencies[int(n * 0.5)],
        latency_p90_ms=sorted_latencies[int(n * 0.9)],
        latency_p99_ms=sorted_latencies[min(int(n * 0.99), n - 1)],
        throughput_rps=total_requests / max(duration, 0.001),
        error_rate=(errors / total_requests * 100) if total_requests > 0 else 0,
        duration_seconds=round(duration, 2),
    )
