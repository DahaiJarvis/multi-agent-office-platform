"""LLM Token 预算与成本控制

提供 Token 用量追踪、预算管理和自动降级能力。

核心功能:
  - 按用户/会话/天维度追踪 Token 消耗
  - 配置化预算阈值，超出预算自动降级模型
  - 成本估算与统计报表
  - 与 Prometheus 指标联动

预算策略:
  - 用户日预算: 每用户每天 Token 消耗上限
  - 会话预算: 单会话 Token 消耗上限
  - 全局预算: 平台整体日消耗上限
  - 降级策略: 超出预算后从 max 降级到 plus，plus 降级到 turbo
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from agent.core.infrastructure.config import get_settings
from agent.core.performance.model_router import ModelTier, estimate_cost

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    """Token 用量记录"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    model: str = ""
    tier: str = ""


@dataclass
class BudgetConfig:
    """预算配置

    多维度预算体系：
      - 用户维度: 每用户每天 Token 消耗上限
      - 会话维度: 单会话 Token 消耗上限
      - 全局维度: 平台整体日消耗上限
      - 租户维度: 每租户每天 Token 消耗上限
      - Agent 维度: 单次 Agent 调用 Token 消耗上限
    """

    user_daily_budget: int = 500000
    session_budget: int = 100000
    global_daily_budget: int = 50000000
    enable_auto_downgrade: bool = True
    tenant_daily_budget: int = 5000000
    agent_per_call_budget: int = 50000


