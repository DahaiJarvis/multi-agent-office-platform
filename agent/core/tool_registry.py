"""工具执行桥接模块

为工作流引擎提供工具调用能力，桥接工作流节点与现有工具注册中心。

核心功能：
  - execute_tool(): 查找并执行指定工具
  - 复用 agent.tools.registry 的工具注册和加载机制

与直接使用 NativeToolRegistry 的区别：
  - NativeToolRegistry: 管理工具的注册、发现和加载
  - execute_tool: 封装工具查找和执行过程，提供统一调用接口

使用场景：
  - 工作流引擎的 Tool 节点执行
  - 需要通过工具名称直接调用工具的场景
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute_tool(
    tool_name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """查找并执行指定工具

    从原生工具注册中心查找工具，传入参数并执行。

    执行流程：
    -------------------------------------------------------------------------
    1. 从 NativeToolRegistry 获取工具实例
    2. 如果是原生工具（native_ 前缀），直接调用
    3. 如果是 MCP 工具，通过 MCP 集成层调用
    4. 返回执行结果
    -------------------------------------------------------------------------

    Args:
        tool_name: 工具名称，如 "native_current_time" 或 "approval:query"
        params: 工具输入参数字典

    Returns:
        执行结果字典，包含：
        - status: 执行状态（success / error）
        - output: 工具输出内容
        - tool_name: 工具名称
    """
    if not tool_name:
        return {
            "status": "error",
            "output": "未指定工具名称",
            "tool_name": "",
        }

    params = params or {}

    if tool_name.startswith("native_"):
        return await _execute_native_tool(tool_name, params)

    return await _execute_mcp_tool(tool_name, params)


async def _execute_native_tool(
    tool_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """执行原生工具

    从 NativeToolRegistry 获取工具实例，将参数序列化后调用。

    AutoGen FunctionTool 的调用方式为 json_schema 风格：
    工具函数的参数通过关键字参数传入，由 AutoGen 框架自动处理序列化。

    Args:
        tool_name: 原生工具名称（native_ 前缀）
        params: 工具输入参数

    Returns:
        执行结果字典
    """
    try:
        from agent.tools.registry import get_native_tool_registry

        registry = get_native_tool_registry()
        tool = registry.get(tool_name)

        if tool is None:
            return {
                "status": "error",
                "output": f"工具 {tool_name} 未注册",
                "tool_name": tool_name,
            }

        meta = registry.get_meta(tool_name)
        if meta and not meta.enabled:
            return {
                "status": "error",
                "output": f"工具 {tool_name} 已禁用",
                "tool_name": tool_name,
            }

        func = tool.func
        result = await func(**params) if _is_async_func(func) else func(**params)

        output = _parse_tool_output(result)

        return {
            "status": "success",
            "output": output,
            "tool_name": tool_name,
        }

    except Exception as e:
        logger.error("原生工具执行失败: tool=%s, error=%s", tool_name, e)
        return {
            "status": "error",
            "output": f"工具 {tool_name} 执行失败: {str(e)}",
            "tool_name": tool_name,
        }


async def _execute_mcp_tool(
    tool_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """执行 MCP 工具

    通过 MCP 集成层调用外部服务工具。
    MCP 工具名称格式为 "资源:操作"，如 "approval:query"。

    Args:
        tool_name: MCP 工具名称
        params: 工具输入参数

    Returns:
        执行结果字典
    """
    try:
        from agent.core.mcp_integration import load_mcp_tools

        resource_prefix = tool_name.split(":")[0] if ":" in tool_name else tool_name
        tools = await load_mcp_tools([resource_prefix])

        target_tool = None
        for tool in tools:
            t_name = getattr(tool, "name", "")
            if t_name == tool_name:
                target_tool = tool
                break

        if target_tool is None:
            return {
                "status": "error",
                "output": f"MCP 工具 {tool_name} 未找到",
                "tool_name": tool_name,
            }

        func = target_tool.func
        result = await func(**params) if _is_async_func(func) else func(**params)

        output = _parse_tool_output(result)

        return {
            "status": "success",
            "output": output,
            "tool_name": tool_name,
        }

    except Exception as e:
        logger.error("MCP 工具执行失败: tool=%s, error=%s", tool_name, e)
        return {
            "status": "error",
            "output": f"MCP 工具 {tool_name} 执行失败: {str(e)}",
            "tool_name": tool_name,
        }


def _is_async_func(func: Any) -> bool:
    """判断函数是否为异步函数

    Args:
        func: 待检查的函数

    Returns:
        True 表示异步函数
    """
    import asyncio
    return asyncio.iscoroutinefunction(func)


def _parse_tool_output(result: Any) -> Any:
    """解析工具输出

    工具输出可能是 JSON 字符串或 Python 对象，
    统一解析为可序列化的格式。

    Args:
        result: 工具原始输出

    Returns:
        解析后的输出
    """
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return result

    if isinstance(result, (dict, list, int, float, bool)):
        return result

    return str(result)
