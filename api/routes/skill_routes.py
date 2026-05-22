"""Skills API 路由

提供 Skills 的 CRUD、绑定/解绑、搜索和导出接口。
提供技能市场2.0的版本管理、市场操作、依赖解析和测试接口。

权限控制：
  - GET 接口：认证用户可访问
  - POST/PUT/DELETE 接口：需要 admin 角色
"""

import logging

from fastapi import APIRouter, Query, Request
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


class PublishVersionRequest(BaseModel):
    """发布版本请求"""
    version: str = Field(min_length=1, max_length=32, description="版本号")
    content: str | None = Field(default=None, description="自定义内容")


class ActivateVersionRequest(BaseModel):
    """激活版本请求"""
    version: str = Field(min_length=1, max_length=32, description="目标版本号")


class PublishMarketplaceRequest(BaseModel):
    """发布到市场请求"""
    category: str = Field(default="general", description="市场分类")


class RateSkillRequest(BaseModel):
    """评分请求"""
    user_id: str = Field(min_length=1, max_length=64, description="用户ID")
    score: float = Field(ge=1.0, le=5.0, description="评分(1-5)")
    comment: str = Field(default="", max_length=1024, description="评价内容")


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


@router.get("/marketplace/search", summary="搜索技能市场")
async def api_search_marketplace(
    keyword: str = Query(default="", description="搜索关键词"),
    category: str = Query(default="", description="分类过滤"),
    sort_by: str = Query(default="rating", description="排序方式(rating/downloads/name)"),
) -> dict:
    """搜索技能市场"""
    registry = SkillRegistry.get_instance()
    entries = await registry.search_marketplace(keyword=keyword, category=category, sort_by=sort_by)
    return {
        "keyword": keyword,
        "category": category,
        "sort_by": sort_by,
        "total": len(entries),
        "entries": [e.model_dump() for e in entries],
    }


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


# ==================== SkillPack 元数据 ====================


@router.get("/{skill_name}/pack", summary="获取 SkillPack 元数据")
async def api_get_pack_manifest(skill_name: str) -> dict:
    """获取技能的 SkillPack 元数据（skill.yaml 解析结果）"""
    registry = SkillRegistry.get_instance()
    manifest = registry.get_pack_manifest(skill_name)
    if manifest is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"Skill '{skill_name}' 无 SkillPack 元数据")

    return manifest.model_dump()


@router.get("/{skill_name}/dependencies", summary="获取技能依赖声明")
async def api_get_dependencies(skill_name: str) -> dict:
    """获取技能的依赖声明（dependencies.yaml 解析结果）"""
    registry = SkillRegistry.get_instance()
    deps = registry.get_pack_dependencies(skill_name)
    return deps.model_dump()


@router.get("/{skill_name}/tools-config", summary="获取技能工具绑定")
async def api_get_tools_config(skill_name: str) -> dict:
    """获取技能的工具绑定配置（tools.yaml 解析结果）"""
    registry = SkillRegistry.get_instance()
    tools_config = registry.get_pack_tools(skill_name)
    return tools_config.model_dump()


@router.get("/{skill_name}/system-prompt", summary="获取技能系统Prompt")
async def api_get_system_prompt(skill_name: str) -> dict:
    """获取技能的系统 Prompt 模板"""
    registry = SkillRegistry.get_instance()
    prompt = registry.get_pack_system_prompt(skill_name)
    return {"skill_name": skill_name, "system_prompt": prompt}


@router.get("/{skill_name}/few-shots", summary="获取技能Few-shot示例")
async def api_get_few_shots(skill_name: str) -> dict:
    """获取技能的 Few-shot 示例"""
    registry = SkillRegistry.get_instance()
    shots = registry.get_pack_few_shots(skill_name)
    return {"skill_name": skill_name, "few_shots": shots}


# ==================== 版本管理 ====================


@router.post("/{skill_name}/versions/publish", summary="发布技能版本")
async def api_publish_version(request: Request, skill_name: str, body: PublishVersionRequest) -> dict:
    """发布技能新版本（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = await registry.publish_skill_version(skill_name, body.version, body.content)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_VALIDATION_ERROR, message=f"版本发布失败: {skill_name}@{body.version}")

    return {"skill_name": skill_name, "version": body.version, "published": True}


@router.post("/{skill_name}/versions/activate", summary="激活技能版本")
async def api_activate_version(request: Request, skill_name: str, body: ActivateVersionRequest) -> dict:
    """激活指定版本（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = await registry.activate_skill_version(skill_name, body.version)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_VALIDATION_ERROR, message=f"版本激活失败: {skill_name}@{body.version}")

    return {"skill_name": skill_name, "version": body.version, "activated": True}


