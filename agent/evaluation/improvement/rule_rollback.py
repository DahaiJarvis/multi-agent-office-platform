"""规则回滚器

对应 spec 05 第 4.6 节 RuleRollback。

支持按版本回滚已上线规则。触发场景：
  1. 误报率回升超过阈值（RuleMetricsCollector 触发）
  2. 人工主动回滚（运维 API 调用）
  3. 规则上线后引入评估套件回归

回滚操作必须记录审计日志。
"""

import logging

from agent.evaluation.improvement.rule_store import GuardrailRuleStore

logger = logging.getLogger(__name__)


class RuleRollback:
    """规则回滚器

    支持按版本回滚已上线规则。触发场景：
      1. 误报率回升超过阈值（RuleMetricsCollector 触发）
      2. 人工主动回滚（运维 API 调用）
      3. 规则上线后引入评估套件回归

    回滚操作必须记录审计日志。

    使用示例：
        store = GuardrailRuleStore()
        rollback = RuleRollback(store)
        success = await rollback.rollback("rule-xxx", target_version=2)
    """

    def __init__(self, store: GuardrailRuleStore) -> None:
        """初始化规则回滚器

        Args:
            store: 规则持久化存储
        """
        self._store = store

    async def rollback(
        self,
        rule_id: str,
        target_version: int | None = None,
        operator: str = "system",
        reason: str = "",
    ) -> bool:
        """回滚规则

        Args:
            rule_id: 规则 ID
            target_version: 目标版本，None 表示回滚到上一活跃版本
            operator: 操作人（system 表示自动触发）
            reason: 回滚原因

        Returns:
            是否回滚成功
        """
        # 获取规则信息
        rule = await self._store.get_rule(rule_id)
        if rule is None:
            logger.warning("回滚失败：规则不存在 rule_id=%s", rule_id)
            return False

        current_version = rule.get("current_version", 1)

        # 如果未指定目标版本，查找上一活跃版本
        if target_version is None:
            target_version = await self._find_previous_active_version(rule_id, current_version)
            if target_version is None:
                logger.warning("回滚失败：找不到可回滚的版本 rule_id=%s", rule_id)
                return False

        # 目标版本与当前版本相同时不回滚
        if target_version == current_version:
            logger.info("目标版本与当前版本相同，跳过回滚: rule_id=%s version=%d", rule_id, target_version)
            return True

        # 执行回滚
        success = await self._store.rollback_to_version(
            rule_id=rule_id,
            target_version=target_version,
            operator=operator,
        )

        if success:
            logger.info(
                "规则回滚成功: rule_id=%s %d -> %d operator=%s reason=%s",
                rule_id, current_version, target_version, operator, reason,
            )
            # 刷新缓存
            await self._store.refresh_cache()
        else:
            logger.error("规则回滚失败: rule_id=%s target_version=%d", rule_id, target_version)

        return success

    async def rollback_and_disable(
        self,
        rule_id: str,
        operator: str = "system",
        reason: str = "",
    ) -> bool:
        """回滚失败时直接禁用规则

        Args:
            rule_id: 规则 ID
            operator: 操作人
            reason: 回滚原因

        Returns:
            是否操作成功
        """
        # 先尝试回滚
        success = await self.rollback(rule_id, operator=operator, reason=reason)

        if not success:
            # 回滚失败，直接禁用
            logger.warning("回滚失败，直接禁用规则: rule_id=%s", rule_id)
            success = await self._store.update_status(
                rule_id=rule_id,
                new_status="disabled",
                operator=operator,
                reason=f"回滚失败后禁用: {reason}",
            )
            if success:
                await self._store.refresh_cache()

        return success

    async def _find_previous_active_version(
        self,
        rule_id: str,
        current_version: int,
    ) -> int | None:
        """查找上一活跃版本

        Args:
            rule_id: 规则 ID
            current_version: 当前版本号

        Returns:
            上一活跃版本号，找不到时返回 None
        """
        versions = await self._store.get_rule_versions(rule_id)

        # 从当前版本往前找，找到第一个非当前版本的 active 或 disabled 版本
        for version in reversed(versions):
            if version.version >= current_version:
                continue
            status_str = version.status.value if hasattr(version.status, "value") else str(version.status)
            if status_str in ("active", "disabled"):
                return version.version

        return None
