"""Skills 原生工具

提供 Skill 的运行时加载、卸载、列表和搜索功能，
使 Agent 可在对话过程中动态加载 Skill 指令。

工具列表：
  - native_skill_load: 运行时加载 Skill 指令
  - native_skill_unload: 运行时卸载 Skill
  - native_skill_list: 列出可用 Skills
  - native_skill_search: 搜索 Skills
"""

import json
import logging
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.session_tools import get_current_session_id

logger = logging.getLogger(__name__)


async def _skill_load(skill_name: str) -> str:
    """运行时加载 Skill 指令

    从 SkillRegistry 获取 Skill 的 instruction，
    标记为当前会话已激活，返回 Skill 指令内容供 LLM 执行。

    Args:
        skill_name: Skill 名称

    Returns:
        JSON 格式的加载结果
    """
    if not skill_name or not skill_name.strip():
        return json.dumps({"error": "Skill 名称不能为空", "instruction": ""}, ensure_ascii=False)

    try:
        from agent.core.skill_adapter import SkillRegistry

        registry = SkillRegistry.get_instance()
        normalized_name = skill_name.strip().lower().replace("_", "-").replace(" ", "-")

        doc = registry.get(normalized_name)
        if doc is None:
            return json.dumps({
                "error": f"Skill '{normalized_name}' 不存在",
                "instruction": "",
                "available_skills": [d.manifest.name for d in registry.list_skills()],
            }, ensure_ascii=False)

        if not doc.manifest.enabled:
            return json.dumps({
                "error": f"Skill '{normalized_name}' 已禁用",
                "instruction": "",
            }, ensure_ascii=False)

        session_id = get_current_session_id() or "default"
        registry.activate_skill(session_id, normalized_name)

        instruction = doc.instruction
        result = {
            "skill_name": normalized_name,
            "description": doc.manifest.description,
            "instruction": instruction,
            "suggested_tools": doc.manifest.suggested_tools,
            "activated": True,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Skill 加载失败: skill_name=%s, error=%s", skill_name[:100], e)
        return json.dumps({"error": f"Skill 加载失败: {str(e)}", "instruction": ""}, ensure_ascii=False)


async def _skill_unload(skill_name: str) -> str:
    """运行时卸载 Skill

    标记 Skill 为当前会话已卸载，LLM 后续不再引用该 Skill 的指令。

    Args:
        skill_name: Skill 名称

    Returns:
        JSON 格式的卸载结果
    """
    if not skill_name or not skill_name.strip():
        return json.dumps({"error": "Skill 名称不能为空", "unloaded": False}, ensure_ascii=False)

    try:
        from agent.core.skill_adapter import SkillRegistry

        registry = SkillRegistry.get_instance()
        normalized_name = skill_name.strip().lower().replace("_", "-").replace(" ", "-")

        session_id = get_current_session_id() or "default"
        success = registry.deactivate_skill(session_id, normalized_name)

        if success:
            result = {
                "skill_name": normalized_name,
                "unloaded": True,
                "message": f"Skill '{normalized_name}' 已卸载，后续不再引用该 Skill 的指令",
            }
        else:
            result = {
                "skill_name": normalized_name,
                "unloaded": False,
                "message": f"Skill '{normalized_name}' 未在当前会话中激活，无需卸载",
            }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Skill 卸载失败: skill_name=%s, error=%s", skill_name[:100], e)
        return json.dumps({"error": f"Skill 卸载失败: {str(e)}", "unloaded": False}, ensure_ascii=False)


async def _skill_list() -> str:
    """列出可用 Skills

    仅列出已启用的 Skill，按优先级降序排列。

    Returns:
        JSON 格式的 Skill 列表
    """
    try:
        from agent.core.skill_adapter import SkillRegistry

        registry = SkillRegistry.get_instance()
        skills = registry.list_skills(enabled_only=True)

        skill_list = []
        for doc in skills:
            skill_list.append({
                "name": doc.manifest.name,
                "description": doc.manifest.description,
                "category": doc.manifest.category,
                "tags": doc.manifest.tags,
                "priority": doc.manifest.priority,
                "version": doc.manifest.version,
            })

        session_id = get_current_session_id() or "default"
        active_skills = registry.get_active_skills(session_id)

        result = {
            "total": len(skill_list),
            "skills": skill_list,
            "active_skills": list(active_skills),
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Skill 列表获取失败: error=%s", e)
        return json.dumps({"error": f"Skill 列表获取失败: {str(e)}", "skills": []}, ensure_ascii=False)


async def _skill_search(keyword: str) -> str:
    """搜索 Skills

    按关键词搜索，匹配 name、description、tags 字段。

    Args:
        keyword: 搜索关键词

    Returns:
        JSON 格式的搜索结果
    """
    if not keyword or not keyword.strip():
        return json.dumps({"error": "搜索关键词不能为空", "skills": []}, ensure_ascii=False)

    try:
        from agent.core.skill_adapter import SkillRegistry

        registry = SkillRegistry.get_instance()
        results = registry.search(keyword.strip())

        skill_list = []
        for doc in results:
            skill_list.append({
                "name": doc.manifest.name,
                "description": doc.manifest.description,
                "category": doc.manifest.category,
                "tags": doc.manifest.tags,
                "priority": doc.manifest.priority,
            })

        result = {
            "keyword": keyword.strip(),
            "total": len(skill_list),
            "skills": skill_list,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Skill 搜索失败: keyword=%s, error=%s", keyword[:100], e)
        return json.dumps({"error": f"Skill 搜索失败: {str(e)}", "skills": []}, ensure_ascii=False)


def register_all(registry: Any) -> None:
    """注册所有 Skills 原生工具

    Args:
        registry: NativeToolRegistry 实例
    """
    registry.register_lazy(
        name="native_skill_load",
        factory=lambda: FunctionTool(
            func=_skill_load,
            name="native_skill_load",
            description="运行时加载 Skill 指令，加载后 LLM 将按照 Skill 指令执行任务",
        ),
        meta=NativeToolMeta(
            name="native_skill_load",
            display_name="加载 Skill",
            description="运行时加载 Skill 指令，加载后 LLM 将按照 Skill 指令执行任务",
            category="skill",
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "要加载的 Skill 名称",
                    },
                },
                "required": ["skill_name"],
            },
            latency_tier=LatencyTier.GENERAL,
            permission_level=PermissionLevel.READ_WRITE,
            timeout_seconds=10,
            requires_llm=False,
            tags=["skill", "load"],
        ),
    )

    registry.register_lazy(
        name="native_skill_unload",
        factory=lambda: FunctionTool(
            func=_skill_unload,
            name="native_skill_unload",
            description="运行时卸载 Skill，卸载后 LLM 不再引用该 Skill 的指令",
        ),
        meta=NativeToolMeta(
            name="native_skill_unload",
            display_name="卸载 Skill",
            description="运行时卸载 Skill，卸载后 LLM 不再引用该 Skill 的指令",
            category="skill",
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "要卸载的 Skill 名称",
                    },
                },
                "required": ["skill_name"],
            },
            latency_tier=LatencyTier.GENERAL,
            permission_level=PermissionLevel.READ_WRITE,
            timeout_seconds=10,
            requires_llm=False,
            tags=["skill", "unload"],
        ),
    )

    registry.register(
        tool=FunctionTool(
            func=_skill_list,
            name="native_skill_list",
            description="列出所有可用的 Skills，仅显示已启用的 Skill",
        ),
        meta=NativeToolMeta(
            name="native_skill_list",
            display_name="列出 Skills",
            description="列出所有可用的 Skills，仅显示已启用的 Skill",
            category="skill",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            latency_tier=LatencyTier.INSTANT,
            permission_level=PermissionLevel.READ_ONLY,
            timeout_seconds=5,
            requires_llm=False,
            tags=["skill", "list"],
        ),
    )

    registry.register_lazy(
        name="native_skill_search",
        factory=lambda: FunctionTool(
            func=_skill_search,
            name="native_skill_search",
            description="按关键词搜索 Skills，匹配名称、描述和标签",
        ),
        meta=NativeToolMeta(
            name="native_skill_search",
            display_name="搜索 Skills",
            description="按关键词搜索 Skills，匹配名称、描述和标签",
            category="skill",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                },
                "required": ["keyword"],
            },
            latency_tier=LatencyTier.INSTANT,
            permission_level=PermissionLevel.READ_ONLY,
            timeout_seconds=5,
            requires_llm=False,
            tags=["skill", "search"],
        ),
    )
