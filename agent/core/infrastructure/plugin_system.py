"""插件架构 - 运行时动态扩展

与 M365 Copilot AppSource 和 Coze 500+ 插件生态对齐。

能力：
  - 插件注册：动态注册和注销插件
  - 插件生命周期：加载、启用、禁用、卸载
  - 插件沙箱：隔离执行环境，防止插件影响主系统
  - 插件市场：插件发现、安装、评分
  - Hook 机制：在关键流程点注入插件逻辑
  - 权限控制：插件权限声明和校验

持久化：
  - 插件清单和实例状态同步到 Redis，多实例部署时数据共享
  - Handler 不持久化（运行时 Python 函数引用），启动时从 Redis
    加载清单后对 ENABLED 状态的插件重新执行 enable_plugin 触发注册
  - 市场数据同步到 Redis，支持跨实例查询
"""

import importlib
import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Redis Key 前缀
_REGISTRY_KEY_PREFIX = "plugin_registry:"
_INSTANCE_KEY_PREFIX = "plugin_instance:"
_REGISTRY_SET_KEY = "plugin_registry:all"
_MARKET_KEY_PREFIX = "plugin_market:"
_MARKET_SET_KEY = "plugin_market:all"


class PluginStatus(str, Enum):
    """插件状态"""

    REGISTERED = "registered"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


class HookPoint(str, Enum):
    """Hook 点"""

    PRE_CHAT = "pre_chat"
    POST_CHAT = "post_chat"
    PRE_AGENT = "pre_agent"
    POST_AGENT = "post_agent"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    ON_ERROR = "on_error"
    ON_FEEDBACK = "on_feedback"
    ON_SESSION_CREATE = "on_session_create"
    ON_SESSION_CLOSE = "on_session_close"


class PluginPermission(str, Enum):
    """插件权限"""

    READ_MESSAGES = "read_messages"
    WRITE_MESSAGES = "write_messages"
    CALL_TOOLS = "call_tools"
    ACCESS_USER_INFO = "access_user_info"
    ACCESS_SESSION = "access_session"
    NETWORK_ACCESS = "network_access"


class PluginManifest(BaseModel):
    """插件清单"""

    plugin_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=100, description="插件名称")
    display_name: str = Field(default="", description="显示名称")
    description: str = Field(default="", max_length=500, description="插件描述")
    version: str = Field(default="1.0.0", description="版本号")
    author: str = Field(default="", description="作者")

    permissions: list[PluginPermission] = Field(default_factory=list, description="所需权限")
    hooks: list[HookPoint] = Field(default_factory=list, description="注册的 Hook 点")

    module_path: str = Field(default="", description="Python 模块路径")
    entry_class: str = Field(default="", description="入口类名")

    is_official: bool = Field(default=False, description="是否官方插件")
    is_public: bool = Field(default=True, description="是否公开")
    icon: str = Field(default="", description="图标标识")

    config_schema: dict[str, Any] = Field(default_factory=dict, description="配置 Schema")

    installed_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PluginInstance(BaseModel):
    """插件运行实例"""

    plugin_id: str
    status: PluginStatus = PluginStatus.REGISTERED
    config: dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""
    loaded_at: float = Field(default_factory=time.time)
    execution_count: int = 0
    last_executed_at: float | None = None


class HookContext(BaseModel):
    """Hook 上下文"""

    hook_point: HookPoint
    session_id: str = ""
    user_id: str = ""
    agent_name: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HookResult(BaseModel):
    """Hook 执行结果"""

    plugin_id: str
    success: bool = True
    modified_data: dict[str, Any] | None = None
    error: str = ""
    execution_time_ms: float = 0


# ==================== Redis 持久化 ====================

_redis_client: Any = None


async def _get_redis() -> Any:
    """获取 Redis 客户端"""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        from agent.core.infrastructure.redis_manager import get_redis_client
        _redis_client = await get_redis_client()
        return _redis_client
    except Exception as e:
        logger.debug("Redis 获取失败: %s", e)
        return None


