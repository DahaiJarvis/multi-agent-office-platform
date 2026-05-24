"""统一工具加载器

负责将原生工具和 MCP 工具统一加载，为 Agent 提供完整的工具集。

加载流程：
  -------------------------------------------------------------------------
  1. 从 NativeToolRegistry 加载 Agent 绑定的原生工具
  2. 从 mcp_integration 加载 Agent 绑定的 MCP 工具
  3. 合并原生与 MCP 工具，原生工具已带 native_ 前缀，天然避免同名冲突
  4. 检查 schema 兼容性，不兼容时发出 WARNING
  -------------------------------------------------------------------------

Agent 工具绑定关系：
  通过 AGENT_NATIVE_TOOL_BINDINGS 定义每个 Agent 绑定的原生工具列表，
  与 mcp_integration.py 中的 AGENT_TOOL_BINDINGS（MCP 绑定）互补。

动态绑定：
  bind_native_tool() / unbind_native_tool() 支持运行时动态调整绑定关系。
"""

import logging
from typing import Any

from agent.tools.registry import get_native_tool_registry

logger = logging.getLogger(__name__)


AGENT_NATIVE_TOOL_BINDINGS: dict[str, list[str]] = {
    "EmailAgent": ["native_session_history", "native_text_format", "native_session_summary"],
    "ApprovalAgent": ["native_session_history", "native_session_summary"],
    "CalendarAgent": ["native_session_history", "native_current_time", "native_date_calculate", "native_session_summary"],
    "CRMAgent": ["native_session_history", "native_data_query", "native_data_visualize", "native_session_summary"],
    "HRAgent": ["native_session_history", "native_data_query", "native_session_summary"],
    "FinanceAgent": ["native_session_history", "native_data_query", "native_data_visualize", "native_data_export", "native_report_export", "native_session_summary"],
    "KnowledgeAgent": [
        "native_session_history", "native_document_parse", "native_document_summarize",
        "native_document_compare", "native_search_all", "native_search_documents",
        "native_search_knowledge", "native_rag_search", "native_rag_qa",
        "native_report_generate", "native_report_export", "native_image_analyze",
        "native_image_ocr", "native_text_extract",
        "native_skill_load", "native_skill_unload", "native_skill_list",
        "native_session_summary",
    ],
    "OfficeAssistant": [
        "native_session_history", "native_search_all", "native_current_time",
        "native_date_calculate", "native_text_format", "native_text_extract",
        "native_text_translate",
        "native_skill_load", "native_skill_unload", "native_skill_list",
        "native_session_summary",
    ],
    "Reviewer": ["native_session_history", "native_data_query", "native_session_summary"],
}


async def load_all_tools(agent_name: str) -> list[Any]:
    """加载 Agent 的全部工具（原生 + MCP）

    统一加载入口，按命名空间直接拼接原生工具和 MCP 工具。
    原生工具已带 native_ 前缀，MCP 工具保持原名，天然避免同名冲突。

    Args:
        agent_name: Agent 名称，如 "EmailAgent"

    Returns:
        FunctionTool 列表（包含原生工具和 MCP 工具）
    """
    native_tools = _load_native_tools(agent_name)
    mcp_tools = await _load_mcp_tools(agent_name)
    merged = _merge_tools(native_tools, mcp_tools)
    logger.info(
        "Agent %s 加载工具完成: 原生=%d, MCP=%d, 合计=%d",
        agent_name, len(native_tools), len(mcp_tools), len(merged),
    )
    return merged


def _load_native_tools(agent_name: str) -> list[Any]:
    """从 NativeToolRegistry 加载绑定的原生工具

    Args:
        agent_name: Agent 名称

    Returns:
        FunctionTool 列表
    """
    tool_names = AGENT_NATIVE_TOOL_BINDINGS.get(agent_name, [])
    if not tool_names:
        logger.debug("Agent %s 无原生工具绑定", agent_name)
        return []

    registry = get_native_tool_registry()
    tools = registry.load_tools(tool_names)

    if len(tools) < len(tool_names):
        loaded_names = set()
        for tool in tools:
            tool_name = getattr(tool, "name", None) or getattr(tool, "schema", {}).get("name", "")
            if tool_name:
                loaded_names.add(tool_name)
        missing = set(tool_names) - loaded_names
        if missing:
            logger.warning("Agent %s 原生工具部分加载失败: %s", agent_name, missing)

    return tools


