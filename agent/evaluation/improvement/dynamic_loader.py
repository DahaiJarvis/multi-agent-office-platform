"""动态规则加载器

对应 spec 05 第 4.5 节 DynamicRuleLoader。

将已上线规则加载到运行时护栏，无需重启服务。
采用 Redis 缓存 + 定时刷新策略，规则变更后秒级生效。

与 guardrails.py 的集成方式：
  在 check_input_guardrails / check_output_guardrails 入口处
  新增 dynamic_rules = DynamicRuleLoader.get_active_rules() 调用，
  原有硬编码规则保留不变，动态规则作为补充层执行。
"""

import logging
import time
from typing import Any

from agent.evaluation.improvement.rule_store import GuardrailRuleStore

logger = logging.getLogger(__name__)


class DynamicRuleLoader:
    """动态规则加载器

    将已上线规则加载到运行时护栏，无需重启服务。
    采用缓存 + 定时刷新策略，规则变更后秒级生效。

    使用示例：
        store = GuardrailRuleStore()
        loader = DynamicRuleLoader(store)
        rules = await loader.get_active_rules()
    """

    REFRESH_INTERVAL_SECONDS = 60  # 缓存刷新间隔

    def __init__(self, store: GuardrailRuleStore) -> None:
        """初始化动态规则加载器

        Args:
            store: 规则持久化存储
        """
        self._store = store
        self._cache: list[dict[str, Any]] = []
        self._last_refresh: float = 0.0

    async def get_active_rules(
        self,
        pattern: str | None = None,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """获取当前活跃的动态规则（带缓存）

        缓存有效时直接返回缓存，否则从存储加载。

        Args:
            pattern: 失败模式过滤
            tenant_id: 租户 ID

        Returns:
            活跃规则列表
        """
        # 检查缓存是否有效
        now = time.time()
        if (
            self._cache
            and (now - self._last_refresh) < self.REFRESH_INTERVAL_SECONDS
        ):
            # 从缓存中过滤
            return self._filter_rules(self._cache, pattern, tenant_id)

        # 从存储加载
        await self.refresh()

        return self._filter_rules(self._cache, pattern, tenant_id)

    async def refresh(self) -> int:
        """强制刷新缓存，返回加载数量

        Returns:
            加载的规则数量
        """
        try:
            self._cache = await self._store.list_active_rules()
            self._last_refresh = time.time()
            logger.info("刷新动态规则缓存: %d 条", len(self._cache))
            return len(self._cache)
        except Exception as e:
            logger.warning("刷新动态规则缓存失败: %s", e)
            return len(self._cache)

    def compile_rule(self, rule_spec: dict[str, Any]) -> Any:
        """将规则定义编译为可执行对象（正则 / 函数）

        Args:
            rule_spec: 规则定义

        Returns:
            可执行对象（正则 Pattern 或函数引用）

        Raises:
            ValueError: FUNCTION 规则引用了不在白名单内的函数
        """
        import re

        rule_type = rule_spec.get("rule_type", "regex")

        if rule_type == "regex":
            # 正则规则：编译为 Pattern 对象
            patterns = rule_spec.get("patterns", [])
            if not patterns and rule_spec.get("pattern"):
                patterns = [rule_spec["pattern"]]

            flags_str = rule_spec.get("flags", "IGNORECASE")
            flags = re.IGNORECASE if "IGNORECASE" in flags_str.upper() else 0

            compiled = []
            for pattern in patterns:
                try:
                    compiled.append(re.compile(pattern, flags))
                except re.error as e:
                    logger.warning("正则编译失败 pattern=%s: %s", pattern, e)
            return compiled

        elif rule_type == "keyword":
            # 关键词规则：返回关键词列表
            return rule_spec.get("keywords", [])

        elif rule_type == "function":
            # 函数规则：仅允许预注册函数
            func_name = rule_spec.get("function_name", "")
            _ALLOWED_FUNCTIONS = {
                "check_factuality",
                "check_permission",
                "check_tool_param_combination",
            }
            if func_name not in _ALLOWED_FUNCTIONS:
                raise ValueError(
                    f"FUNCTION 规则引用了不在白名单内的函数: {func_name}"
                )
            return func_name

        elif rule_type == "schema":
            # Schema 规则：返回 schema 定义
            return rule_spec.get("schema", {})

        return None

    def _filter_rules(
        self,
        rules: list[dict[str, Any]],
        pattern: str | None,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """按条件过滤规则

        Args:
            rules: 规则列表
            pattern: 失败模式过滤
            tenant_id: 租户 ID 过滤

        Returns:
            过滤后的规则列表
        """
        result: list[dict[str, Any]] = []
        for rule in rules:
            if pattern and rule.get("pattern") != pattern:
                continue
            if tenant_id and rule.get("tenant_id", "") != tenant_id:
                continue
            result.append(rule)
        return result