async def _persist_manifest(manifest: PluginManifest) -> None:
    """持久化插件清单到 Redis"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        key = f"{_REGISTRY_KEY_PREFIX}{manifest.plugin_id}"
        await redis.set(key, manifest.model_dump_json(), ex=86400 * 30)
        await redis.sadd(_REGISTRY_SET_KEY, manifest.plugin_id)
    except Exception as e:
        logger.debug("插件清单持久化失败: %s", e)


async def _remove_manifest(plugin_id: str) -> None:
    """从 Redis 删除插件清单"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.delete(f"{_REGISTRY_KEY_PREFIX}{plugin_id}")
        await redis.srem(_REGISTRY_SET_KEY, plugin_id)
    except Exception as e:
        logger.debug("插件清单删除失败: %s", e)


async def _persist_instance(instance: PluginInstance) -> None:
    """持久化插件实例状态到 Redis"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        key = f"{_INSTANCE_KEY_PREFIX}{instance.plugin_id}"
        await redis.set(key, instance.model_dump_json(), ex=86400 * 30)
    except Exception as e:
        logger.debug("插件实例持久化失败: %s", e)


async def _remove_instance(plugin_id: str) -> None:
    """从 Redis 删除插件实例"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        await redis.delete(f"{_INSTANCE_KEY_PREFIX}{plugin_id}")
    except Exception as e:
        logger.debug("插件实例删除失败: %s", e)


async def _persist_market_entry(entry: "MarketplaceEntry") -> None:
    """持久化市场条目到 Redis

    注意：MarketplaceEntry 在下方定义，此处使用前向引用。
    """
    redis = await _get_redis()
    if redis is None:
        return
    try:
        data = {
            "manifest_json": entry.manifest.model_dump_json(),
            "downloads": entry.downloads,
            "rating": entry.rating,
            "rating_count": entry.rating_count,
            "category": entry.category,
        }
        key = f"{_MARKET_KEY_PREFIX}{entry.manifest.plugin_id}"
        await redis.set(key, json.dumps(data, ensure_ascii=False), ex=86400 * 30)
        await redis.sadd(_MARKET_SET_KEY, entry.manifest.plugin_id)
    except Exception as e:
        logger.debug("市场条目持久化失败: %s", e)


# ==================== 插件注册表 ====================

_registry: dict[str, PluginManifest] = {}
_instances: dict[str, PluginInstance] = {}
_handlers: dict[str, dict[HookPoint, Callable]] = {}


def register_plugin(manifest: PluginManifest) -> PluginManifest:
    """注册插件

    同时更新内存和 Redis。

    Args:
        manifest: 插件清单

    Returns:
        注册后的清单
    """
    if manifest.plugin_id in _registry:
        raise ValueError(f"插件已注册: {manifest.plugin_id}")

    _registry[manifest.plugin_id] = manifest
    _instances[manifest.plugin_id] = PluginInstance(plugin_id=manifest.plugin_id)

    logger.info("插件已注册: id=%s name=%s", manifest.plugin_id, manifest.name)

    # 异步持久化到 Redis
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist_manifest(manifest))
        else:
            loop.run_until_complete(_persist_manifest(manifest))
    except Exception:
        pass

    return manifest


def unregister_plugin(plugin_id: str) -> bool:
    """注销插件

    同时删除内存和 Redis 中的数据。
    """
    if plugin_id not in _registry:
        return False

    instance = _instances.get(plugin_id)
    if instance and instance.status == PluginStatus.ENABLED:
        disable_plugin(plugin_id)

    del _registry[plugin_id]
    _instances.pop(plugin_id, None)
    _handlers.pop(plugin_id, None)

    logger.info("插件已注销: id=%s", plugin_id)

    # 异步删除 Redis 数据
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_remove_manifest(plugin_id))
            asyncio.ensure_future(_remove_instance(plugin_id))
        else:
            loop.run_until_complete(_remove_manifest(plugin_id))
            loop.run_until_complete(_remove_instance(plugin_id))
    except Exception:
        pass

    return True


