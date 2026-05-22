"""原生工具注册中心

提供原生工具的注册、发现、加载和管理能力，支持：
  - 立即注册：无外部依赖的瞬时工具
  - 懒注册：依赖 LLM 客户端的慢速工具，避免启动时全量初始化
  - 动态启禁：运行时控制工具可用性
  - 关键词搜索：按名称、描述、分类匹配工具

全局入口：
  get_native_tool_registry() -> 单例模式，首次调用时触发自动注册

注册流程：
  -------------------------------------------------------------------------
  1. 首次调用 get_native_tool_registry() 时创建单例
  2. 触发 _auto_register_all()，仅注册工厂函数，不实例化工具
  3. 各工具模块在导入时调用 register() 或 register_lazy()
  4. Agent 创建时通过 load_tools() 批量加载，触发懒实例化
  -------------------------------------------------------------------------
"""

import logging
from typing import Any, Callable

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel

logger = logging.getLogger(__name__)


class NativeToolRegistry:
    """原生工具注册中心

    管理所有原生工具的注册信息，支持立即注册和懒注册两种模式。

    立即注册 vs 懒注册：
    -------------------------------------------------------------------------
    立即注册（register）：
      - 适用于无外部依赖的瞬时工具
      - 注册时即创建 FunctionTool 实例
      - 典型工具：时间查询、文本格式转换

    懒注册（register_lazy）：
      - 适用于依赖 LLM 客户端或数据库的慢速工具
      - 注册时仅保存工厂函数，不创建实例
      - 首次 get() 或 load_tools() 时触发实例化
      - 典型工具：文档摘要、RAG 检索、报告生成
    -------------------------------------------------------------------------
    """

    def __init__(self) -> None:
        self._tools: dict[str, FunctionTool] = {}
        self._metas: dict[str, NativeToolMeta] = {}
        self._factories: dict[str, Callable[[], FunctionTool]] = {}
        self._instantiated: set[str] = set()
        self._auto_registered: bool = False

    def register(self, tool: FunctionTool, meta: NativeToolMeta) -> None:
        """立即注册工具

        注册时即创建 FunctionTool 实例，适用于无外部依赖的瞬时工具。

        Args:
            tool: AutoGen FunctionTool 实例
            meta: 工具元数据

        Raises:
            ValueError: 同名工具重复注册
        """
        if meta.name in self._metas:
            raise ValueError(f"工具 {meta.name} 已注册，不可重复注册")
        self._tools[meta.name] = tool
        self._metas[meta.name] = meta
        self._instantiated.add(meta.name)
        logger.debug("立即注册工具: %s (category=%s, tier=%s)", meta.name, meta.category, meta.latency_tier.value)

    def register_lazy(self, name: str, factory: Callable[[], FunctionTool], meta: NativeToolMeta) -> None:
        """懒注册工具

        注册时仅保存工厂函数，不创建实例。首次 get() 或 load_tools() 时触发实例化。
        适用于依赖 LLM 客户端或数据库的慢速工具，避免启动时全量初始化。

        Args:
            name: 工具名称
            factory: 工具工厂函数，调用时返回 FunctionTool 实例
            meta: 工具元数据

        Raises:
            ValueError: 同名工具重复注册
        """
        if name in self._metas:
            raise ValueError(f"工具 {name} 已注册，不可重复注册")
        self._factories[name] = factory
        self._metas[name] = meta
        logger.debug("懒注册工具: %s (category=%s, tier=%s)", name, meta.category, meta.latency_tier.value)

    def get(self, name: str) -> FunctionTool | None:
        """获取工具实例

        对于懒注册的工具，首次调用时触发实例化。

        Args:
            name: 工具名称

        Returns:
            FunctionTool 实例，未找到时返回 None
        """
        if name in self._tools:
            return self._tools[name]

        if name in self._factories and name not in self._instantiated:
            factory = self._factories[name]
            try:
                tool = factory()
                self._tools[name] = tool
                self._instantiated.add(name)
                logger.debug("懒实例化工具: %s", name)
                return tool
            except Exception as e:
                logger.error("懒实例化工具 %s 失败: %s", name, e)
                return None

        return None

    def get_meta(self, name: str) -> NativeToolMeta | None:
        """获取工具元数据

        不触发懒实例化，仅返回元数据信息。

        Args:
            name: 工具名称

        Returns:
            NativeToolMeta 实例，未找到时返回 None
        """
        return self._metas.get(name)

    def list_tools(
        self,
        category: str | None = None,
        latency_tier: LatencyTier | None = None,
    ) -> list[NativeToolMeta]:
        """列出已注册工具的元数据

        支持按分类和延迟分层过滤。

        Args:
            category: 按分类过滤（可选）
            latency_tier: 按延迟分层过滤（可选）

        Returns:
            工具元数据列表
        """
        result = []
        for meta in self._metas.values():
            if not meta.enabled:
                continue
            if category and meta.category != category:
                continue
            if latency_tier and meta.latency_tier != latency_tier:
                continue
            result.append(meta)
        return result

    def load_tools(self, names: list[str]) -> list[FunctionTool]:
        """批量加载工具

        触发懒实例化，返回 FunctionTool 列表。
        跳过已禁用的工具。

        Args:
            names: 工具名称列表

        Returns:
            FunctionTool 实例列表
        """
        tools: list[FunctionTool] = []
        for name in names:
            meta = self._metas.get(name)
            if meta is None:
                logger.warning("工具 %s 未注册，跳过", name)
                continue
            if not meta.enabled:
                logger.debug("工具 %s 已禁用，跳过", name)
                continue
            tool = self.get(name)
            if tool is not None:
                tools.append(tool)
            else:
                logger.warning("工具 %s 加载失败，跳过", name)
        return tools

    def enable(self, name: str) -> bool:
        """启用工具

        Args:
            name: 工具名称

        Returns:
            是否操作成功
        """
        meta = self._metas.get(name)
        if meta is None:
            logger.warning("工具 %s 未注册，无法启用", name)
            return False
        meta.enabled = True
        logger.info("启用工具: %s", name)
        return True

    def disable(self, name: str) -> bool:
        """禁用工具

        运行时生效，不影响已创建的 Agent 实例。

        Args:
            name: 工具名称

        Returns:
            是否操作成功
        """
        meta = self._metas.get(name)
        if meta is None:
            logger.warning("工具 %s 未注册，无法禁用", name)
            return False
        meta.enabled = False
        logger.info("禁用工具: %s", name)
        return True

    def search(self, keyword: str) -> list[NativeToolMeta]:
        """关键词搜索工具

        匹配 name、display_name、description、category 字段。

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的工具元数据列表
        """
        keyword_lower = keyword.lower()
        result = []
        for meta in self._metas.values():
            if not meta.enabled:
                continue
            searchable = " ".join([
                meta.name,
                meta.display_name,
                meta.description,
                meta.category,
                " ".join(meta.tags),
            ]).lower()
            if keyword_lower in searchable:
                result.append(meta)
        return result