@router.post("/{skill_name}/versions/rollback", summary="回滚技能版本")
async def api_rollback_version(request: Request, skill_name: str) -> dict:
    """回滚到上一版本（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    result = await registry.rollback_skill(skill_name)
    if result is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_VALIDATION_ERROR, message=f"版本回滚失败: {skill_name}")

    return {"skill_name": skill_name, "rolled_back_to": result}


@router.get("/{skill_name}/versions", summary="获取技能版本列表")
async def api_list_versions(skill_name: str) -> dict:
    """列出技能的所有版本"""
    registry = SkillRegistry.get_instance()
    versions = await registry.list_versions(skill_name)
    return {"skill_name": skill_name, "versions": versions}


@router.get("/{skill_name}/versions/active", summary="获取当前激活版本")
async def api_get_active_version(skill_name: str) -> dict:
    """获取技能的当前激活版本"""
    registry = SkillRegistry.get_instance()
    version = await registry.get_active_version(skill_name)
    return {"skill_name": skill_name, "active_version": version}


# ==================== 技能市场 ====================


@router.post("/{skill_name}/marketplace/publish", summary="发布技能到市场")
async def api_publish_to_marketplace(request: Request, skill_name: str, body: PublishMarketplaceRequest) -> dict:
    """发布技能到市场（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    entry = await registry.publish_to_marketplace(skill_name, body.category)
    if entry is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"技能 '{skill_name}' 不存在")

    return entry.model_dump()


@router.post("/{skill_name}/marketplace/unpublish", summary="从市场下架技能")
async def api_unpublish_from_marketplace(request: Request, skill_name: str) -> dict:
    """从市场下架技能（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    success = await registry.unpublish_from_marketplace(skill_name)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"技能 '{skill_name}' 不在市场中")

    return {"skill_name": skill_name, "unpublished": True}


@router.post("/{skill_name}/marketplace/install", summary="从市场安装技能")
async def api_install_from_marketplace(
    skill_name: str,
    target_version: str = Query(default="", description="目标版本号"),
) -> dict:
    """从市场安装技能"""
    registry = SkillRegistry.get_instance()
    doc = await registry.install_from_marketplace(skill_name, target_version)
    if doc is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_NOT_FOUND, message=f"技能 '{skill_name}' 不在市场中")

    return {
        "skill_name": skill_name,
        "version": doc.manifest.version,
        "installed": True,
    }


@router.post("/{skill_name}/marketplace/rate", summary="为技能评分")
async def api_rate_skill(skill_name: str, body: RateSkillRequest) -> dict:
    """为技能评分"""
    registry = SkillRegistry.get_instance()
    success = await registry.rate_skill(skill_name, body.user_id, body.score, body.comment)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.SKILL_VALIDATION_ERROR, message=f"评分失败: {skill_name}")

    return {"skill_name": skill_name, "score": body.score, "rated": True}


# ==================== 依赖解析 ====================


@router.get("/{skill_name}/resolve", summary="解析技能依赖")
async def api_resolve_dependencies(skill_name: str) -> dict:
    """解析技能的依赖关系，检查所有依赖是否满足"""
    from agent.core.skill_resolver import resolve_dependencies
    result = await resolve_dependencies(skill_name)
    return result.model_dump()


# ==================== 技能测试 ====================


@router.post("/{skill_name}/test", summary="运行技能测试")
async def api_run_skill_tests(request: Request, skill_name: str) -> dict:
    """运行技能测试用例（需要管理员权限）"""
    _require_admin(request)

    registry = SkillRegistry.get_instance()
    report = await registry.run_skill_tests(skill_name)
    return report.model_dump()


@router.get("/{skill_name}/test-cases", summary="获取技能测试用例")
async def api_get_test_cases(skill_name: str) -> dict:
    """获取技能的测试用例列表"""
    registry = SkillRegistry.get_instance()
    test_suite = registry.get_pack_test_suite(skill_name)
    return test_suite.model_dump()
