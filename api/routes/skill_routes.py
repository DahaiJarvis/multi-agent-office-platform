"""Skills API 路由

提供 Skills 的 CRUD、绑定/解绑、搜索和导出接口。

权限控制：
  - GET 接口：认证用户可访问
  - POST/PUT/DELETE 接口：需要 admin 角色
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from agent.core.skill_adapter import SkillRegistry, SkillParseError, SkillValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["Skills"])


class CreateSkillRequest(BaseModel):
    """创建 Skill 请求"""
    content: str = Field(min_length=1, max_length=65536, description="SKILL.md 完整内容")


class UpdateSkillRequest(BaseModel):
    """更新 Skill 请求"""
    content: str = Field(min_length=1, max_length=65536, description="SKILL.md 完整内容")


class BindSkillRequest(BaseModel):
    """绑定 Skill 请求"""
    agent_name: str = Field(min_length=1, max_length=64, description="Agent 名称")


def _require_admin(request: Request) -> None:
    """校验管理员权限

    Args:
        request: FastAPI 请求对象

    Raises:
        AppException: 权限不足
    """
    user_roles = getattr(request.state, "user_roles", [])
    if "admin" not in user_roles:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.PERMISSION_DENIED, message="需要管理员权限")


@router.get("", summary="列出所有 Skills")
async def api_list_skills(
    category: str | None = None,
    enabled_only: bool = True,
) -> dict:
    """列出所有 Skills"""
    registry = SkillRegistry.get_instance()
    skills = registry.list_skills(enabled_only=enabled_only)

    if category:
        skills = [s for s in skills if s.manifest.category == category]

    result = []
    for doc in skills:
        result.append({
            "name": doc.manifest.name,
            "description": doc.manifest.description,
            "version": doc.manifest.version,
            "author": doc.manifest.author,
            "category": doc.manifest.category,
            "tags": doc.manifest.tags,
            "priority": doc.manifest.priority,
            "enabled": doc.manifest.enabled,
            "review_required": doc.manifest.review_required,
        })

    return {"total": len(result), "skills": result}


@router.get("/search", summary="搜索 Skills")
async def api_search_skills(keyword: str = "") -> dict:
    """搜索 Skills"""
    if not keyword.strip():
        return {"total": 0, "skills": []}

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

    return {"keyword": keyword.strip(), "total": len(skill_list), "skills": skill_list}


@router.get("/{skill_name}", summary="获取 Skill 详情")
async def api_get_skill(skill_name: str) -> dict:
    """获取 Skill 详情"""
    registry = SkillRegistry.get_instance()
    doc = registry.get(skill_name)
    if doc is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"Skill '{skill_name}' 不存在")

    return {
        "name": doc.manifest.name,
        "description": doc.manifest.description,
        "version": doc.manifest.version,
        "author": doc.manifest.author,
        "category": doc.manifest.category,
        "tags": doc.manifest.tags,
        "priority": doc.manifest.priority,
        "enabled": doc.manifest.enabled,
        "review_required": doc.manifest.review_required,
        "collaboration_mode": doc.manifest.collaboration_mode,
        "suggested_tools": doc.manifest.suggested_tools,
        "created_at": doc.manifest.created_at,
        "updated_at": doc.manifest.updated_at,
        "instruction_length": len(doc.instruction),
    }


@router.get("/{skill_name}/raw", summary="获取 SKILL.md 原始内容")
async def api_get_skill_raw(skill_name: str) -> dict:
    """获取 SKILL.md 原始内容"""
    registry = SkillRegistry.get_instance()
    content = registry.get_skill_raw_content(skill_name)
    if content is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"Skill '{skill_name}' 不存在")

    return {"name": skill_name, "content": content}


@router.post("", summary="上传/创建 Skill", status_code=201)
async def api_create_skill(request: Request, body: CreateSkillRequest) -> dict:
    """上传/创建 Skill（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    try:
        doc = registry.save_skill(skill_name="", content=body.content)
        return {
            "name": doc.manifest.name,
            "description": doc.manifest.description,
            "version": doc.manifest.version,
            "review_required": doc.manifest.review_required,
            "created": True,
        }
    except SkillValidationError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_VALIDATION_ERROR, message=str(e))
    except SkillParseError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_PARSE_ERROR, message=str(e))


@router.put("/{skill_name}", summary="更新 Skill")
async def api_update_skill(request: Request, skill_name: str, body: UpdateSkillRequest) -> dict:
    """更新 Skill（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    try:
        doc = registry.save_skill(skill_name=skill_name, content=body.content)
        return {
            "name": doc.manifest.name,
            "description": doc.manifest.description,
            "version": doc.manifest.version,
            "review_required": doc.manifest.review_required,
            "updated": True,
        }
    except SkillValidationError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_VALIDATION_ERROR, message=str(e))
    except SkillParseError as e:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_PARSE_ERROR, message=str(e))


@router.delete("/{skill_name}", summary="删除 Skill")
async def api_delete_skill(request: Request, skill_name: str) -> dict:
    """删除 Skill（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = registry.delete(skill_name)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"Skill '{skill_name}' 不存在")

    return {"name": skill_name, "deleted": True}


@router.post("/{skill_name}/enable", summary="启用 Skill")
async def api_enable_skill(request: Request, skill_name: str) -> dict:
    """启用 Skill（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = registry.enable(skill_name)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"Skill '{skill_name}' 不存在")

    return {"name": skill_name, "enabled": True}


@router.post("/{skill_name}/disable", summary="禁用 Skill")
async def api_disable_skill(request: Request, skill_name: str) -> dict:
    """禁用 Skill（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = registry.disable(skill_name)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"Skill '{skill_name}' 不存在")

    return {"name": skill_name, "enabled": False}


@router.post("/{skill_name}/bind", summary="绑定 Skill 到 Agent")
async def api_bind_skill(request: Request, skill_name: str, body: BindSkillRequest) -> dict:
    """绑定 Skill 到 Agent（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = registry.bind_to_agent(skill_name, body.agent_name)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_BIND_FAILED, message=f"绑定 Skill '{skill_name}' 到 Agent '{body.agent_name}' 失败")

    return {"skill_name": skill_name, "agent_name": body.agent_name, "bound": True}


@router.post("/{skill_name}/unbind", summary="解除 Skill 与 Agent 的绑定")
async def api_unbind_skill(request: Request, skill_name: str, body: BindSkillRequest) -> dict:
    """解除 Skill 与 Agent 的绑定（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = registry.unbind_from_agent(skill_name, body.agent_name)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_BIND_FAILED, message=f"解除 Skill '{skill_name}' 与 Agent '{body.agent_name}' 的绑定失败")

    return {"skill_name": skill_name, "agent_name": body.agent_name, "bound": False}


@router.get("/export/{agent_name}", summary="导出 Agent 为 SKILL.md")
async def api_export_agent(request: Request, agent_name: str) -> dict:
    """导出 Agent 为 SKILL.md（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    content = registry.export_agent_as_skill(agent_name)
    if content is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.AGENT_NOT_FOUND, message=f"Agent '{agent_name}' 不存在")

    return {"agent_name": agent_name, "content": content}
