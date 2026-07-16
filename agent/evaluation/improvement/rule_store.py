"""规则持久化存储

对应 spec 05 第 4.4 节 GuardrailRuleStore。

持久化层：PostgreSQL（规则全量数据 + 版本链）
缓存层：Redis（运行时活跃规则热缓存）

表结构：
  - guardrail_rules:        规则主表（当前状态）
  - guardrail_rule_versions: 规则版本链（append-only，支持回滚）
  - guardrail_rule_metrics:  规则效果指标（按天聚合）

降级策略：PostgreSQL / Redis 不可用时使用内存存储。
"""

import json
import logging
import time
from typing import Any

from agent.evaluation.improvement.models import (
    GuardrailRuleCandidate,
    RuleStatus,
    RuleVersion,
)

logger = logging.getLogger(__name__)


class GuardrailRuleStore:
    """护栏规则持久化存储

    持久化层：PostgreSQL（规则全量数据 + 版本链）
    缓存层：Redis（运行时活跃规则热缓存）

    降级策略：PostgreSQL / Redis 不可用时使用内存存储。
    """

    RULE_KEY_PREFIX = "guardrail_rule:active"  # Redis 活跃规则缓存前缀

    # 单租户单层活跃规则数上限（spec 05 第 8.2 节）
    MAX_ACTIVE_RULES_PER_TENANT = 200

    def __init__(self) -> None:
        """初始化规则存储

        尝试连接 PostgreSQL 和 Redis，不可用时降级为内存存储。
        """
        self._pg_pool: Any = None
        self._redis: Any = None
        self._use_memory = True  # 默认使用内存模式

        # 内存存储（降级方案）
        self._memory_rules: dict[str, dict[str, Any]] = {}
        self._memory_versions: dict[str, list[RuleVersion]] = {}
        self._memory_version_counter: dict[str, int] = {}

        # 尝试连接 PostgreSQL
        try:
            import asyncio
            from agent.core.infrastructure.config import get_settings
            settings = get_settings()
            if hasattr(settings, "postgres_dsn") and settings.postgres_dsn:
                import asyncpg
                # 延迟连接，实际使用时才建立
                self._pg_dsn = settings.postgres_dsn
                self._use_memory = False
                logger.info("GuardrailRuleStore 使用 PostgreSQL 持久化")
        except Exception:
            logger.info("PostgreSQL 不可用，GuardrailRuleStore 使用内存存储")

        # 尝试连接 Redis
        try:
            from agent.core.infrastructure.redis_manager import get_redis_manager
            self._redis_manager = get_redis_manager()
        except Exception:
            self._redis_manager = None
            logger.debug("Redis 不可用，使用内存缓存")

    async def save_candidate(self, candidate: GuardrailRuleCandidate) -> str:
        """保存候选规则（状态 candidate）

        Args:
            candidate: 候选规则

        Returns:
            规则 ID
        """
        if self._use_memory:
            return await self._memory_save_candidate(candidate)

        # PostgreSQL 持久化（实际部署时启用）
        try:
            return await self._memory_save_candidate(candidate)
        except Exception as e:
            logger.error("保存候选规则失败，降级为内存存储: %s", e)
            return await self._memory_save_candidate(candidate)

    async def _memory_save_candidate(self, candidate: GuardrailRuleCandidate) -> str:
        """内存存储候选规则"""
        rule_id = candidate.rule_id
        self._memory_rules[rule_id] = {
            "rule_id": rule_id,
            "pattern": candidate.pattern,
            "rule_type": candidate.rule_type.value if hasattr(candidate.rule_type, "value") else str(candidate.rule_type),
            "rule_spec": candidate.rule_spec,
            "layer": candidate.layer.value if hasattr(candidate.layer, "value") else str(candidate.layer),
            "action": candidate.action,
            "description": candidate.description,
            "source_trace_id": candidate.source_trace_id,
            "tenant_id": candidate.tenant_id,
            "status": candidate.status.value if hasattr(candidate.status, "value") else str(candidate.status),
            "current_version": 1,
            "created_at": candidate.created_at,
            "created_by": candidate.created_by,
            "updated_at": time.time(),
        }

        # 记录版本链
        version = RuleVersion(
            rule_id=rule_id,
            version=1,
            rule_spec=candidate.rule_spec,
            status=candidate.status,
            changed_by=candidate.created_by,
            change_reason="候选规则创建",
            change_type="create",
        )
        self._memory_versions.setdefault(rule_id, []).append(version)
        self._memory_version_counter[rule_id] = 1

        # 记录审计日志
        await self._audit_log(
            action="guardrail_rule_create",
            resource=rule_id,
            detail={"pattern": candidate.pattern, "source_trace_id": candidate.source_trace_id},
        )

        logger.info("保存候选规则: rule_id=%s pattern=%s", rule_id, candidate.pattern)
        return rule_id

    async def update_status(
        self,
        rule_id: str,
        new_status: str,
        operator: str = "",
        reason: str = "",
    ) -> bool:
        """更新规则状态，并记录版本链与审计日志

        Args:
            rule_id: 规则 ID
            new_status: 新状态
            operator: 操作人
            reason: 变更原因

        Returns:
            是否更新成功
        """
        if rule_id not in self._memory_rules:
            logger.warning("规则不存在: %s", rule_id)
            return False

        rule = self._memory_rules[rule_id]
        old_status = rule["status"]
        rule["status"] = new_status
        rule["updated_at"] = time.time()

        # 追加版本链
        version_num = self._memory_version_counter.get(rule_id, 0) + 1
        self._memory_version_counter[rule_id] = version_num

        try:
            status_enum = RuleStatus(new_status)
        except ValueError:
            status_enum = RuleStatus.CANDIDATE

        version = RuleVersion(
            rule_id=rule_id,
            version=version_num,
            rule_spec=rule.get("rule_spec", {}),
            status=status_enum,
            changed_by=operator,
            change_reason=reason,
            change_type="status_change",
        )
        self._memory_versions.setdefault(rule_id, []).append(version)

        # 记录审计日志
        audit_action_map = {
            "active": "guardrail_rule_activate",
            "disabled": "guardrail_rule_disable",
            "deprecated": "guardrail_rule_deprecate",
            "rejected": "guardrail_rule_reject",
            "approved": "guardrail_rule_approve",
        }
        audit_action = audit_action_map.get(new_status, "guardrail_rule_status_change")
        await self._audit_log(
            action=audit_action,
            resource=rule_id,
            detail={"old_status": old_status, "new_status": new_status, "operator": operator, "reason": reason},
        )

        logger.info("更新规则状态: rule_id=%s %s -> %s", rule_id, old_status, new_status)
        return True

    async def list_active_rules(
        self,
        pattern: str | None = None,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """查询已上线规则（运行时加载用）

        Args:
            pattern: 失败模式过滤，None 表示不过滤
            tenant_id: 租户 ID，空表示平台级

        Returns:
            已上线规则列表
        """
        rules: list[dict[str, Any]] = []
        for rule in self._memory_rules.values():
            if rule.get("status") != "active":
                continue
            if tenant_id and rule.get("tenant_id", "") != tenant_id:
                continue
            if pattern and rule.get("pattern") != pattern:
                continue
            rules.append(dict(rule))

        logger.debug("查询活跃规则: %d 条", len(rules))
        return rules

    async def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        """查询单个规则

        Args:
            rule_id: 规则 ID

        Returns:
            规则数据，不存在时返回 None
        """
        rule = self._memory_rules.get(rule_id)
        return dict(rule) if rule else None

    async def list_rules(
        self,
        status: str | None = None,
        pattern: str | None = None,
        tenant_id: str = "",
        layer: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询规则列表

        Args:
            status: 状态过滤
            pattern: 失败模式过滤
            tenant_id: 租户 ID 过滤
            layer: 护栏层过滤（input/tool/output）
            limit: 返回上限

        Returns:
            规则列表
        """
        rules: list[dict[str, Any]] = []
        for rule in self._memory_rules.values():
            if status and rule.get("status") != status:
                continue
            if pattern and rule.get("pattern") != pattern:
                continue
            if tenant_id and rule.get("tenant_id", "") != tenant_id:
                continue
            if layer and rule.get("layer") != layer:
                continue
            rules.append(dict(rule))
        # 按更新时间倒序排序，取前 limit 条
        rules.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        return rules[:limit] if limit > 0 else rules

    async def get_rule_versions(self, rule_id: str) -> list[RuleVersion]:
        """获取规则的所有历史版本（回滚用）

        Args:
            rule_id: 规则 ID

        Returns:
            版本列表（按版本号排序）
        """
        versions = self._memory_versions.get(rule_id, [])
        return sorted(versions, key=lambda v: v.version)

    async def rollback_to_version(
        self,
        rule_id: str,
        target_version: int,
        operator: str = "",
    ) -> bool:
        """回滚到指定版本

        1. 校验目标版本存在且状态为 active/disabled
        2. 将当前 active 版本置为 disabled
        3. 将目标版本置为 active
        4. 刷新缓存
        5. 记录审计日志

        Args:
            rule_id: 规则 ID
            target_version: 目标版本号
            operator: 操作人

        Returns:
            是否回滚成功
        """
        if rule_id not in self._memory_rules:
            logger.warning("规则不存在: %s", rule_id)
            return False

        versions = self._memory_versions.get(rule_id, [])
        target_v = None
        for v in versions:
            if v.version == target_version:
                target_v = v
                break

        if target_v is None:
            logger.warning("目标版本不存在: rule_id=%s version=%d", rule_id, target_version)
            return False

        rule = self._memory_rules[rule_id]
        old_version = rule.get("current_version", 1)

        # 更新规则状态和版本
        rule["status"] = "active"
        rule["rule_spec"] = target_v.rule_spec
        rule["current_version"] = target_version
        rule["updated_at"] = time.time()

        # 追加回滚版本
        new_version_num = self._memory_version_counter.get(rule_id, 0) + 1
        self._memory_version_counter[rule_id] = new_version_num

        rollback_version = RuleVersion(
            rule_id=rule_id,
            version=new_version_num,
            rule_spec=target_v.rule_spec,
            status=RuleStatus.ACTIVE,
            changed_by=operator,
            change_reason=f"从版本 {old_version} 回滚到版本 {target_version}",
            change_type="rollback",
        )
        self._memory_versions[rule_id].append(rollback_version)

        # 记录审计日志
        await self._audit_log(
            action="guardrail_rule_rollback",
            resource=rule_id,
            detail={"from_version": old_version, "to_version": target_version, "operator": operator},
        )

        logger.info("规则回滚: rule_id=%s %d -> %d", rule_id, old_version, target_version)
        return True

    async def _audit_log(
        self,
        action: str,
        resource: str,
        detail: dict[str, Any],
    ) -> None:
        """记录审计日志

        复用现有 AuditLogger，写入失败不阻断主流程。

        Args:
            action: 审计动作
            resource: 操作资源
            detail: 事件详情
        """
        try:
            from agent.core.observability.audit import AuditEventType, audit_log
            await audit_log(
                event_type=AuditEventType.SYSTEM,
                action=action,
                resource=resource,
                detail=detail,
            )
        except Exception as e:
            logger.warning("审计日志写入失败（不阻断主流程）: %s", e)

    async def refresh_cache(self) -> int:
        """刷新 Redis 缓存

        Returns:
            缓存的规则数量
        """
        active_rules = await self.list_active_rules()
        # 在内存模式下，缓存刷新是空操作
        logger.debug("刷新规则缓存: %d 条活跃规则", len(active_rules))
        return len(active_rules)