async def _load_mcp_tools(agent_name: str) -> list[Any]:
    """复用 mcp_integration 加载 MCP 工具

    Args:
        agent_name: Agent 名称

    Returns:
        FunctionTool 列表
    """
    try:
        from agent.core.mcp.mcp_integration import load_agent_tools
        return await load_agent_tools(agent_name)
    except Exception as e:
        logger.warning("Agent %s MCP 工具加载失败: %s", agent_name, e)
        return []


def _merge_tools(native: list[Any], mcp: list[Any]) -> list[Any]:
    """合并原生与 MCP 工具

    原生工具已带 native_ 前缀，MCP 工具保持原名，
    三层工具的命名空间完全正交，直接拼接即可。

    Args:
        native: 原生工具列表
        mcp: MCP 工具列表

    Returns:
        合并后的工具列表
    """
    merged = list(native) + list(mcp)

    for native_tool in native:
        for mcp_tool in mcp:
            _check_schema_compatibility(
                getattr(native_tool, "name", ""),
                native_tool,
                mcp_tool,
            )

    return merged


def _check_schema_compatibility(name: str, native: Any, mcp: Any) -> None:
    """检查原生工具与 MCP 工具的 schema 兼容性

    当同名工具（理论上不会出现，因为命名空间正交）的参数差异时发出 WARNING。

    Args:
        name: 工具名称
        native: 原生工具
        mcp: MCP 工具
    """
    native_name = getattr(native, "name", "")
    mcp_name = getattr(mcp, "name", "")
    if native_name and mcp_name and native_name == mcp_name:
        logger.warning("工具名称冲突: %s（原生与 MCP 同名）", native_name)


def bind_native_tool(agent_name: str, tool_name: str) -> None:
    """为 Agent 绑定原生工具

    与 domain.py 中 bind_skill_to_agent() 模式对齐。

    Args:
        agent_name: Agent 名称
        tool_name: 工具名称
    """
    if agent_name not in AGENT_NATIVE_TOOL_BINDINGS:
        AGENT_NATIVE_TOOL_BINDINGS[agent_name] = []
    if tool_name not in AGENT_NATIVE_TOOL_BINDINGS[agent_name]:
        AGENT_NATIVE_TOOL_BINDINGS[agent_name].append(tool_name)
        logger.info("绑定原生工具 %s 到 Agent %s", tool_name, agent_name)


def unbind_native_tool(agent_name: str, tool_name: str) -> None:
    """解除 Agent 与原生工具的绑定

    Args:
        agent_name: Agent 名称
        tool_name: 工具名称
    """
    if agent_name in AGENT_NATIVE_TOOL_BINDINGS:
        bindings = AGENT_NATIVE_TOOL_BINDINGS[agent_name]
        if tool_name in bindings:
            bindings.remove(tool_name)
            logger.info("解除 Agent %s 的原生工具绑定: %s", agent_name, tool_name)


async def get_available_tool_names(agent_name: str) -> set[str]:
    """获取 Agent 当前实际可用的工具名称集合

    合并原生工具和 MCP 工具的名称，用于运行时工具可用性校验。

    Args:
        agent_name: Agent 名称

    Returns:
        可用工具名称集合
    """
    available: set[str] = set()

    # 原生工具
    tool_names = AGENT_NATIVE_TOOL_BINDINGS.get(agent_name, [])
    registry = get_native_tool_registry()
    for name in tool_names:
        if registry.get(name) is not None:
            available.add(name)

    # MCP 工具
    try:
        from agent.core.mcp.mcp_integration import _tool_cache, AGENT_TOOL_BINDINGS
        bound_servers = AGENT_TOOL_BINDINGS.get(agent_name, [])
        for server_name in bound_servers:
            server_tools = _tool_cache.get(server_name, [])
            for t in server_tools:
                tool_fn_name = getattr(t, "name", None) or getattr(t, "__name__", "")
                if tool_fn_name:
                    available.add(tool_fn_name)
    except Exception as e:
        logger.debug("获取 MCP 工具名称失败（非致命）: %s", e)

    return available
