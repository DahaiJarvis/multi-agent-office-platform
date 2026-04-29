"""FastAPI 应用入口"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.core.config import get_settings
from agent.core.session_manager import get_session_manager
from agent.core.mcp_integration import close_all_connections
from api.middleware.auth import AuthMiddleware
from api.middleware.rate_limit import DistributedRateLimitMiddleware
from api.middleware.tracing import TracingMiddleware
from api.routes import agent_routes, session_routes, admin_routes, auth_routes, tenant_routes, compliance_routes, agent_builder_routes, embed_routes, multimodal_routes, search_routes, analytics_routes, prompt_template_routes, workflow_routes, plugin_routes, sla_routes, region_routes, knowledge_proxy_routes, jwks_routes, approval_routes
from api.errors import AppException, app_exception_handler, generic_exception_handler
from observability.logging_config import setup_logging
from observability.tracing import setup_tracing

logger = logging.getLogger(__name__)
settings = get_settings()

API_VERSION = settings.api_version


async def check_ida_compatibility() -> bool:
    """启动时检查智能文档助手兼容性

    消费 IDA 的 /api/monitoring/compatibility 端点，
    确认版本和工具兼容性。不可达时记录警告，不阻止启动。
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.ida_backend_url}/api/monitoring/compatibility"
            )
            if response.status_code == 200:
                data = response.json()
                logger.info(
                    "IDA 兼容性检查通过: service=%s, api_versions=%s, mcp_tools=%d",
                    data.get("service"),
                    data.get("api_versions"),
                    len(data.get("mcp_tools", [])),
                )

                breaking_changes = data.get("breaking_changes", [])
                if breaking_changes:
                    logger.warning("IDA 存在破坏性变更: %s", breaking_changes)

                return True
            else:
                logger.warning("IDA 兼容性检查失败: status=%d", response.status_code)
                return False
    except Exception as e:
        logger.warning("IDA 兼容性检查跳过（服务不可达）: %s", str(e))
        return False

# 审计日志后台刷新任务
_audit_flush_task: asyncio.Task | None = None


