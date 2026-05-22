"""原生工具模块

提供平台原生工具的注册、加载和管理能力，包括：
  - 基础模型（LatencyTier / PermissionLevel / NativeToolMeta）
  - 工具注册中心（NativeToolRegistry）
  - 统一工具加载器（load_all_tools）
  - 协议适配层（AutoGen / Claude / OpenClaw）
  - 路径安全校验（validate_file_path）
  - 时间日期工具（native_current_time / native_date_calculate）
  - 会话工具（native_session_history / native_session_search / native_session_summary）
  - 数据分析工具（native_data_query / native_data_visualize / native_data_export）
  - 文档处理工具（native_document_parse / native_document_summarize / native_document_compare）
  - 搜索引擎工具（native_search_all / native_search_documents / native_search_knowledge）
  - 文本处理工具（native_text_format / native_text_extract / native_text_translate）
  - 报告生成工具（native_report_generate / native_report_export）
  - 多模态处理工具（native_image_analyze / native_image_ocr）
  - RAG 增强检索工具（native_rag_search / native_rag_qa）
  - Skills 工具（native_skill_load / native_skill_unload / native_skill_list / native_skill_search）
  - 原生工具审计日志（audit_native_tool_call / ToolCallAuditor）

双层工具体系：
  -------------------------------------------------------------------------
  Native Tools（原生工具）：平台自身能力封装，带 native_ 前缀
  MCP Tools（外部工具）：MCP Server 提供的工具，保持原始命名
  -------------------------------------------------------------------------
"""

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import get_native_tool_registry, NativeToolRegistry
from agent.tools.loader import (
    load_all_tools,
    bind_native_tool,
    unbind_native_tool,
    AGENT_NATIVE_TOOL_BINDINGS,
)
from agent.tools.protocol_adapter import get_protocol_adapter, BaseProtocolAdapter
from agent.tools.path_validator import validate_file_path, is_path_safe, PathValidationError
from agent.tools.session_tools import set_current_session_id, get_current_session_id

__all__ = [
    "LatencyTier",
    "NativeToolMeta",
    "PermissionLevel",
    "get_native_tool_registry",
    "NativeToolRegistry",
    "load_all_tools",
    "bind_native_tool",
    "unbind_native_tool",
    "AGENT_NATIVE_TOOL_BINDINGS",
    "get_protocol_adapter",
    "BaseProtocolAdapter",
    "validate_file_path",
    "is_path_safe",
    "PathValidationError",
    "set_current_session_id",
    "get_current_session_id",
]