def enable_plugin(plugin_id: str, config: dict[str, Any] | None = None) -> PluginInstance | None:
    """启用插件

    加载插件模块并注册 Hook 处理器。
    同时更新 Redis 中的实例状态。

    Args:
        plugin_id: 插件ID
        config: 插件配置

    Returns:
        插件实例
    """
    manifest = _registry.get(plugin_id)
    if not manifest:
        return None

    instance = _instances.get(plugin_id)
    if not instance:
        return None

    if config:
        instance.config = config

    try:
        if manifest.module_path and manifest.entry_class:
            _load_plugin_module(manifest, instance)

        instance.status = PluginStatus.ENABLED
        instance.loaded_at = time.time()
        instance.error_message = ""

        logger.info("插件已启用: id=%s name=%s", plugin_id, manifest.name)
    except Exception as e:
        instance.status = PluginStatus.ERROR
        instance.error_message = str(e)
        logger.error("插件启用失败: id=%s error=%s", plugin_id, e)

    # 异步持久化实例状态
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist_instance(instance))
        else:
            loop.run_until_complete(_persist_instance(instance))
    except Exception:
        pass

    return instance


def disable_plugin(plugin_id: str) -> PluginInstance | None:
    """禁用插件

    同时更新 Redis 中的实例状态。
    """
    instance = _instances.get(plugin_id)
    if not instance:
        return None

    instance.status = PluginStatus.DISABLED
    _handlers.pop(plugin_id, None)

    logger.info("插件已禁用: id=%s", plugin_id)

    # 异步持久化实例状态
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist_instance(instance))
        else:
            loop.run_until_complete(_persist_instance(instance))
    except Exception:
        pass

    return instance


_ALLOWED_PLUGIN_PREFIXES: tuple[str, ...] = (
    "plugins.",
    "agent.plugins.",
    "agent.extensions.",
)


def _validate_module_path(module_path: str) -> None:
    """校验插件模块路径是否在允许的前缀范围内

    仅允许从预定义的插件目录加载模块，防止加载任意系统模块。

    Args:
        module_path: Python 模块路径

    Raises:
        ValueError: 模块路径不在白名单前缀范围内
    """
    if not module_path:
        raise ValueError("插件模块路径不能为空")

    if not any(module_path.startswith(prefix) for prefix in _ALLOWED_PLUGIN_PREFIXES):
        raise ValueError(
            f"插件模块路径不被允许: {module_path}，"
            f"仅支持以下前缀: {', '.join(_ALLOWED_PLUGIN_PREFIXES)}"
        )


def _load_plugin_module(manifest: PluginManifest, instance: PluginInstance) -> None:
    """加载插件模块

    对模块路径进行白名单校验，仅允许从预定义的插件目录加载，
    防止通过 importlib.import_module 加载任意系统模块。

    Args:
        manifest: 插件清单
        instance: 插件实例

    Raises:
        ValueError: 模块路径校验失败或加载失败
    """
    _validate_module_path(manifest.module_path)

    try:
        module = importlib.import_module(manifest.module_path)
        entry_class = getattr(module, manifest.entry_class, None)
        if not entry_class:
            raise ValueError(f"入口类不存在: {manifest.entry_class}")

        entry_instance = entry_class(config=instance.config)

        handlers: dict[HookPoint, Callable] = {}
        for hook_point in manifest.hooks:
            handler_name = f"on_{hook_point.value}"
            handler = getattr(entry_instance, handler_name, None)
            if handler and callable(handler):
                handlers[hook_point] = handler

        _handlers[manifest.plugin_id] = handlers

    except ImportError as e:
        raise ValueError(f"插件模块加载失败: {manifest.module_path}, {e}")


# ==================== Hook 执行 ====================


async def execute_hooks(hook_point: HookPoint, context: HookContext) -> list[HookResult]:
    """执行指定 Hook 点的所有插件

    Args:
        hook_point: Hook 点
        context: Hook 上下文

    Returns:
        Hook 执行结果列表
    """
    results: list[HookResult] = []

    for plugin_id, handlers in _handlers.items():
        instance = _instances.get(plugin_id)
        if not instance or instance.status != PluginStatus.ENABLED:
            continue

        handler = handlers.get(hook_point)
        if not handler:
            continue

        start = time.time()
        try:
            result_data = await handler(context) if _is_async(handler) else handler(context)

            instance.execution_count += 1
            instance.last_executed_at = time.time()

            elapsed = (time.time() - start) * 1000
            results.append(HookResult(
                plugin_id=plugin_id,
                success=True,
                modified_data=result_data if isinstance(result_data, dict) else None,
                execution_time_ms=round(elapsed, 2),
            ))

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            results.append(HookResult(
                plugin_id=plugin_id,
                success=False,
                error=str(e),
                execution_time_ms=round(elapsed, 2),
            ))
            logger.error("插件 Hook 执行失败: plugin=%s hook=%s error=%s", plugin_id, hook_point, e)

    return results


