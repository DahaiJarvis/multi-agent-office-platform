"""负载均衡器

将请求均匀分发到后端 Agent 服务实例，
支持加权轮询和一致性哈希两种策略。
"""

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServiceInstance:
    """服务实例"""

    id: str
    host: str
    port: int
    weight: int = 1
    healthy: bool = True
    active_connections: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


class LoadBalancerStrategy(ABC):
    """负载均衡策略基类"""

    @abstractmethod
    def select(self, instances: list[ServiceInstance]) -> ServiceInstance | None:
        """选择一个服务实例

        Args:
            instances: 可用实例列表

        Returns:
            选中的实例，无可用实例时返回 None
        """


class WeightedRoundRobin(LoadBalancerStrategy):
    """加权轮询策略

    根据实例权重分配请求，权重越高被选中的概率越大。
    采用平滑加权轮询算法，避免短时间内集中请求高权重实例。
    """

    def __init__(self) -> None:
        self._current_weights: dict[str, int] = {}
        self._lock = threading.Lock()

    def select(self, instances: list[ServiceInstance]) -> ServiceInstance | None:
        healthy = [i for i in instances if i.healthy]
        if not healthy:
            return None

        with self._lock:
            total_weight = sum(i.weight for i in healthy)

            for inst in healthy:
                current = self._current_weights.get(inst.id, 0)
                self._current_weights[inst.id] = current + inst.weight

            best = max(healthy, key=lambda i: self._current_weights.get(i.id, 0))
            self._current_weights[best.id] = self._current_weights.get(best.id, 0) - total_weight

            return best


class ConsistentHash(LoadBalancerStrategy):
    """一致性哈希策略

    根据请求的哈希值将请求映射到固定实例，
    实例增减时仅影响相邻节点，减少请求迁移。
    """

    def __init__(self, virtual_nodes: int = 150) -> None:
        self._virtual_nodes = virtual_nodes
        self._ring: list[tuple[int, str]] = []
        self._instance_map: dict[str, ServiceInstance] = {}
        self._lock = threading.Lock()

    def _build_ring(self, instances: list[ServiceInstance]) -> None:
        self._ring = []
        self._instance_map = {}
        for inst in instances:
            if not inst.healthy:
                continue
            self._instance_map[inst.id] = inst
            for i in range(self._virtual_nodes):
                key = self._hash(f"{inst.id}:{i}")
                self._ring.append((key, inst.id))
        self._ring.sort(key=lambda x: x[0])

    def select(self, instances: list[ServiceInstance]) -> ServiceInstance | None:
        healthy = [i for i in instances if i.healthy]
        if not healthy:
            return None

        with self._lock:
            self._build_ring(healthy)
            if not self._ring:
                return None

            hash_key = self._hash(str(id(self)))
            return self._find_instance(hash_key)

    def select_by_key(self, instances: list[ServiceInstance], key: str) -> ServiceInstance | None:
        """根据指定 key 进行一致性哈希选择

        Args:
            instances: 实例列表
            key: 哈希 key，通常使用 user_id 或 session_id

        Returns:
            选中的实例
        """
        healthy = [i for i in instances if i.healthy]
        if not healthy:
            return None

        with self._lock:
            self._build_ring(healthy)
            if not self._ring:
                return None

            hash_key = self._hash(key)
            return self._find_instance(hash_key)

    def _find_instance(self, hash_key: int) -> ServiceInstance | None:
        for ring_key, inst_id in self._ring:
            if ring_key >= hash_key:
                return self._instance_map.get(inst_id)
        if self._ring:
            return self._instance_map.get(self._ring[0][1])
        return None

    @staticmethod
    def _hash(key: str) -> int:
        return hash(key) & 0xFFFFFFFF


class LoadBalancer:
    """负载均衡器

    管理服务实例列表，根据指定策略选择实例。
    支持实例注册/注销、健康状态管理。
    """

    def __init__(self, strategy: LoadBalancerStrategy | None = None) -> None:
        self._strategy = strategy or WeightedRoundRobin()
        self._instances: dict[str, ServiceInstance] = {}
        self._lock = threading.Lock()

    def register(self, instance: ServiceInstance) -> None:
        """注册服务实例"""
        with self._lock:
            self._instances[instance.id] = instance
            logger.info("注册服务实例: %s (%s)", instance.id, instance.address)

    def deregister(self, instance_id: str) -> None:
        """注销服务实例"""
        with self._lock:
            self._instances.pop(instance_id, None)
            logger.info("注销服务实例: %s", instance_id)

    def select(self, key: str = "") -> ServiceInstance | None:
        """选择一个服务实例

        Args:
            key: 哈希 key，用于一致性哈希策略

        Returns:
            选中的实例
        """
        with self._lock:
            instances = list(self._instances.values())

        if not instances:
            return None

        if isinstance(self._strategy, ConsistentHash) and key:
            return self._strategy.select_by_key(instances, key)

        return self._strategy.select(instances)

    def mark_healthy(self, instance_id: str) -> None:
        """标记实例为健康"""
        with self._lock:
            if instance_id in self._instances:
                self._instances[instance_id].healthy = True

    def mark_unhealthy(self, instance_id: str) -> None:
        """标记实例为不健康"""
        with self._lock:
            if instance_id in self._instances:
                self._instances[instance_id].healthy = False
                logger.warning("实例 %s 标记为不健康", instance_id)

    def get_all_instances(self) -> list[ServiceInstance]:
        """获取所有实例"""
        with self._lock:
            return list(self._instances.values())

    def get_healthy_instances(self) -> list[ServiceInstance]:
        """获取健康实例"""
        with self._lock:
            return [i for i in self._instances.values() if i.healthy]

    def get_stats(self) -> dict[str, Any]:
        """获取负载均衡统计信息"""
        instances = list(self._instances.values())
        healthy = [i for i in instances if i.healthy]
        return {
            "total_instances": len(instances),
            "healthy_instances": len(healthy),
            "strategy": type(self._strategy).__name__,
        }


# 全局负载均衡器实例
_agent_load_balancer: LoadBalancer | None = None


def get_agent_load_balancer() -> LoadBalancer:
    """获取 Agent 服务负载均衡器"""
    global _agent_load_balancer
    if _agent_load_balancer is None:
        _agent_load_balancer = LoadBalancer(WeightedRoundRobin())
    return _agent_load_balancer
