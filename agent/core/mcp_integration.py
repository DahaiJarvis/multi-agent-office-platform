"""MCP 工具加载与集成

负责从 MCP Server 加载工具，并转换为 AutoGen 可用的 Function Tool。
支持 SSE 远程连接和 STDIO 本地进程两种模式。
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from autogen_ext.tools.mcp import McpWorkbench, SseMcpToolAdapter, StdioMcpToolAdapter

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP 服务配置"""

    name: str
    description: str
    transport: str = "sse"  # sse | stdio
    url: str = ""  # SSE 模式的服务地址
    command: str = ""  # STDIO 模式的启动命令
    args: list[str] = field(default_factory=list)  # STDIO 模式的命令参数
    env: dict[str, str] = field(default_factory=dict)  # 环境变量
    enabled: bool = True


# MCP 服务注册表
MCP_SERVER_REGISTRY: dict[str, MCPServerConfig] = {
    "oa": MCPServerConfig(
        name="oa-mcp-server",
        description="OA 审批系统 MCP 服务",
        transport="sse",
        url="http://localhost:9001/sse",
    ),
    "email": MCPServerConfig(
        name="email-mcp-server",
        description="邮件系统 MCP 服务",
        transport="sse",
        url="http://localhost:9002/sse",
    ),
    "calendar": MCPServerConfig(
        name="calendar-mcp-server",
        description="日历系统 MCP 服务",
        transport="sse",
        url="http://localhost:9003/sse",
    ),
    "crm": MCPServerConfig(
        name="crm-mcp-server",
        description="CRM 系统 MCP 服务",
        transport="sse",
        url="http://localhost:9004/sse",
    ),
    "approval": MCPServerConfig(
        name="approval-mcp-server",
        description="审批系统 MCP 服务",
        transport="sse",
        url="http://localhost:9005/sse",
    ),
    "im": MCPServerConfig(
        name="im-mcp-server",
        description="IM 消息系统 MCP 服务",
        transport="sse",
        url="http://localhost:9006/sse",
    ),
    "doc": MCPServerConfig(
        name="doc-mcp-server",
        description="文档系统 MCP 服务",
        transport="sse",
        url="http://localhost:9007/sse",
    ),
    "hr": MCPServerConfig(
        name="hr-mcp-server",
        description="HR 人事系统 MCP 服务",
        transport="sse",
        url="http://localhost:9008/sse",
    ),
    "finance": MCPServerConfig(
        name="finance-mcp-server",
        description="财务系统 MCP 服务",
        transport="sse",
        url="http://localhost:9009/sse",
    ),
    "knowledge": MCPServerConfig(
        name="knowledge-mcp-server",
        description="知识库 MCP 服务",
        transport="sse",
        url="http://localhost:9010/sse",
    ),
}

# Agent 与 MCP 服务的工具绑定关系
AGENT_TOOL_BINDINGS: dict[str, list[str]] = {
    "OfficeAssistant": ["oa", "email", "calendar", "im", "doc"],
    "EmailAgent": ["email"],
    "ApprovalAgent": ["oa", "approval"],
    "CalendarAgent": ["calendar"],
    "CRMAgent": ["crm"],
    "HRAgent": ["hr"],
    "FinanceAgent": ["finance"],
    "Reviewer": ["oa", "approval", "hr", "finance"],
}

# 工具缓存
_tool_cache: dict[str, list[Any]] = {}
_workbench_cache: dict[str, McpWorkbench] = {}


async def load_mcp_tools(server_names: list[str]) -> list[Any]:
    """从指定的 MCP 服务加载工具列表

    Args:
        server_names: MCP 服务名称列表，如 ["oa", "email"]

    Returns:
        AutoGen Function Tool 列表
    """
    all_tools: list[Any] = []

    for name in server_names:
        config = MCP_SERVER_REGISTRY.get(name)
        if not config or not config.enabled:
            logger.warning("MCP 服务 %s 未注册或已禁用，跳过", name)
            continue

        if name in _tool_cache:
            all_tools.extend(_tool_cache[name])
            continue

        try:
            tools = await _connect_and_load(config)
            _tool_cache[name] = tools
            all_tools.extend(tools)
            logger.info("成功加载 MCP 服务 %s 的 %d 个工具", name, len(tools))
        except Exception as e:
            logger.error("加载 MCP 服务 %s 失败: %s", name, e)

    return all_tools


async def _connect_and_load(config: MCPServerConfig) -> list[Any]:
    """连接 MCP 服务并加载工具"""
    if config.transport == "sse":
        workbench = McpWorkbench(SseMcpToolAdapter(url=config.url))
    else:
        workbench = McpWorkbench(
            StdioMcpToolAdapter(command=config.command, args=config.args, env=config.env)
        )

    _workbench_cache[config.name] = workbench
    tools = await workbench.list_tools()
    return tools


async def load_agent_tools(agent_name: str) -> list[Any]:
    """根据 Agent 名称加载其绑定的 MCP 工具

    Args:
        agent_name: Agent 名称，如 "EmailAgent"

    Returns:
        AutoGen Function Tool 列表
    """
    bound_servers = AGENT_TOOL_BINDINGS.get(agent_name, [])
    return await load_mcp_tools(bound_servers)


async def close_all_connections() -> None:
    """关闭所有 MCP 连接"""
    for name, workbench in _workbench_cache.items():
        try:
            await workbench.close()
            logger.info("已关闭 MCP 服务 %s 的连接", name)
        except Exception as e:
            logger.error("关闭 MCP 服务 %s 连接失败: %s", name, e)

    _workbench_cache.clear()
    _tool_cache.clear()


def register_mcp_server(name: str, config: MCPServerConfig) -> None:
    """动态注册新的 MCP 服务

    Args:
        name: 服务标识
        config: MCP 服务配置
    """
    MCP_SERVER_REGISTRY[name] = config
    logger.info("已注册 MCP 服务: %s (%s)", name, config.url or config.command)


def disable_mcp_server(name: str) -> None:
    """禁用指定的 MCP 服务"""
    if name in MCP_SERVER_REGISTRY:
        MCP_SERVER_REGISTRY[name].enabled = False
        _tool_cache.pop(name, None)
        logger.info("已禁用 MCP 服务: %s", name)