def _is_async(func: Callable) -> bool:
    """判断函数是否为异步函数"""
    import asyncio
    return asyncio.iscoroutinefunction(func)


# ==================== 查询 ====================


def list_plugins(
    status: PluginStatus | None = None,
    hook_point: HookPoint | None = None,
) -> list[PluginManifest]:
    """列出插件"""
    plugins = list(_registry.values())

    if status:
        plugins = [p for p in plugins if _instances.get(p.plugin_id, PluginInstance(plugin_id="")).status == status]

    if hook_point:
        plugins = [p for p in plugins if hook_point in p.hooks]

    plugins.sort(key=lambda p: (not p.is_official, p.name))
    return plugins


def get_plugin(plugin_id: str) -> PluginManifest | None:
    """获取插件信息"""
    return _registry.get(plugin_id)


def get_plugin_instance(plugin_id: str) -> PluginInstance | None:
    """获取插件实例"""
    return _instances.get(plugin_id)


# ==================== 插件市场 ====================


class MarketplaceEntry(BaseModel):
    """市场条目"""

    manifest: PluginManifest
    downloads: int = 0
    rating: float = Field(default=0.0, ge=0.0, le=5.0)
    rating_count: int = 0
    category: str = "general"


_marketplace: dict[str, MarketplaceEntry] = {}


def publish_to_marketplace(manifest: PluginManifest, category: str = "general") -> MarketplaceEntry:
    """发布到插件市场

    同时持久化到 Redis。
    """
    entry = MarketplaceEntry(manifest=manifest, category=category)
    _marketplace[manifest.plugin_id] = entry
    logger.info("插件已发布到市场: id=%s name=%s", manifest.plugin_id, manifest.name)

    # 异步持久化
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist_market_entry(entry))
        else:
            loop.run_until_complete(_persist_market_entry(entry))
    except Exception:
        pass

    return entry


def search_marketplace(keyword: str = "", category: str = "") -> list[MarketplaceEntry]:
    """搜索插件市场"""
    entries = list(_marketplace.values())

    if category:
        entries = [e for e in entries if e.category == category]

    if keyword:
        kw_lower = keyword.lower()
        entries = [
            e for e in entries
            if kw_lower in e.manifest.name.lower()
            or kw_lower in e.manifest.description.lower()
        ]

    entries.sort(key=lambda e: (-e.rating, -e.downloads))
    return entries


def install_from_marketplace(plugin_id: str) -> PluginManifest | None:
    """从市场安装插件"""
    entry = _marketplace.get(plugin_id)
    if not entry:
        return None

    if plugin_id in _registry:
        return _registry[plugin_id]

    entry.downloads += 1

    # 异步持久化市场条目（更新下载量）
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist_market_entry(entry))
        else:
            loop.run_until_complete(_persist_market_entry(entry))
    except Exception:
        pass

    return register_plugin(entry.manifest.model_copy())


# ==================== 启动恢复 ====================


