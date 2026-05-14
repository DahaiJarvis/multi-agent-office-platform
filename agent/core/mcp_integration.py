"""MCP 工具加载与集成

负责从 MCP Server 加载工具，并转换为 AutoGen 可用的 Function Tool。
支持 SSE 远程连接和 STDIO 本地进程两种模式。
支持从 MCP Registry 动态发现服务，替代纯硬编码注册表。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from autogen_ext.tools.mcp import SseServerParams, StdioServerParams, mcp_server_tools

from agent.core.config import get_settings

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


# MCP 服务注册表（静态默认配置，可被 Registry 动态覆盖）
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
        description="知识库 MCP 服务 - 由智能文档助手提供",
        transport="sse",
        url="http://localhost:9010/sse",
    ),
    "web_search": MCPServerConfig(
        name="web-search-mcp-server",
        description="网络搜索 MCP 服务 - 提供联网搜索能力",
        transport="sse",
        url="http://localhost:9011/sse",
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
    "KnowledgeAgent": ["knowledge"],
}

# 工具缓存
_tool_cache: dict[str, list[Any]] = {}
# 缓存过期时间戳（失败缓存 60s 后过期，允许重新尝试连接）
_cache_expiry: dict[str, float] = {}
_CACHE_TTL_SECONDS = 60
_adapter_cache: dict[str, list[Any]] = {}

# Registry 同步状态
_registry_synced: bool = False


async def discover_from_registry() -> dict[str, MCPServerConfig]:
    """从 MCP Registry 动态发现服务

    向 Registry 查询已注册的服务列表，将结果合并到本地注册表。
    Registry 中的服务信息会覆盖本地静态配置（URL 可能动态变化）。

    Returns:
        从 Registry 发现的服务配置字典
    """
    settings = get_settings()
    registry_url = settings.mcp_registry_url

    discovered: dict[str, MCPServerConfig] = {}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{registry_url}/services")
            if response.status_code != 200:
                logger.warning("Registry 查询失败: status=%d", response.status_code)
                return discovered

            data = response.json()
            services = data.get("data", [])

            for svc in services:
                svc_name = svc.get("name", "")
                svc_url = svc.get("url", "")
                svc_transport = svc.get("transport", "sse")
                svc_description = svc.get("description", "")
                svc_status = svc.get("status", "healthy")

                if not svc_name or not svc_url:
                    continue

                # 通过服务名匹配本地注册表的 key
                local_key = _match_local_key(svc_name)
                config = MCPServerConfig(
                    name=svc_name,
                    description=svc_description or (MCP_SERVER_REGISTRY.get(local_key, MCPServerConfig(name="", description="")).description),
                    transport=svc_transport,
                    url=svc_url,
                    enabled=svc_status == "healthy",
                )

                discovered[local_key or svc_name] = config

            # 合并到本地注册表
            for key, config in discovered.items():
                MCP_SERVER_REGISTRY[key] = config
                # 清除旧缓存，强制重新加载
                _tool_cache.pop(key, None)

            global _registry_synced
            _registry_synced = True

            logger.info("Registry 同步完成: 发现 %d 个服务", len(discovered))

    except Exception as e:
        logger.warning("Registry 同步失败，使用本地静态配置: %s", e)

    return discovered


def _match_local_key(service_name: str) -> str:
    """将 Registry 服务名匹配到本地注册表 key

    Args:
        service_name: Registry 中的服务名，如 "oa-mcp-server"

    Returns:
        本地注册表 key，如 "oa"
    """
    name_mapping = {
        "oa-mcp-server": "oa",
        "email-mcp-server": "email",
        "calendar-mcp-server": "calendar",
        "crm-mcp-server": "crm",
        "approval-mcp-server": "approval",
        "im-mcp-server": "im",
        "doc-mcp-server": "doc",
        "hr-mcp-server": "hr",
        "finance-mcp-server": "finance",
        "knowledge-mcp-server": "knowledge",
        "web-search-mcp-server": "web_search",
    }
    return name_mapping.get(service_name, "")


async def ensure_registry_synced() -> None:
    """确保已从 Registry 同步过服务信息

    首次调用时自动触发同步，后续调用跳过。
    """
    global _registry_synced
    if not _registry_synced:
        await discover_from_registry()


async def _check_sse_endpoint(url: str, timeout: float = 3.0) -> bool:
    """快速检测 SSE 端点是否可达

    在正式建立 SSE 连接之前，先通过 HTTP 检测端点是否可访问。
    避免对不可达的服务发起长时间 SSE 连接导致系统卡住。

    Args:
        url: SSE 端点 URL，如 http://localhost:9100/sse
        timeout: 检测超时秒数

    Returns:
        True 表示端点可达，False 表示不可达
    """
    try:
        import httpx

        base_url = url.replace("/sse", "")
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(base_url)
            return response.status_code < 500
    except Exception:
        return False


async def load_mcp_tools(server_names: list[str]) -> list[Any]:
    """从指定的 MCP 服务加载工具列表

    加载前自动确保 Registry 已同步，使动态发现的服务可用。
    连接失败的服务会被标记为不可用并缓存空结果，避免反复重试。

    Args:
        server_names: MCP 服务名称列表，如 ["oa", "email"]

    Returns:
        AutoGen Function Tool 列表
    """
    await ensure_registry_synced()

    all_tools: list[Any] = []

    for name in server_names:
        config = MCP_SERVER_REGISTRY.get(name)
        if not config:
            logger.warning("MCP 服务 %s 未注册，跳过", name)
            continue

        # 检查缓存是否过期（失败缓存过期后允许重新尝试连接）
        if name in _tool_cache:
            if name in _cache_expiry and time.time() >= _cache_expiry[name]:
                _tool_cache.pop(name, None)
                _cache_expiry.pop(name, None)
                config.enabled = True
            else:
                all_tools.extend(_tool_cache[name])
                continue

        if not config.enabled:
            logger.warning("MCP 服务 %s 已禁用，跳过", name)
            continue

        try:
            tools = await _connect_and_load(name, config)
            _tool_cache[name] = tools
            _cache_expiry.pop(name, None)
            all_tools.extend(tools)
            logger.info("成功加载 MCP 服务 %s 的 %d 个工具", name, len(tools))
        except Exception as e:
            logger.error("加载 MCP 服务 %s 失败: %s", name, e)
            _tool_cache[name] = []
            _cache_expiry[name] = time.time() + _CACHE_TTL_SECONDS

    return all_tools


async def _connect_and_load(service_key: str, config: MCPServerConfig) -> list[Any]:
    """连接 MCP 服务并加载工具

    使用 mcp_server_tools() 获取 AutoGen 兼容的工具适配器列表。
    返回的 SseMcpToolAdapter / StdioMcpToolAdapter 可直接传给 AssistantAgent。

    对于 knowledge 服务，SSE 连接时传递 X-MCP-API-Key 请求头，
    以通过智能文档助手 MCP Server 的 MCPAuthMiddleware 认证。

    连接前先进行 SSE 端点预检，避免对不可达服务发起长时间连接。

    Args:
        service_key: 服务标识，如 "knowledge"
        config: MCP 服务配置

    Raises:
        ConnectionError: SSE 端点不可达
        TimeoutError: 连接或加载工具超时
    """
    settings = get_settings()

    if config.transport == "sse":
        reachable = await _check_sse_endpoint(config.url)
        if not reachable:
            config.enabled = False
            raise ConnectionError(f"MCP 服务 [{service_key}] SSE 端点不可达: {config.url}")

        connect_kwargs: dict[str, Any] = {"url": config.url, "timeout": 5, "sse_read_timeout": 30}
        if service_key == "knowledge" and settings.mcp_api_key:
            connect_kwargs["headers"] = {"X-MCP-API-Key": settings.mcp_api_key}
        server_params = SseServerParams(**connect_kwargs)
    else:
        server_params = StdioServerParams(command=config.command, args=config.args, env=config.env)

    try:
        tools = await asyncio.wait_for(
            mcp_server_tools(server_params),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.error("MCP 服务 %s 连接超时（15s），标记为不可用", config.name)
        config.enabled = False
        raise TimeoutError(f"MCP 服务 [{service_key}] 连接超时，请确认服务是否正常运行")
    except Exception as e:
        logger.error("MCP 服务 %s 连接异常: %s，标记为不可用", config.name, e)
        config.enabled = False
        raise

    _adapter_cache[config.name] = tools
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
    for name, adapters in _adapter_cache.items():
        try:
            for adapter in adapters:
                if hasattr(adapter, "close"):
                    await adapter.close()
            logger.info("已关闭 MCP 服务 %s 的连接", name)
        except Exception as e:
            logger.error("关闭 MCP 服务 %s 连接失败: %s", name, e)

    _adapter_cache.clear()
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


async def check_tool_health(server_name: str) -> bool:
    """检查 MCP 服务健康状态

    通过尝试连接 MCP 服务的 SSE 端点判断服务是否可用。

    Args:
        server_name: MCP 服务名称，如 "oa"

    Returns:
        服务是否健康
    """
    config = MCP_SERVER_REGISTRY.get(server_name)
    if not config:
        return False

    if not config.enabled:
        return False

    try:
        import httpx

        if config.transport == "sse" and config.url:
            health_url = config.url.replace("/sse", "/health")
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(health_url)
                return response.status_code == 200

        return True
    except Exception as e:
        logger.warning("MCP 服务 %s 健康检查失败: %s", server_name, e)
        return False


async def call_tool_with_timeout(
    tool_func,
    tool_input: dict[str, Any],
    timeout: int | None = None,
    max_retries: int | None = None,
    server_name: str = "",
    tool_name: str = "",
    session_id: str = "",
    agent_name: str = "",
) -> Any:
    """带超时、重试、熔断、校验和溯源的工具调用

    在 FunctionTool 的执行函数中包裹超时控制、重试逻辑、熔断保护、
    响应校验和调用溯源。

    Args:
        tool_func: 工具执行函数
        tool_input: 工具输入参数
        timeout: 超时秒数（None 则使用配置默认值）
        max_retries: 最大重试次数（None 则使用配置默认值）
        server_name: MCP 服务名（用于熔断、校验和溯源）
        tool_name: 工具名（用于校验和溯源）
        agent_name: 调用方 Agent 名称（用于溯源）
        session_id: 会话ID（用于溯源）

    Returns:
        工具执行结果（已校验和清洗）

    Raises:
        TimeoutError: 工具调用超时
        CircuitOpenError: 熔断器打开，服务不可用
        Exception: 重试耗尽后抛出最后一次异常
    """
    import asyncio

    settings = get_settings()
    timeout = timeout or settings.tool_execution_timeout
    max_retries = max_retries or settings.tool_max_retries
    backoff = settings.tool_retry_backoff

    # 熔断器检查
    circuit_breaker = None
    if server_name:
        try:
            from agent.core.circuit_breaker import get_circuit_breaker, CircuitOpenError
            circuit_breaker = get_circuit_breaker(f"mcp_{server_name}")
            if not circuit_breaker.allow_request():
                raise CircuitOpenError(
                    f"mcp_{server_name}",
                    circuit_breaker.config.recovery_timeout,
                    f"MCP 服务 [{server_name}] 当前不可用，请稍后重试"
                )
        except Exception:
            pass

    # 启动溯源
    trace_id = ""
    if server_name and tool_name:
        try:
            from agent.core.mcp_tracing import get_mcp_tracer
            tracer = get_mcp_tracer()
            trace_id = await tracer.start_call(
                server_name=server_name,
                tool_name=tool_name,
                session_id=session_id,
                agent_name=agent_name,
                input_params=tool_input,
            )
        except Exception:
            pass

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.wait_for(
                tool_func(**tool_input),
                timeout=timeout,
            )

            # 响应校验
            validation_passed = True
            validation_confidence = 1.0
            if server_name and tool_name:
                try:
                    from agent.core.mcp_validator import validate_mcp_response
                    validation = await validate_mcp_response(server_name, tool_name, result)
                    validation_passed = validation.is_valid
                    validation_confidence = validation.confidence
                    if validation.is_valid:
                        result = validation.sanitized_data
                    else:
                        logger.warning(
                            "MCP 响应校验失败: server=%s tool=%s errors=%s",
                            server_name, tool_name, validation.errors,
                        )
                except Exception:
                    pass

            # 熔断器记录成功
            if circuit_breaker:
                circuit_breaker.record_success()

            # 结束溯源 - 成功
            if trace_id:
                try:
                    from agent.core.mcp_tracing import get_mcp_tracer
                    tracer = get_mcp_tracer()
                    await tracer.end_call(
                        trace_id=trace_id,
                        status="success",
                        response=result,
                        validation_passed=validation_passed,
                        validation_confidence=validation_confidence,
                    )
                except Exception:
                    pass

            return result

        except asyncio.TimeoutError:
            last_error = TimeoutError(f"工具调用超时 ({timeout}s)")
            logger.warning(
                "工具调用超时: attempt=%d/%d timeout=%ds",
                attempt + 1, max_retries + 1, timeout,
            )
        except Exception as e:
            last_error = e
            logger.warning(
                "工具调用失败: attempt=%d/%d error=%s",
                attempt + 1, max_retries + 1, str(e),
            )

        # 重试退避
        if attempt < max_retries:
            wait_time = backoff * (2 ** attempt)
            await asyncio.sleep(wait_time)

    # 熔断器记录失败
    if circuit_breaker:
        circuit_breaker.record_failure()

    # 结束溯源 - 失败
    if trace_id:
        try:
            from agent.core.mcp_tracing import get_mcp_tracer
            tracer = get_mcp_tracer()
            await tracer.end_call(
                trace_id=trace_id,
                status="error",
                error=str(last_error),
            )
        except Exception:
            pass

    raise last_error or Exception("工具调用失败")