@dataclass
class UsageRecord:
    """用量记录（存储在 Redis 中）"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    call_count: int = 0


class TokenBudgetManager:
    """Token 预算管理器

    追踪 Token 消耗，执行预算检查和自动降级。
    用量数据存储在 Redis 中，支持分布式部署。
    """

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self._config = config or BudgetConfig()
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端（使用统一连接管理器）"""
        if self._redis is None:
            try:
                from agent.core.infrastructure.redis_manager import get_redis_client
                self._redis = await get_redis_client()
            except Exception as e:
                logger.warning("Token 预算管理器 Redis 连接失败: %s", e)
                return None
        return self._redis

    async def record_usage(
        self,
        user_id: str,
        session_id: str,
        model: str,
        tier: str,
        prompt_tokens: int,
        completion_tokens: int,
        tenant_id: str = "",
        agent_name: str = "",
    ) -> TokenUsage:
        """记录 Token 用量

        支持多维度追踪：用户、会话、租户、Agent。

        Args:
            user_id: 用户ID
            session_id: 会话ID
            model: 模型名称
            tier: 模型级别
            prompt_tokens: 输入 Token 数
            completion_tokens: 输出 Token 数
            tenant_id: 租户ID
            agent_name: Agent 名称

        Returns:
            TokenUsage 记录
        """
        total = prompt_tokens + completion_tokens
        model_tier = ModelTier(tier) if tier in [t.value for t in ModelTier] else ModelTier.PLUS
        cost = estimate_cost(model_tier, prompt_tokens, completion_tokens)

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost=cost,
            model=model,
            tier=tier,
        )

        # 记录到 Prometheus
        try:
            from observability.metrics import record_llm_usage
            record_llm_usage(model, prompt_tokens, completion_tokens)
        except Exception as e:
            logger.debug("操作失败，已忽略: %s", e)

        # 异步更新 Redis 中的用量统计
        await self._update_usage_stats(user_id, session_id, usage, tenant_id, agent_name)

        return usage

    async def _update_usage_stats(
        self,
        user_id: str,
        session_id: str,
        usage: TokenUsage,
        tenant_id: str = "",
        agent_name: str = "",
    ) -> None:
        """更新 Redis 中的用量统计

        使用 Pipeline 批量提交所有 Redis 命令，将多次网络往返减少为一次。

        多维度统计：
          - 用户维度: token_usage:user:{user_id}:{date}
          - 会话维度: token_usage:session:{session_id}
          - 全局维度: token_usage:global:{date}
          - 租户维度: token_usage:tenant:{tenant_id}:{date}
          - Agent 维度: token_usage:agent:{agent_name}:{date}
        """
        redis = await self._get_redis()
        if redis is None:
            return

        try:
            today = time.strftime("%Y-%m-%d")
            two_days = 86400 * 2
            one_day = 86400

            async with redis.pipeline(transaction=False) as pipe:
                # 用户日用量
                user_key = f"token_usage:user:{user_id}:{today}"
                pipe.hincrby(user_key, "prompt_tokens", usage.prompt_tokens)
                pipe.hincrby(user_key, "completion_tokens", usage.completion_tokens)
                pipe.hincrby(user_key, "total_tokens", usage.total_tokens)
                pipe.hincrbyfloat(user_key, "cost", usage.cost)
                pipe.hincrby(user_key, "call_count", 1)
                pipe.expire(user_key, two_days)

                # 会话用量
                session_key = f"token_usage:session:{session_id}"
                pipe.hincrby(session_key, "prompt_tokens", usage.prompt_tokens)
                pipe.hincrby(session_key, "completion_tokens", usage.completion_tokens)
                pipe.hincrby(session_key, "total_tokens", usage.total_tokens)
                pipe.hincrbyfloat(session_key, "cost", usage.cost)
                pipe.hincrby(session_key, "call_count", 1)
                pipe.expire(session_key, one_day)

                # 全局日用量
                global_key = f"token_usage:global:{today}"
                pipe.hincrby(global_key, "total_tokens", usage.total_tokens)
                pipe.hincrbyfloat(global_key, "cost", usage.cost)
                pipe.expire(global_key, two_days)

                # 租户日用量
                if tenant_id:
                    tenant_key = f"token_usage:tenant:{tenant_id}:{today}"
                    pipe.hincrby(tenant_key, "total_tokens", usage.total_tokens)
                    pipe.hincrbyfloat(tenant_key, "cost", usage.cost)
                    pipe.hincrby(tenant_key, "call_count", 1)
                    pipe.expire(tenant_key, two_days)

                # Agent 日用量
                if agent_name:
                    agent_key = f"token_usage:agent:{agent_name}:{today}"
                    pipe.hincrby(agent_key, "total_tokens", usage.total_tokens)
                    pipe.hincrbyfloat(agent_key, "cost", usage.cost)
                    pipe.hincrby(agent_key, "call_count", 1)
                    pipe.expire(agent_key, two_days)

                await pipe.execute()

        except Exception as e:
            logger.warning("更新 Token 用量统计失败: %s", e)

    async def check_budget(
        self,
        user_id: str,
        session_id: str,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        """检查预算是否充足

        使用 Pipeline 批量读取多个维度的用量数据，减少网络往返。

        多维度预算检查：用户、会话、全局、租户。

        Args:
            user_id: 用户ID
            session_id: 会话ID
            tenant_id: 租户ID

        Returns:
            预算检查结果，包含是否超预算和建议的模型级别
        """
        redis = await self._get_redis()
        today = time.strftime("%Y-%m-%d")

        user_key = f"token_usage:user:{user_id}:{today}"
        session_key = f"token_usage:session:{session_id}"
        global_key = f"token_usage:global:{today}"

        # 使用 Pipeline 批量读取所有维度的用量
        user_usage = UsageRecord()
        session_usage = UsageRecord()
        global_usage = UsageRecord()
        tenant_usage = UsageRecord()

        if redis:
            try:
                keys_to_fetch = [user_key, session_key, global_key]
                tenant_key = ""
                if tenant_id:
                    tenant_key = f"token_usage:tenant:{tenant_id}:{today}"
                    keys_to_fetch.append(tenant_key)

                async with redis.pipeline(transaction=False) as pipe:
                    for key in keys_to_fetch:
                        pipe.hgetall(key)
                    results = await pipe.execute()

                user_usage = self._parse_usage_record(results[0] if len(results) > 0 else {})
                session_usage = self._parse_usage_record(results[1] if len(results) > 1 else {})
                global_usage = self._parse_usage_record(results[2] if len(results) > 2 else {})
                if tenant_id and len(results) > 3:
                    tenant_usage = self._parse_usage_record(results[3])
            except Exception as e:
                logger.warning("批量读取 Token 用量失败: %s", e)

        user_exceeded = user_usage.total_tokens >= self._config.user_daily_budget
        session_exceeded = session_usage.total_tokens >= self._config.session_budget
        global_exceeded = global_usage.total_tokens >= self._config.global_daily_budget

        # 租户预算检查
        tenant_exceeded = False
        tenant_usage_data: dict[str, Any] = {}
        if tenant_id:
            tenant_exceeded = tenant_usage.total_tokens >= self._config.tenant_daily_budget
            tenant_usage_data = {
                "total_tokens": tenant_usage.total_tokens,
                "cost": round(tenant_usage.cost, 4),
                "budget": self._config.tenant_daily_budget,
                "remaining": max(0, self._config.tenant_daily_budget - tenant_usage.total_tokens),
            }

        # 确定建议的模型级别（自动降级）
        suggested_tier = "max"
        if self._config.enable_auto_downgrade:
            if user_exceeded or global_exceeded or tenant_exceeded:
                suggested_tier = "turbo"
            elif session_exceeded:
                suggested_tier = "plus"

        result: dict[str, Any] = {
            "user_exceeded": user_exceeded,
            "session_exceeded": session_exceeded,
            "global_exceeded": global_exceeded,
            "tenant_exceeded": tenant_exceeded,
            "any_exceeded": user_exceeded or session_exceeded or global_exceeded or tenant_exceeded,
            "suggested_tier": suggested_tier,
            "user_usage": {
                "total_tokens": user_usage.total_tokens,
                "cost": round(user_usage.cost, 4),
                "budget": self._config.user_daily_budget,
                "remaining": max(0, self._config.user_daily_budget - user_usage.total_tokens),
            },
            "session_usage": {
                "total_tokens": session_usage.total_tokens,
                "cost": round(session_usage.cost, 4),
                "budget": self._config.session_budget,
                "remaining": max(0, self._config.session_budget - session_usage.total_tokens),
            },
        }

        if tenant_usage_data:
            result["tenant_usage"] = tenant_usage_data

        return result

    @staticmethod
    def _parse_usage_record(data: dict[str, Any]) -> "UsageRecord":
        """解析 Redis hgetall 返回的用量数据

        Args:
            data: Redis 哈希字段字典

        Returns:
            UsageRecord 实例
        """
        if not data:
            return UsageRecord()
        try:
            return UsageRecord(
                prompt_tokens=int(data.get("prompt_tokens", 0)),
                completion_tokens=int(data.get("completion_tokens", 0)),
                total_tokens=int(data.get("total_tokens", 0)),
                cost=float(data.get("cost", 0)),
                call_count=int(data.get("call_count", 0)),
            )
        except Exception:
            return UsageRecord()

    async def get_user_daily_usage(self, user_id: str) -> dict[str, Any]:
        """获取用户当日用量统计

        Args:
            user_id: 用户ID

        Returns:
            用量统计字典
        """
        redis = await self._get_redis()
        today = time.strftime("%Y-%m-%d")
        usage = await self._get_usage(redis, f"token_usage:user:{user_id}:{today}")

        return {
            "user_id": user_id,
            "date": today,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost": round(usage.cost, 4),
            "call_count": usage.call_count,
            "budget": self._config.user_daily_budget,
            "remaining": max(0, self._config.user_daily_budget - usage.total_tokens),
        }

    async def get_tenant_daily_usage(self, tenant_id: str) -> dict[str, Any]:
        """获取租户当日用量统计

        Args:
            tenant_id: 租户ID

        Returns:
            租户用量统计字典
        """
        redis = await self._get_redis()
        today = time.strftime("%Y-%m-%d")
        usage = await self._get_usage(redis, f"token_usage:tenant:{tenant_id}:{today}")

        return {
            "tenant_id": tenant_id,
            "date": today,
            "total_tokens": usage.total_tokens,
            "cost": round(usage.cost, 4),
            "call_count": usage.call_count,
            "budget": self._config.tenant_daily_budget,
            "remaining": max(0, self._config.tenant_daily_budget - usage.total_tokens),
        }

    async def get_agent_daily_usage(self, agent_name: str) -> dict[str, Any]:
        """获取 Agent 当日用量统计

        Args:
            agent_name: Agent 名称

        Returns:
            Agent 用量统计字典
        """
        redis = await self._get_redis()
        today = time.strftime("%Y-%m-%d")
        usage = await self._get_usage(redis, f"token_usage:agent:{agent_name}:{today}")

        return {
            "agent_name": agent_name,
            "date": today,
            "total_tokens": usage.total_tokens,
            "cost": round(usage.cost, 4),
            "call_count": usage.call_count,
            "per_call_budget": self._config.agent_per_call_budget,
        }

    async def get_cost_report(self, date: str = "") -> dict[str, Any]:
        """获取成本报表

        汇总全局、按 Agent 分类的成本数据。

        Args:
            date: 日期（默认今天）

        Returns:
            成本报表字典
        """
        redis = await self._get_redis()
        if not date:
            date = time.strftime("%Y-%m-%d")

        global_usage = await self._get_usage(redis, f"token_usage:global:{date}")

        # 扫描所有 Agent 的用量
        agent_reports = {}
        if redis:
            try:
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(
                        cursor, match=f"token_usage:agent:*:{date}", count=100,
                    )
                    for key in keys:
                        parts = key.split(":")
                        if len(parts) >= 4:
                            agent_name = parts[2]
                            agent_usage = await self._get_usage(redis, key)
                            agent_reports[agent_name] = {
                                "total_tokens": agent_usage.total_tokens,
                                "cost": round(agent_usage.cost, 4),
                                "call_count": agent_usage.call_count,
                            }
                    if cursor == 0:
                        break
            except Exception as e:
                logger.warning("扫描 Agent 用量失败: %s", e)

        return {
            "date": date,
            "global": {
                "total_tokens": global_usage.total_tokens,
                "cost": round(global_usage.cost, 4),
            },
            "agents": agent_reports,
        }

    async def _get_usage(self, redis: Any, key: str) -> UsageRecord:
        """从 Redis 获取用量记录"""
        if redis is None:
            return UsageRecord()

        try:
            data = await redis.hgetall(key)
            if not data:
                return UsageRecord()

            return UsageRecord(
                prompt_tokens=int(data.get("prompt_tokens", 0)),
                completion_tokens=int(data.get("completion_tokens", 0)),
                total_tokens=int(data.get("total_tokens", 0)),
                cost=float(data.get("cost", 0)),
                call_count=int(data.get("call_count", 0)),
            )
        except Exception:
            return UsageRecord()

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# 全局 Token 预算管理器
_budget_manager: TokenBudgetManager | None = None


def get_token_budget_manager() -> TokenBudgetManager:
    """获取全局 Token 预算管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _budget_manager
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_token_budget_manager() is not None:
            return ctx.get_token_budget_manager()
    except Exception as e:
        logger.debug("操作失败，已忽略: %s", e)
    if _budget_manager is None:
        _budget_manager = TokenBudgetManager()
    return _budget_manager