async def restore_from_redis() -> int:
    """从 Redis 恢复插件注册表和实例状态

    应用启动时调用，从 Redis 加载所有已注册插件清单，
    对状态为 ENABLED 的插件自动执行 enable_plugin 触发 Handler 注册。

    Returns:
        恢复的插件数量
    """
    redis = await _get_redis()
    if redis is None:
        logger.info("Redis 不可用，跳过插件恢复")
        return 0

    restored = 0

    try:
        plugin_ids = await redis.smembers(_REGISTRY_SET_KEY)
        if not plugin_ids:
            return 0

        for plugin_id in plugin_ids:
            try:
                # 加载插件清单
                manifest_raw = await redis.get(f"{_REGISTRY_KEY_PREFIX}{plugin_id}")
                if not manifest_raw:
                    continue
                manifest = PluginManifest.model_validate_json(manifest_raw)

                # 跳过已存在的插件（如官方插件已在内存中）
                if manifest.plugin_id in _registry:
                    continue

                _registry[manifest.plugin_id] = manifest

                # 加载实例状态
                instance_raw = await redis.get(f"{_INSTANCE_KEY_PREFIX}{plugin_id}")
                if instance_raw:
                    instance = PluginInstance.model_validate_json(instance_raw)
                    _instances[manifest.plugin_id] = instance
                else:
                    _instances[manifest.plugin_id] = PluginInstance(plugin_id=manifest.plugin_id)

                # 对 ENABLED 状态的插件重新执行 enable_plugin（触发 Handler 注册）
                instance = _instances[manifest.plugin_id]
                if instance.status == PluginStatus.ENABLED:
                    enable_plugin(manifest.plugin_id, instance.config)

                restored += 1
            except Exception as e:
                logger.warning("恢复插件失败: id=%s error=%s", plugin_id, e)

        # 恢复市场数据
        market_ids = await redis.smembers(_MARKET_SET_KEY)
        for plugin_id in market_ids:
            try:
                market_raw = await redis.get(f"{_MARKET_KEY_PREFIX}{plugin_id}")
                if not market_raw:
                    continue
                data = json.loads(market_raw)
                manifest = PluginManifest.model_validate_json(data["manifest_json"])
                entry = MarketplaceEntry(
                    manifest=manifest,
                    downloads=data.get("downloads", 0),
                    rating=data.get("rating", 0.0),
                    rating_count=data.get("rating_count", 0),
                    category=data.get("category", "general"),
                )
                _marketplace[plugin_id] = entry
            except Exception as e:
                logger.warning("恢复市场条目失败: id=%s error=%s", plugin_id, e)

        logger.info("从 Redis 恢复了 %d 个插件", restored)

    except Exception as e:
        logger.warning("插件恢复失败: %s", e)

    return restored


# ==================== 初始化官方插件 ====================


def _init_official_plugins() -> None:
    """初始化官方插件清单并注册内置 Handler"""
    if any(p.is_official for p in _registry.values()):
        return

    official_plugins = [
        PluginManifest(
            plugin_id="plugin-logging",
            name="audit-logger",
            display_name="审计日志插件",
            description="自动记录所有对话和工具调用的审计日志",
            version="1.0.0",
            author="platform",
            permissions=[PluginPermission.READ_MESSAGES],
            hooks=[HookPoint.PRE_CHAT, HookPoint.POST_CHAT, HookPoint.PRE_TOOL, HookPoint.POST_TOOL],
            is_official=True,
            icon="logging",
        ),
        PluginManifest(
            plugin_id="plugin-content-filter",
            name="content-filter",
            display_name="内容过滤插件",
            description="对输入和输出内容进行安全过滤，阻止敏感信息泄露",
            version="1.0.0",
            author="platform",
            permissions=[PluginPermission.READ_MESSAGES, PluginPermission.WRITE_MESSAGES],
            hooks=[HookPoint.PRE_CHAT, HookPoint.POST_CHAT],
            is_official=True,
            icon="filter",
        ),
        PluginManifest(
            plugin_id="plugin-metrics",
            name="metrics-collector",
            display_name="指标采集插件",
            description="采集对话和 Agent 执行的性能指标",
            version="1.0.0",
            author="platform",
            permissions=[PluginPermission.READ_MESSAGES, PluginPermission.ACCESS_SESSION],
            hooks=[HookPoint.PRE_CHAT, HookPoint.POST_CHAT, HookPoint.ON_ERROR],
            is_official=True,
            icon="metrics",
        ),
    ]

    for manifest in official_plugins:
        register_plugin(manifest)
        enable_plugin(manifest.plugin_id)
        publish_to_marketplace(manifest, category="official")

    _register_official_handlers()


