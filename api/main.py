"""FastAPI 应用入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.core.config import get_settings
from agent.core.session_manager import get_session_manager
from agent.core.mcp_integration import close_all_connections
from api.middleware.auth import AuthMiddleware
from api.middleware.rate_limit import RateLimitMiddleware
from api.middleware.tracing import TracingMiddleware
from api.routes import agent_routes, session_routes, admin_routes
from observability.logging_config import setup_logging
from observability.tracing import setup_tracing

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    setup_logging(log_level=settings.log_level)
    logger.info("应用启动中...")

    setup_tracing(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
    )

    # 初始化会话管理器，预热 Redis 连接
    session_mgr = await get_session_manager()
    logger.info("会话管理器初始化完成")

    logger.info(
        "应用启动完成: host=%s port=%d env=%s",
        settings.api_host,
        settings.api_port,
        settings.environment,
    )

    yield

    # 关闭连接
    await session_mgr.close()
    await close_all_connections()
    logger.info("应用已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="企业级多Agent办公平台",
        description="基于 AutoGen + MCP 的企业智能办公平台 API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 自定义中间件（按执行顺序添加，后添加的先执行）
    app.add_middleware(TracingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)

    # 注册路由
    app.include_router(agent_routes.router, prefix="/api/v1")
    app.include_router(session_routes.router, prefix="/api/v1")
    app.include_router(admin_routes.router, prefix="/api/v1")

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
