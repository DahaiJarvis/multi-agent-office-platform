"""会话相关原生工具

提供会话历史查询、搜索和摘要功能，复用 SessionManager 的数据。

工具列表：
  -------------------------------------------------------------------------
  native_session_history: 查询当前会话历史消息
    - 延迟分层: instant
    - 权限级别: read_only
    - 注册方式: 立即注册

  native_session_search: 在历史会话中搜索关键词
    - 延迟分层: instant
    - 权限级别: read_only
    - 注册方式: 立即注册

  native_session_summary: 生成当前会话摘要
    - 延迟分层: slow
    - 权限级别: read_only
    - 注册方式: 懒注册（依赖 LLM）
  -------------------------------------------------------------------------

数据来源：复用 agent/core/session_manager.py 的 SessionManager
会话 ID 从 Agent 运行时上下文获取，不由用户传入
"""

import json
import logging
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)

_current_session_id: str = ""


def set_current_session_id(session_id: str) -> None:
    """设置当前会话 ID

    由 Agent 运行时上下文调用，在工具执行前设置当前会话 ID。
    工具内部通过此 ID 获取会话数据，不由用户传入。

    Args:
        session_id: 当前会话 ID
    """
    global _current_session_id
    _current_session_id = session_id


def get_current_session_id() -> str:
    """获取当前会话 ID

    Returns:
        当前会话 ID
    """
    return _current_session_id