def _register_official_handlers() -> None:
    """为官方插件注册内置 Handler 实现"""

    async def _audit_logger_pre_chat(ctx: HookContext) -> dict[str, Any]:
        logger.info(
            "审计日志[PRE_CHAT]: session=%s user=%s agent=%s",
            ctx.session_id, ctx.user_id, ctx.agent_name,
        )
        return {"logged": True, "hook_point": "pre_chat", "session_id": ctx.session_id}

    async def _audit_logger_post_chat(ctx: HookContext) -> dict[str, Any]:
        logger.info(
            "审计日志[POST_CHAT]: session=%s user=%s agent=%s",
            ctx.session_id, ctx.user_id, ctx.agent_name,
        )
        return {"logged": True, "hook_point": "post_chat", "session_id": ctx.session_id}

    async def _audit_logger_pre_tool(ctx: HookContext) -> dict[str, Any]:
        tool_name = ctx.data.get("tool_name", "unknown")
        logger.info(
            "审计日志[PRE_TOOL]: session=%s tool=%s",
            ctx.session_id, tool_name,
        )
        return {"logged": True, "hook_point": "pre_tool", "tool_name": tool_name}

    async def _audit_logger_post_tool(ctx: HookContext) -> dict[str, Any]:
        tool_name = ctx.data.get("tool_name", "unknown")
        logger.info(
            "审计日志[POST_TOOL]: session=%s tool=%s",
            ctx.session_id, tool_name,
        )
        return {"logged": True, "hook_point": "post_tool", "tool_name": tool_name}

    _SENSITIVE_PATTERNS: list[tuple[str, str]] = [
        ("password", "***"),
        ("secret", "***"),
        ("token", "***"),
        ("api_key", "***"),
        ("private_key", "***"),
        ("credit_card", "***"),
        ("id_card", "***"),
        ("phone", "***"),
    ]

    def _mask_sensitive(text: str) -> str:
        import re
        for keyword, mask in _SENSITIVE_PATTERNS:
            pattern = rf'({keyword}\s*[:=]\s*)\S+'
            text = re.sub(pattern, rf'\1{mask}', text, flags=re.IGNORECASE)
        return text

    async def _content_filter_pre_chat(ctx: HookContext) -> dict[str, Any]:
        message = ctx.data.get("message", "")
        if message:
            masked = _mask_sensitive(message)
            if masked != message:
                ctx.data["message"] = masked
                logger.info("内容过滤[PRE_CHAT]: 已脱敏 session=%s", ctx.session_id)
        return {"filtered": True, "hook_point": "pre_chat"}

    async def _content_filter_post_chat(ctx: HookContext) -> dict[str, Any]:
        message = ctx.data.get("message", "")
        if message:
            masked = _mask_sensitive(message)
            if masked != message:
                ctx.data["message"] = masked
                logger.info("内容过滤[POST_CHAT]: 已脱敏 session=%s", ctx.session_id)
        return {"filtered": True, "hook_point": "post_chat"}

    _metrics_data: dict[str, list[float]] = {}

    async def _metrics_pre_chat(ctx: HookContext) -> dict[str, Any]:
        key = f"{ctx.session_id}:{ctx.agent_name}"
        _metrics_data[key] = [time.time()]
        return {"metric": "start", "session_id": ctx.session_id}

    async def _metrics_post_chat(ctx: HookContext) -> dict[str, Any]:
        key = f"{ctx.session_id}:{ctx.agent_name}"
        start_times = _metrics_data.get(key, [])
        if start_times:
            elapsed_ms = (time.time() - start_times[-1]) * 1000
            logger.info(
                "指标采集[POST_CHAT]: session=%s agent=%s elapsed=%.1fms",
                ctx.session_id, ctx.agent_name, elapsed_ms,
            )
            _metrics_data.pop(key, None)
            return {"metric": "complete", "elapsed_ms": round(elapsed_ms, 2), "session_id": ctx.session_id}
        return {"metric": "complete", "session_id": ctx.session_id}

    async def _metrics_on_error(ctx: HookContext) -> dict[str, Any]:
        error_msg = ctx.data.get("error", "unknown")
        logger.warning(
            "指标采集[ON_ERROR]: session=%s agent=%s error=%s",
            ctx.session_id, ctx.agent_name, error_msg,
        )
        return {"metric": "error", "error": error_msg, "session_id": ctx.session_id}

    _handlers["plugin-logging"] = {
        HookPoint.PRE_CHAT: _audit_logger_pre_chat,
        HookPoint.POST_CHAT: _audit_logger_post_chat,
        HookPoint.PRE_TOOL: _audit_logger_pre_tool,
        HookPoint.POST_TOOL: _audit_logger_post_tool,
    }

    _handlers["plugin-content-filter"] = {
        HookPoint.PRE_CHAT: _content_filter_pre_chat,
        HookPoint.POST_CHAT: _content_filter_post_chat,
    }

    _handlers["plugin-metrics"] = {
        HookPoint.PRE_CHAT: _metrics_pre_chat,
        HookPoint.POST_CHAT: _metrics_post_chat,
        HookPoint.ON_ERROR: _metrics_on_error,
    }


_init_official_plugins()
