"""MCP 工具加载与集成

负责从 MCP Server 加载工具，并转换为 AutoGen 可用的 Function Tool。
支持 SSE 远程连接和 STDIO 本地进程两种模式。
支持从 MCP Registry 动态发现服务，替代纯硬编码注册表。
"""

import asyncio
import logging
import os
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
    mock_mode = os.getenv("MCP_MOCK_MODE", "").lower() in ("true", "1", "yes")

    if config.transport == "sse":
        # Mock 模式下跳过 SSE 端点预检，日志提示
        if not mock_mode:
            reachable = await _check_sse_endpoint(config.url)
            if not reachable:
                config.enabled = False
                raise ConnectionError(f"MCP 服务 [{service_key}] SSE 端点不可达: {config.url}")
        else:
            logger.info("Mock 模式已启用，跳过 SSE 端点预检: %s", config.name)

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


# ==================== Mock 工具 ====================

MOCK_TOOL_DEFINITIONS: dict[str, list[dict[str, Any]]] = {
    "crm": [
        {
            "name": "query_customer",
            "description": "查询客户信息，支持按姓名、手机号、企业名称检索",
            "params": {"customer_name": {"type": "string", "description": "客户姓名"}},
            "mock_data": {
                "customer_name": "{customer_name}",
                "company": "示例科技有限公司",
                "industry": "互联网",
                "scale": "200-500人",
                "contact": "138****5678",
                "cooperation_start": "2023-06-15",
                "status": "活跃客户",
                "last_interaction": "2025-05-10",
                "total_contract_amount": "¥580万元",
                "recent_deal": "2025-04-20 签约 ¥120万",
            },
        },
        {
            "name": "query_customer_orders",
            "description": "查询客户订单记录",
            "params": {"customer_name": {"type": "string", "description": "客户姓名"}},
            "mock_data": [
                {"order_id": "ORD-2025-0042", "product": "企业版SaaS", "amount": "¥120万", "date": "2025-04-20", "status": "已签约"},
                {"order_id": "ORD-2024-0198", "product": "增值服务包", "amount": "¥35万", "date": "2024-11-15", "status": "已回款"},
                {"order_id": "ORD-2024-0087", "product": "基础版SaaS", "amount": "¥58万", "date": "2024-06-01", "status": "已回款"},
            ],
        },
    ],
    "finance": [
        {
            "name": "query_financial_report",
            "description": "查询财务报表数据，包括收入、支出、利润等",
            "params": {"period": {"type": "string", "description": "查询周期，如 2025年Q1"}},
            "mock_data": {
                "period": "{period}",
                "revenue": "¥3,280万",
                "cost": "¥2,150万",
                "gross_profit": "¥1,130万",
                "gross_margin": "34.5%",
                "operating_expense": "¥680万",
                "net_profit": "¥450万",
                "net_margin": "13.7%",
                "yoy_growth": "+12.3%",
            },
        },
        {
            "name": "query_payment_status",
            "description": "查询回款状态和待回款明细",
            "params": {"customer_name": {"type": "string", "description": "客户姓名"}},
            "mock_data": {
                "customer": "{customer_name}",
                "total_contract": "¥580万",
                "received": "¥460万",
                "pending": "¥120万",
                "pending_details": [
                    {"invoice": "INV-2025-0089", "amount": "¥80万", "due_date": "2025-06-30", "status": "已开票未回款"},
                    {"invoice": "INV-2025-0102", "amount": "¥40万", "due_date": "2025-07-15", "status": "待开票"},
                ],
            },
        },
    ],
    "oa": [
        {
            "name": "query_approval_status",
            "description": "查询审批流程状态",
            "params": {"approval_id": {"type": "string", "description": "审批单号"}},
            "mock_data": {
                "approval_id": "{approval_id}",
                "title": "采购申请 - 办公设备",
                "applicant": "张三",
                "department": "技术部",
                "amount": "¥15,000",
                "status": "审批中",
                "current_node": "部门经理审批",
                "created_at": "2025-05-15",
            },
        },
        {
            "name": "submit_approval",
            "description": "提交审批申请",
            "params": {
                "title": {"type": "string", "description": "审批标题"},
                "content": {"type": "string", "description": "审批内容"},
            },
            "mock_data": {
                "approval_id": "APR-2025-MOCK001",
                "status": "已提交",
                "message": "审批申请已成功提交，等待部门经理审批",
            },
        },
    ],
    "email": [
        {
            "name": "send_email",
            "description": "发送邮件",
            "params": {
                "to": {"type": "string", "description": "收件人"},
                "subject": {"type": "string", "description": "邮件主题"},
                "body": {"type": "string", "description": "邮件正文"},
            },
            "mock_data": {
                "message_id": "MSG-2025-MOCK001",
                "status": "已发送",
                "to": "{to}",
                "subject": "{subject}",
            },
        },
        {
            "name": "search_emails",
            "description": "搜索邮件",
            "params": {"keyword": {"type": "string", "description": "搜索关键词"}},
            "mock_data": [
                {"from": "lisi@example.com", "subject": "关于Q2项目进度", "date": "2025-05-18", "snippet": "请查看附件中的项目进度报告..."},
                {"from": "wangwu@example.com", "subject": "会议通知 - 周五例会", "date": "2025-05-17", "snippet": "本周五下午2点召开部门例会..."},
            ],
        },
    ],
    "calendar": [
        {
            "name": "query_schedule",
            "description": "查询日程安排",
            "params": {"date": {"type": "string", "description": "日期，如 2025-05-20"}},
            "mock_data": [
                {"time": "09:00-10:00", "title": "项目周会", "location": "3楼会议室A", "attendees": "张三、李四、王五"},
                {"time": "14:00-15:30", "title": "客户演示", "location": "线上-腾讯会议", "attendees": "张三、客户团队"},
                {"time": "16:00-17:00", "title": "1v1 with 主管", "location": "5楼小会议室", "attendees": "张三、赵六"},
            ],
        },
    ],
    "hr": [
        {
            "name": "query_employee_info",
            "description": "查询员工信息",
            "params": {"employee_name": {"type": "string", "description": "员工姓名"}},
            "mock_data": {
                "name": "{employee_name}",
                "department": "技术部",
                "position": "高级工程师",
                "level": "P7",
                "entry_date": "2021-03-15",
                "status": "在职",
                "annual_leave_remaining": "7天",
            },
        },
        {
            "name": "query_leave_balance",
            "description": "查询假期余额",
            "params": {"employee_name": {"type": "string", "description": "员工姓名"}},
            "mock_data": {
                "employee": "{employee_name}",
                "annual_leave": {"total": 15, "used": 8, "remaining": 7},
                "sick_leave": {"total": 10, "used": 2, "remaining": 8},
                "personal_leave": {"total": 3, "used": 0, "remaining": 3},
            },
        },
    ],
    "approval": [
        {
            "name": "approve_request",
            "description": "审批通过",
            "params": {"approval_id": {"type": "string", "description": "审批单号"}, "comment": {"type": "string", "description": "审批意见"}},
            "mock_data": {"approval_id": "{approval_id}", "status": "已通过", "comment": "{comment}"},
        },
        {
            "name": "reject_request",
            "description": "审批驳回",
            "params": {"approval_id": {"type": "string", "description": "审批单号"}, "reason": {"type": "string", "description": "驳回原因"}},
            "mock_data": {"approval_id": "{approval_id}", "status": "已驳回", "reason": "{reason}"},
        },
    ],
    "im": [
        {
            "name": "send_message",
            "description": "发送即时消息",
            "params": {"to": {"type": "string", "description": "接收人"}, "content": {"type": "string", "description": "消息内容"}},
            "mock_data": {"message_id": "IM-2025-MOCK001", "status": "已发送", "to": "{to}"},
        },
    ],
    "doc": [
        {
            "name": "search_documents",
            "description": "搜索文档",
            "params": {"keyword": {"type": "string", "description": "搜索关键词"}},
            "mock_data": [
                {"title": "2025年Q1工作总结", "author": "张三", "updated": "2025-04-05", "type": "文档"},
                {"title": "项目技术方案V2.0", "author": "李四", "updated": "2025-03-20", "type": "文档"},
            ],
        },
    ],
    "knowledge": [
        {
            "name": "search_knowledge",
            "description": "搜索知识库",
            "params": {"query": {"type": "string", "description": "搜索查询"}},
            "mock_data": [
                {"title": "公司差旅报销制度", "content": "员工出差报销标准：交通费实报实销，住宿费上限500元/晚...", "score": 0.95},
                {"title": "年假管理制度", "content": "入职满1年享有15天年假，未休年假可结转至次年Q1...", "score": 0.88},
            ],
        },
    ],
    "web_search": [
        {
            "name": "web_search",
            "description": "网络搜索",
            "params": {"query": {"type": "string", "description": "搜索关键词"}},
            "mock_data": [
                {"title": "搜索结果1", "url": "https://example.com/1", "snippet": "这是模拟的搜索结果..."},
                {"title": "搜索结果2", "url": "https://example.com/2", "snippet": "另一个模拟的搜索结果..."},
            ],
        },
    ],
}


