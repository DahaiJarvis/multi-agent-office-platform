"""管理路由"""

import logging
from datetime import datetime

from fastapi import APIRouter

from api.models.response import HealthResponse
from agent.core.config import get_settings
from agent.core.mcp_integration import MCP_SERVER_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查接口"""
    settings = get_settings()

    components = {
        "api": "healthy",
        "environment": settings.environment,
    }

    # 检查 MCP 服务注册状态
    mcp_status = {}
    for name, config in MCP_SERVER_REGISTRY.items():
        mcp_status[name] = "registered" if config.enabled else "disabled"
    components["mcp_servers"] = str(len([s for s in mcp_status.values() if s == "registered"]))

    return HealthResponse(
        status="healthy",
        version="0.1.0",
        timestamp=datetime.now(),
        components=components,
    )


@router.get("/mcp/status")
async def mcp_status() -> dict:
    """查看 MCP 服务状态"""
    servers = {}
    for name, config in MCP_SERVER_REGISTRY.items():
        servers[name] = {
            "name": config.name,
            "description": config.description,
            "transport": config.transport,
            "url": config.url,
            "enabled": config.enabled,
        }
    return {"servers": servers, "total": len(servers)}