async def _audit_flush_loop() -> None:
    """审计日志缓冲区定时刷新（每 30 秒）"""
    while True:
        try:
            await asyncio.sleep(30)
            from agent.core.audit import get_audit_logger
            audit = get_audit_logger()
            await audit.flush_buffer()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("审计日志定时刷新失败: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    setup_logging(log_level=settings.log_level)
    logger.info("应用启动中...")

    setup_tracing(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
    )

    session_mgr = await get_session_manager()
    logger.info("会话管理器初始化完成")

    # 初始化连接池管理器
    try:
        from agent.core.performance.connection_pool import get_pool_manager
        pool_mgr = get_pool_manager()
        await pool_mgr.initialize()
        logger.info("连接池管理器初始化完成")
    except Exception as e:
        logger.warning("连接池管理器初始化失败（非致命）: %s", e)

    # 初始化 SSO 提供者
    if settings.sso_enabled:
        try:
            from security.sso import init_sso_providers_from_config
            init_sso_providers_from_config(settings.sso_provider_configs)
            logger.info("SSO 提供者初始化完成: providers=%s", list(settings.sso_provider_configs.keys()))
        except Exception as e:
            logger.warning("SSO 提供者初始化失败（非致命）: %s", e)

    # 初始化静态数据加密
    if settings.encryption_enabled:
        try:
            from security.encryption import init_encryption
            init_encryption(
                key_provider_type=settings.encryption_key_provider,
                key_file_path=settings.encryption_key_file,
            )
            logger.info("静态数据加密初始化完成")
        except Exception as e:
            logger.warning("静态数据加密初始化失败（非致命）: %s", e)

    # 初始化数据驻留控制
    try:
        from security.data_residency import get_data_residency_manager, DataRegion
        residency_mgr = get_data_residency_manager()
        if settings.data_residency_region in [e.value for e in DataRegion]:
            residency_mgr.set_current_region(DataRegion(settings.data_residency_region))
        logger.info("数据驻留控制初始化完成: region=%s enforced=%s", settings.data_residency_region, settings.data_residency_enforced)
    except Exception as e:
        logger.warning("数据驻留控制初始化失败（非致命）: %s", e)

    # 初始化多租户管理器
    if settings.multi_tenant_enabled:
        try:
            from security.tenant import get_tenant_manager
            get_tenant_manager()
            logger.info("多租户管理器初始化完成: isolation=%s region=%s", settings.tenant_default_isolation, settings.tenant_default_region)
        except Exception as e:
            logger.warning("多租户管理器初始化失败（非致命）: %s", e)

    # 注册已发布的自定义 Agent
    try:
        from agent.agents.agent_builder import register_all_published_agents
        register_all_published_agents()
        logger.info("自定义 Agent 运行时注册完成")
    except Exception as e:
        logger.warning("自定义 Agent 注册失败（非致命）: %s", e)

    # 智能文档助手兼容性检查
    try:
        await check_ida_compatibility()
    except Exception as e:
        logger.warning("智能文档助手兼容性检查失败（非致命）: %s", e)

    # 初始化用户凭证存储（数据库持久化）
    try:
        from security.user_store import get_user_store
        user_store = get_user_store()
        await user_store.initialize()
        logger.info("用户凭证存储初始化完成")
    except Exception as e:
        logger.warning("用户凭证存储初始化失败（非致命）: %s", e)

    logger.info(
        "应用启动完成: host=%s port=%d env=%s api_version=%s",
        settings.api_host,
        settings.api_port,
        settings.environment,
        API_VERSION,
    )

    # 启动审计日志后台刷新任务
    global _audit_flush_task
    _audit_flush_task = asyncio.create_task(_audit_flush_loop())

    yield

    # 停止审计日志刷新任务
    if _audit_flush_task:
        _audit_flush_task.cancel()
        try:
            await _audit_flush_task
        except asyncio.CancelledError:
            pass

    # 关闭前最后一次刷新审计日志
    try:
        from agent.core.audit import get_audit_logger
        audit = get_audit_logger()
        await audit.flush_buffer()
    except Exception:
        pass

    await session_mgr.close()
    await close_all_connections()

    # 关闭知识库代理 HTTP 客户端，释放连接池资源
    try:
        from api.routes.knowledge_proxy_routes import close_knowledge_proxy_client
        await close_knowledge_proxy_client()
    except Exception:
        pass

    # 关闭连接池管理器
    try:
        from agent.core.performance.connection_pool import get_pool_manager
        pool_mgr = get_pool_manager()
        await pool_mgr.shutdown()
    except Exception:
        pass

    logger.info("应用已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="企业级多Agent办公平台",
        description="基于 AutoGen + MCP 的企业智能办公平台 API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS: 开发环境允许所有来源，生产环境使用配置白名单
    cors_origins = settings.cors_origins_list
    allow_credentials = True
    if cors_origins == ["*"]:
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 自定义中间件（按执行顺序添加，后添加的先执行）
    app.add_middleware(TracingMiddleware)
    app.add_middleware(DistributedRateLimitMiddleware)
    app.add_middleware(AuthMiddleware)

    # 注册全局异常处理器
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # 注册路由 - 使用配置化的 API 版本前缀
    api_prefix = f"/api/{API_VERSION}"
    app.include_router(auth_routes.router, prefix=api_prefix)
    app.include_router(agent_routes.router, prefix=api_prefix)
    app.include_router(session_routes.router, prefix=api_prefix)
    app.include_router(admin_routes.router, prefix=api_prefix)
    app.include_router(tenant_routes.router, prefix=api_prefix)
    app.include_router(compliance_routes.router, prefix=api_prefix)
    app.include_router(agent_builder_routes.router, prefix=api_prefix)
    app.include_router(embed_routes.router, prefix=api_prefix)
    app.include_router(multimodal_routes.router, prefix=api_prefix)
    app.include_router(search_routes.router, prefix=api_prefix)
    app.include_router(analytics_routes.router, prefix=api_prefix)
    app.include_router(prompt_template_routes.router, prefix=api_prefix)
    app.include_router(workflow_routes.router, prefix=api_prefix)
    app.include_router(plugin_routes.router, prefix=api_prefix)
    app.include_router(sla_routes.router, prefix=api_prefix)
    app.include_router(region_routes.router, prefix=api_prefix)
    app.include_router(knowledge_proxy_routes.router, prefix=api_prefix)
    app.include_router(jwks_routes.router)
    app.include_router(approval_routes.router, prefix=api_prefix)

    # Prometheus 指标端点
    from observability.metrics import metrics_endpoint
    app.add_route("/metrics", metrics_endpoint)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment == "development",
    )
