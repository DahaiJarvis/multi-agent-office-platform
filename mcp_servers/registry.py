"""MCP 注册中心

提供服务注册、发现、健康检查功能。
各 MCP Server 启动时向注册中心注册自身信息，
Agent 编排层通过注册中心发现可用的 MCP 服务。
"""

import logging
import os
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REGISTRY_HOST = os.getenv("MCP_REGISTRY_HOST", "0.0.0.0")
REGISTRY_PORT = int(os.getenv("MCP_REGISTRY_PORT", "9099"))

app = FastAPI(title="MCP Registry", description="MCP 服务注册中心", version="1.0.0")


class ServiceInfo(BaseModel):
    """注册的服务信息"""

    name: str = Field(..., description="服务名称")
    description: str = Field(default="", description="服务描述")
    url: str = Field(..., description="服务地址，如 http://localhost:9001/sse")
    transport: str = Field(default="sse", description="传输协议: sse/stdio")
    tools: list[str] = Field(default_factory=list, description="提供的工具列表")
    registered_at: datetime = Field(default_factory=datetime.now, description="注册时间")
    last_heartbeat: datetime = Field(default_factory=datetime.now, description="最后心跳时间")
    status: str = Field(default="healthy", description="服务状态: healthy/unhealthy")


class RegisterRequest(BaseModel):
    """服务注册请求"""

    name: str
    description: str = ""
    url: str
    transport: str = "sse"
    tools: list[str] = Field(default_factory=list)


class HeartbeatRequest(BaseModel):
    """心跳请求"""

    name: str


# 内存存储，生产环境可替换为 Redis 或数据库
_registry: dict[str, ServiceInfo] = {}


@app.post("/register")
async def register_service(req: RegisterRequest) -> dict[str, Any]:
    """注册 MCP 服务

    MCP Server 启动时调用此接口注册自身信息。
    """
    if req.name in _registry:
        logger.info("服务 %s 已注册，更新信息", req.name)

    service = ServiceInfo(
        name=req.name,
        description=req.description,
        url=req.url,
        transport=req.transport,
        tools=req.tools,
    )
    _registry[req.name] = service
    logger.info("服务注册成功: %s (%s)", req.name, req.url)

    return {"success": True, "message": f"服务 {req.name} 注册成功"}


@app.post("/deregister")
async def deregister_service(name: str) -> dict[str, Any]:
    """注销 MCP 服务

    MCP Server 停止时调用此接口注销自身。
    """
    if name not in _registry:
        raise HTTPException(status_code=404, detail=f"服务 {name} 未注册")

    del _registry[name]
    logger.info("服务已注销: %s", name)
    return {"success": True, "message": f"服务 {name} 已注销"}


@app.post("/heartbeat")
async def heartbeat(req: HeartbeatRequest) -> dict[str, Any]:
    """服务心跳

    MCP Server 定期发送心跳，注册中心据此判断服务健康状态。
    """
    if req.name not in _registry:
        raise HTTPException(status_code=404, detail=f"服务 {req.name} 未注册")

    _registry[req.name].last_heartbeat = datetime.now()
    _registry[req.name].status = "healthy"
    return {"success": True}


@app.get("/services")
async def list_services() -> dict[str, Any]:
    """列出所有已注册的 MCP 服务"""
    services = []
    for svc in _registry.values():
        services.append(svc.model_dump(mode="json"))
    return {"success": True, "data": services, "total": len(services)}


@app.get("/services/{name}")
async def get_service(name: str) -> dict[str, Any]:
    """获取指定服务的详细信息"""
    if name not in _registry:
        raise HTTPException(status_code=404, detail=f"服务 {name} 未注册")

    return {"success": True, "data": _registry[name].model_dump(mode="json")}


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """注册中心自身健康检查"""
    return {
        "success": True,
        "status": "healthy",
        "registered_services": len(_registry),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/check/{name}")
async def check_service_health(name: str) -> dict[str, Any]:
    """主动检查指定 MCP 服务的健康状态

    通过向 MCP 服务的 SSE 端点发送请求来验证其是否可用。
    """
    if name not in _registry:
        raise HTTPException(status_code=404, detail=f"服务 {name} 未注册")

    service = _registry[name]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(service.url)
            is_healthy = response.status_code == 200
    except httpx.RequestError:
        is_healthy = False

    service.status = "healthy" if is_healthy else "unhealthy"
    service.last_heartbeat = datetime.now()

    return {
        "success": True,
        "data": {
            "name": name,
            "status": service.status,
            "url": service.url,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=REGISTRY_HOST, port=REGISTRY_PORT)