async def _session_history(limit: int = 10) -> str:
    """查询当前会话历史消息

    返回最近 N 条消息，包含角色、内容和时间戳。

    Args:
        limit: 返回消息数量，默认 10 条

    Returns:
        JSON 格式的消息历史
    """
    session_id = get_current_session_id()
    if not session_id:
        return json.dumps({"error": "当前无活跃会话", "messages": []}, ensure_ascii=False)

    try:
        from agent.core.session_manager import get_session_manager
        manager = await get_session_manager()
        session = await manager.get_session(session_id)
        if session is None:
            return json.dumps({"error": f"会话 {session_id} 不存在", "messages": []}, ensure_ascii=False)

        messages = session.message_history[-limit:]
        result = {
            "session_id": session_id,
            "total_messages": len(session.message_history),
            "returned_messages": len(messages),
            "messages": [
                {
                    "role": msg.get("role", ""),
                    "content": msg.get("content", ""),
                    "timestamp": msg.get("timestamp", ""),
                }
                for msg in messages
            ],
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("查询会话历史失败: %s", e)
        return json.dumps({"error": f"查询会话历史失败: {str(e)}", "messages": []}, ensure_ascii=False)


async def _session_search(keyword: str, limit: int = 20) -> str:
    """在历史会话中搜索关键词

    支持模糊匹配，在消息内容中搜索包含关键词的消息。

    Args:
        keyword: 搜索关键词
        limit: 返回消息数量上限，默认 20 条

    Returns:
        JSON 格式的搜索结果
    """
    session_id = get_current_session_id()
    if not session_id:
        return json.dumps({"error": "当前无活跃会话", "results": []}, ensure_ascii=False)

    if not keyword:
        return json.dumps({"error": "搜索关键词不能为空", "results": []}, ensure_ascii=False)

    try:
        from agent.core.session_manager import get_session_manager
        manager = await get_session_manager()
        session = await manager.get_session(session_id)
        if session is None:
            return json.dumps({"error": f"会话 {session_id} 不存在", "results": []}, ensure_ascii=False)

        keyword_lower = keyword.lower()
        matched = []
        for msg in session.message_history:
            content = msg.get("content", "")
            if keyword_lower in content.lower():
                matched.append({
                    "role": msg.get("role", ""),
                    "content": content,
                    "timestamp": msg.get("timestamp", ""),
                })
                if len(matched) >= limit:
                    break

        result = {
            "session_id": session_id,
            "keyword": keyword,
            "total_matched": len(matched),
            "results": matched,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("搜索会话历史失败: %s", e)
        return json.dumps({"error": f"搜索会话历史失败: {str(e)}", "results": []}, ensure_ascii=False)


async def _session_summary(mode: str = "brief") -> str:
    """生成当前会话摘要

    使用 LLM 生成当前会话的摘要。

    Args:
        mode: 摘要模式，brief(简要) / detailed(详细)

    Returns:
        JSON 格式的会话摘要
    """
    session_id = get_current_session_id()
    if not session_id:
        return json.dumps({"error": "当前无活跃会话", "summary": ""}, ensure_ascii=False)

    try:
        from agent.core.session_manager import get_session_manager
        manager = await get_session_manager()
        session = await manager.get_session(session_id)
        if session is None:
            return json.dumps({"error": f"会话 {session_id} 不存在", "summary": ""}, ensure_ascii=False)

        messages_text = "\n".join([
            f"[{msg.get('role', 'unknown')}]: {msg.get('content', '')}"
            for msg in session.message_history
        ])

        if not messages_text.strip():
            return json.dumps({"session_id": session_id, "summary": "当前会话无消息记录"}, ensure_ascii=False)

        if session.context_summary:
            return json.dumps({
                "session_id": session_id,
                "summary": session.context_summary,
                "mode": mode,
                "cached": True,
            }, ensure_ascii=False)

        from agent.core.model_client import get_lightweight_client
        from autogen_core.models import UserMessage

        client = get_lightweight_client()

        if mode == "detailed":
            prompt = (
                "请详细总结以下会话内容，包括主要讨论的话题、做出的决策、待解决的问题等：\n\n"
                f"{messages_text}"
            )
        else:
            prompt = f"请简要总结以下会话内容（100字以内）：\n\n{messages_text}"

        response = await client.create(messages=[UserMessage(content=prompt)])
        summary = response.content if isinstance(response.content, str) else str(response.content)

        result = {
            "session_id": session_id,
            "summary": summary,
            "mode": mode,
            "message_count": len(session.message_history),
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("生成会话摘要失败: %s", e)
        return json.dumps({"error": f"生成会话摘要失败: {str(e)}", "summary": ""}, ensure_ascii=False)


_SESSION_HISTORY_META = NativeToolMeta(
    name="native_session_history",
    display_name="会话历史查询",
    description="查询当前会话的历史消息，返回最近 N 条消息的列表，包含角色、内容和时间戳。",
    category="session",
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "返回消息数量，默认 10 条",
                "default": 10,
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": [],
    },
    latency_tier=LatencyTier.INSTANT,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=5,
    requires_llm=False,
    tags=["session", "history", "query"],
)

_SESSION_SEARCH_META = NativeToolMeta(
    name="native_session_search",
    display_name="会话内容搜索",
    description="在当前会话的历史消息中搜索关键词，支持模糊匹配，返回包含关键词的消息列表。",
    category="session",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词",
            },
            "limit": {
                "type": "integer",
                "description": "返回消息数量上限，默认 20 条",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["keyword"],
    },
    latency_tier=LatencyTier.INSTANT,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=5,
    requires_llm=False,
    tags=["session", "search"],
)

_SESSION_SUMMARY_META = NativeToolMeta(
    name="native_session_summary",
    display_name="会话摘要生成",
    description="使用 AI 生成当前会话的摘要，支持简要和详细两种模式。",
    category="session",
    parameters={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "摘要模式: brief(简要) / detailed(详细)",
                "enum": ["brief", "detailed"],
                "default": "brief",
            },
        },
        "required": [],
    },
    latency_tier=LatencyTier.SLOW,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=60,
    requires_llm=True,
    tags=["session", "summary", "llm"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有会话工具

    native_session_history 和 native_session_search 无外部依赖，使用立即注册。
    native_session_summary 依赖 LLM 客户端，使用懒注册。

    Args:
        registry: 工具注册中心实例
    """
    history_tool = FunctionTool(
        func=_session_history,
        name="native_session_history",
        description=_SESSION_HISTORY_META.description,
    )
    registry.register(history_tool, _SESSION_HISTORY_META)

    search_tool = FunctionTool(
        func=_session_search,
        name="native_session_search",
        description=_SESSION_SEARCH_META.description,
    )
    registry.register(search_tool, _SESSION_SEARCH_META)

    def _create_summary_tool() -> FunctionTool:
        return FunctionTool(
            func=_session_summary,
            name="native_session_summary",
            description=_SESSION_SUMMARY_META.description,
        )

    registry.register_lazy("native_session_summary", _create_summary_tool, _SESSION_SUMMARY_META)

    logger.debug("会话工具注册完成: native_session_history, native_session_search, native_session_summary(懒注册)")