_registry: NativeToolRegistry | None = None


def _auto_register_all(registry: NativeToolRegistry) -> None:
    """自动注册所有原生工具

    仅注册工厂函数，不实例化工具本身。
    各工具模块的 register_all 函数负责具体的注册逻辑。

    Args:
        registry: 工具注册中心实例
    """
    from agent.tools.time_tools import register_all as register_time_tools
    from agent.tools.session_tools import register_all as register_session_tools
    from agent.tools.data_tools import register_all as register_data_tools
    from agent.tools.document_tools import register_all as register_document_tools
    from agent.tools.search_tools import register_all as register_search_tools
    from agent.tools.text_tools import register_all as register_text_tools
    from agent.tools.report_tools import register_all as register_report_tools
    from agent.tools.multimodal_tools import register_all as register_multimodal_tools
    from agent.tools.rag_tools import register_all as register_rag_tools
    from agent.tools.skill_tools import register_all as register_skill_tools

    register_time_tools(registry)
    register_session_tools(registry)
    register_data_tools(registry)
    register_document_tools(registry)
    register_search_tools(registry)
    register_text_tools(registry)
    register_report_tools(registry)
    register_multimodal_tools(registry)
    register_rag_tools(registry)
    register_skill_tools(registry)

    logger.info("原生工具自动注册完成")


def get_native_tool_registry() -> NativeToolRegistry:
    """获取原生工具注册中心单例

    首次调用时触发自动注册，后续调用直接返回缓存实例。

    Returns:
        NativeToolRegistry 实例
    """
    global _registry
    if _registry is None:
        _registry = NativeToolRegistry()
        _auto_register_all(_registry)
    return _registry
