"""插件架构路由"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.core.plugin_system import (
    register_plugin,
    unregister_plugin,
    enable_plugin,
    disable_plugin,
    list_plugins,
    get_plugin,
    get_plugin_instance,
    execute_hooks,
    search_marketplace,
    install_from_marketplace,
    PluginManifest,
    PluginInstance,
    PluginStatus,
    PluginPermission,
    HookPoint,
    HookContext,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["插件"])


class RegisterPluginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    display_name: str = ""
    description: str = Field(default="", max_length=500)
    version: str = "1.0.0"
    author: str = ""
    permissions: list[PluginPermission] = Field(default_factory=list)
    hooks: list[HookPoint] = Field(default_factory=list)
    module_path: str = ""
    entry_class: str = ""
    is_public: bool = True
    icon: str = ""
    config_schema: dict = Field(default_factory=dict)


class EnablePluginRequest(BaseModel):
    config: dict = Field(default_factory=dict)


class ExecuteHooksRequest(BaseModel):
    hook_point: HookPoint
    session_id: str = ""
    user_id: str = ""
    agent_name: str = ""
    data: dict = Field(default_factory=dict)


@router.get("", response_model=list[PluginManifest], summary="列出插件")
async def api_list_plugins(
    status: PluginStatus | None = None,
    hook_point: HookPoint | None = None,
) -> list[PluginManifest]:
    """列出插件"""
    return list_plugins(status=status, hook_point=hook_point)


@router.get("/marketplace", summary="搜索插件市场")
async def api_search_marketplace(keyword: str = "", category: str = "") -> list[dict]:
    """搜索插件市场"""
    entries = search_marketplace(keyword, category)
    return [
        {
            "plugin_id": e.manifest.plugin_id,
            "name": e.manifest.name,
            "display_name": e.manifest.display_name,
            "description": e.manifest.description,
            "version": e.manifest.version,
            "author": e.manifest.author,
            "downloads": e.downloads,
            "rating": e.rating,
            "category": e.category,
            "icon": e.manifest.icon,
        }
        for e in entries
    ]


@router.get("/hooks", summary="列出Hook点")
async def api_list_hooks() -> dict:
    """列出 Hook 点"""
    return {
        "hooks": [
            {"id": "pre_chat", "name": "对话前", "description": "用户消息处理前"},
            {"id": "post_chat", "name": "对话后", "description": "Agent 响应生成后"},
            {"id": "pre_agent", "name": "Agent执行前", "description": "Agent 执行前"},
            {"id": "post_agent", "name": "Agent执行后", "description": "Agent 执行后"},
            {"id": "pre_tool", "name": "工具调用前", "description": "MCP 工具调用前"},
            {"id": "post_tool", "name": "工具调用后", "description": "MCP 工具调用后"},
            {"id": "on_error", "name": "错误处理", "description": "发生错误时"},
            {"id": "on_feedback", "name": "反馈处理", "description": "用户提交反馈时"},
            {"id": "on_session_create", "name": "会话创建", "description": "新会话创建时"},
            {"id": "on_session_close", "name": "会话关闭", "description": "会话关闭时"},
        ]
    }


@router.get("/{plugin_id}", response_model=PluginManifest, summary="获取插件详情")
async def api_get_plugin(plugin_id: str) -> PluginManifest:
    """获取插件详情"""
    manifest = get_plugin(plugin_id)
    if not manifest:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="插件不存在")
    return manifest


@router.get("/{plugin_id}/instance", response_model=PluginInstance, summary="获取插件实例状态")
async def api_get_plugin_instance(plugin_id: str) -> PluginInstance:
    """获取插件实例状态"""
    instance = get_plugin_instance(plugin_id)
    if not instance:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="插件实例不存在")
    return instance


@router.post("", response_model=PluginManifest, summary="注册插件")
async def api_register_plugin(request: RegisterPluginRequest) -> PluginManifest:
    """注册插件"""
    manifest = PluginManifest(**request.model_dump())
    return register_plugin(manifest)


@router.post("/{plugin_id}/enable", response_model=PluginInstance, summary="启用插件")
async def api_enable_plugin(plugin_id: str, request: EnablePluginRequest) -> PluginInstance:
    """启用插件"""
    instance = enable_plugin(plugin_id, request.config)
    if not instance:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="插件不存在")
    return instance


@router.post("/{plugin_id}/disable", response_model=PluginInstance, summary="禁用插件")
async def api_disable_plugin(plugin_id: str) -> PluginInstance:
    """禁用插件"""
    instance = disable_plugin(plugin_id)
    if not instance:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="插件不存在")
    return instance


@router.delete("/{plugin_id}", summary="注销插件")
async def api_unregister_plugin(plugin_id: str) -> dict:
    """注销插件"""
    success = unregister_plugin(plugin_id)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="插件不存在")
    return {"status": "ok"}


@router.post("/{plugin_id}/install", response_model=PluginManifest, summary="从市场安装插件")
async def api_install_plugin(plugin_id: str) -> PluginManifest:
    """从市场安装插件"""
    manifest = install_from_marketplace(plugin_id)
    if not manifest:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="市场插件不存在")
    return manifest


@router.post("/hooks/execute", summary="执行Hook")
async def api_execute_hooks(request: ExecuteHooksRequest) -> list[dict]:
    """执行 Hook"""
    context = HookContext(
        hook_point=request.hook_point,
        session_id=request.session_id,
        user_id=request.user_id,
        agent_name=request.agent_name,
        data=request.data,
    )
    results = await execute_hooks(request.hook_point, context)
    return [r.model_dump() for r in results]
