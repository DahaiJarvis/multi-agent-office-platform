"""工具协议适配层

将原生工具的 JSON Schema 转换为不同 LLM 供应商的工具调用格式。
当前项目使用 AutoGen，默认适配器零损耗直通。

支持的协议适配器：
  -------------------------------------------------------------------------
  AutoGenProtocolAdapter: AutoGen FunctionTool 格式（默认）
    - 直接使用 JSON Schema，无需转换
    - 当前项目使用此格式，零损耗

  ClaudeProtocolAdapter: Claude input_schema 格式
    - parameters -> input_schema
    - 用于 Anthropic Claude 模型

  OpenClawProtocolAdapter: OpenClaw function declaration 格式
    - JSON Schema -> 参数声明列表
    - 用于 OpenClaw 兼容模型
  -------------------------------------------------------------------------

全局入口：
  get_protocol_adapter(provider) -> 根据供应商名称返回对应适配器
  未找到时降级为 AutoGen 格式
"""

import abc
import logging
from typing import Any

from agent.tools.base import NativeToolMeta

logger = logging.getLogger(__name__)


class BaseProtocolAdapter(abc.ABC):
    """协议适配器基类

    定义工具元数据到目标协议的转换接口。
    """

    @abc.abstractmethod
    def adapt(self, meta: NativeToolMeta) -> dict[str, Any]:
        """将工具元数据转换为目标协议格式

        Args:
            meta: 原生工具元数据

        Returns:
            目标协议格式的工具定义字典
        """


class AutoGenProtocolAdapter(BaseProtocolAdapter):
    """AutoGen FunctionTool 协议适配器

    直接使用 JSON Schema 格式，与 AutoGen FunctionTool 的 schema 格式一致。
    当前项目默认使用此适配器，零损耗直通。
    """

    def adapt(self, meta: NativeToolMeta) -> dict[str, Any]:
        """将工具元数据转换为 AutoGen FunctionTool 格式

        Args:
            meta: 原生工具元数据

        Returns:
            AutoGen FunctionTool 格式的工具定义
        """
        return {
            "name": meta.name,
            "description": meta.description,
            "parameters": meta.parameters,
        }


class ClaudeProtocolAdapter(BaseProtocolAdapter):
    """Claude 协议适配器

    将 JSON Schema 的 parameters 字段转换为 Claude 的 input_schema 格式。
    Claude 工具定义使用 input_schema 替代 parameters。
    """

    def adapt(self, meta: NativeToolMeta) -> dict[str, Any]:
        """将工具元数据转换为 Claude 工具格式

        Args:
            meta: 原生工具元数据

        Returns:
            Claude 格式的工具定义
        """
        return {
            "name": meta.name,
            "description": meta.description,
            "input_schema": meta.parameters,
        }


class OpenClawProtocolAdapter(BaseProtocolAdapter):
    """OpenClaw 协议适配器

    将 JSON Schema 转换为 OpenClaw 的 function declaration 格式。
    OpenClaw 使用参数声明列表替代 JSON Schema。
    """

    def adapt(self, meta: NativeToolMeta) -> dict[str, Any]:
        """将工具元数据转换为 OpenClaw function declaration 格式

        Args:
            meta: 原生工具元数据

        Returns:
            OpenClaw 格式的工具定义
        """
        properties = meta.parameters.get("properties", {})
        required = meta.parameters.get("required", [])

        declarations = []
        for param_name, param_schema in properties.items():
            declarations.append({
                "name": param_name,
                "type": param_schema.get("type", "string"),
                "description": param_schema.get("description", ""),
                "required": param_name in required,
            })

        return {
            "name": meta.name,
            "description": meta.description,
            "parameters": declarations,
        }


_ADAPTERS: dict[str, type[BaseProtocolAdapter]] = {
    "autogen": AutoGenProtocolAdapter,
    "openai": AutoGenProtocolAdapter,
    "qwen": AutoGenProtocolAdapter,
    "claude": ClaudeProtocolAdapter,
    "anthropic": ClaudeProtocolAdapter,
    "openclaw": OpenClawProtocolAdapter,
}

_default_adapter = AutoGenProtocolAdapter()


def get_protocol_adapter(provider: str = "autogen") -> BaseProtocolAdapter:
    """获取协议适配器

    根据供应商名称返回对应的协议适配器。
    未找到时降级为 AutoGen 格式。

    Args:
        provider: 供应商名称，如 "autogen"、"claude"、"openclaw"

    Returns:
        协议适配器实例
    """
    adapter_cls = _ADAPTERS.get(provider.lower())
    if adapter_cls is None:
        logger.debug("未找到 %s 的协议适配器，降级为 AutoGen 格式", provider)
        return _default_adapter
    return adapter_cls()