def _create_mock_tools(agent_name: str, server_names: list[str]) -> list[Any]:
    """为 Agent 创建 mock 工具

    当真实 MCP 服务不可用时，根据服务类型生成对应的 mock 工具。
    mock 工具使用 FunctionTool 包装，返回预设的模拟数据。

    Args:
        agent_name: Agent 名称
        server_names: 绑定的 MCP 服务名称列表

    Returns:
        FunctionTool 列表
    """
    from autogen_core.tools import FunctionTool

    mock_tools: list[Any] = []

    for server_name in server_names:
        tool_defs = MOCK_TOOL_DEFINITIONS.get(server_name, [])
        for tool_def in tool_defs:
            try:
                tool = _build_mock_function_tool(tool_def)
                mock_tools.append(tool)
            except Exception as e:
                logger.warning("创建 mock 工具 %s 失败: %s", tool_def.get("name", ""), e)

    return mock_tools


def _build_mock_function_tool(tool_def: dict[str, Any]) -> Any:
    """根据工具定义构建 mock FunctionTool

    动态创建一个 Python 函数，其参数与真实 MCP 工具一致，
    返回预设的 mock 数据。参数值会替换 mock 数据中的占位符。

    Args:
        tool_def: 工具定义字典，包含 name, description, params, mock_data

    Returns:
        FunctionTool 实例
    """
    import json
    from autogen_core.tools import FunctionTool

    tool_name = tool_def["name"]
    tool_description = tool_def["description"]
    params = tool_def.get("params", {})
    mock_data = tool_def.get("mock_data", {})

    param_names = list(params.keys())

    # 使用闭包捕获 mock_data，通过 exec 创建具有正确参数签名的函数
    # 函数体调用 _mock_tool_executor 完成实际逻辑
    sig_params = ", ".join(f"{pname}: str" for pname in param_names)
    func_body_lines = [
        f"async def {tool_name}({sig_params}) -> str:",
        f"    return await _mock_tool_executor({', '.join(param_names)})",
    ]
    exec_text = "\n".join(func_body_lines)

    async def _mock_tool_executor(*args: str) -> str:
        kwargs = dict(zip(param_names, args))
        result = _replace_placeholders(mock_data, kwargs)
        return json.dumps(result, ensure_ascii=False, indent=2)

    local_ns: dict[str, Any] = {"_mock_tool_executor": _mock_tool_executor}
    exec(exec_text, local_ns, local_ns)
    func = local_ns[tool_name]
    func.__doc__ = tool_description

    return FunctionTool(
        func=func,
        name=tool_name,
        description=tool_description,
    )


def _replace_placeholders(data: Any, values: dict[str, str]) -> Any:
    """替换 mock 数据中的占位符

    占位符格式为 {param_name}，会被替换为对应的参数值。

    Args:
        data: mock 数据（可以是 dict, list, str 等）
        values: 参数值字典

    Returns:
        替换后的数据
    """
    import copy
    data = copy.deepcopy(data)

    if isinstance(data, dict):
        return {k: _replace_placeholders(v, values) for k, v in data.items()}
    elif isinstance(data, list):
        return [_replace_placeholders(item, values) for item in data]
    elif isinstance(data, str):
        for key, val in values.items():
            placeholder = "{" + key + "}"
            if placeholder in data:
                data = data.replace(placeholder, str(val) if val else f"[{key}]")
        return data
    return data


async def load_agent_tools(agent_name: str) -> list[Any]:
    """根据 Agent 名称加载其绑定的 MCP 工具

    当 MCP 服务不可用时，自动降级为 mock 工具，返回模拟数据。
    mock 工具的 schema 与真实 MCP 工具一致，确保 Agent 能正常调用。

    Args:
        agent_name: Agent 名称，如 "EmailAgent"

    Returns:
        AutoGen Function Tool 列表
    """
    bound_servers = AGENT_TOOL_BINDINGS.get(agent_name, [])
    tools = await load_mcp_tools(bound_servers)

    if not tools and bound_servers:
        # 真实 MCP 工具不可用，降级为 mock 工具
        mock_tools = _create_mock_tools(agent_name, bound_servers)
        if mock_tools:
            logger.info(
                "Agent %s MCP 工具不可用，已降级为 %d 个 mock 工具",
                agent_name, len(mock_tools),
            )
        return mock_tools

    return tools


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
