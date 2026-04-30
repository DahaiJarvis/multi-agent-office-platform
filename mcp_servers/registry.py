"""MCP 注册中心

提供服务注册、发现、健康检查功能。
各 MCP Server 启动时向注册中心注册自身信息，
Agent 编排层通过注册中心发现可用的 MCP 服务。

持久化策略：
  - 主存储：Redis，支持跨实例共享和重启恢复
  - 降级存储：进程内字典，Redis 不可用时自动降级
  - 启动时从 Redis 加载已有注册信息，保证重启不丢失
"""

import json
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

REDIS_KEY_PREFIX = "mcp_registry:service:"
REDIS_KEY_SET = "mcp_registry:services"

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


# 进程内存储（Redis 不可用时的降级方案）
_registry: dict[str, ServiceInfo] = {}

# Redis 客户端
_redis_client: Any = None
_use_redis: bool = True
_redis_retry_after: float = 0.0
_redis_retry_backoff: float = 5.0
_redis_max_backoff: float = 300.0


async def _get_redis() -> Any:
    """获取 Redis 客户端

    当 Redis 连接失败时，不会永久降级，而是采用指数退避重试。
    在退避期间使用内存存储，退避结束后自动尝试重新连接。
    """
    global _redis_client, _use_redis, _redis_retry_after, _redis_retry_backoff

    import time
    now = time.time()

    # 如果在退避期内，跳过重试
    if _redis_client is None and now < _redis_retry_after:
        return None

    if _redis_client is not None:
        return _redis_client

    try:
        import redis.asyncio as aioredis

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", "")
        redis_db = int(os.getenv("REDIS_DB", "0"))

        auth = f":{redis_password}@" if redis_password else ""
        redis_url = f"redis://{auth}{redis_host}:{redis_port}/{redis_db}"

        _redis_client = aioredis.from_url(redis_url, decode_responses=True)
        await _redis_client.ping()
        logger.info("MCP 注册中心 Redis 连接成功")
        # 连接成功，重置退避
        _redis_retry_backoff = 5.0
        _use_redis = True
        return _redis_client
    except Exception as e:
        # 连接失败，设置退避时间，但不永久降级
        _redis_client = None
        _redis_retry_after = now + _redis_retry_backoff
        logger.warning(
            "MCP 注册中心 Redis 连接失败，降级到内存存储，%.0fs 后重试: %s",
            _redis_retry_backoff, e,
        )
        _redis_retry_backoff = min(_redis_retry_backoff * 2, _redis_max_backoff)
        return None


async def _save_to_redis(service: ServiceInfo) -> None:
    """将服务信息持久化到 Redis"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        data = service.model_dump(mode="json")
        key = f"{REDIS_KEY_PREFIX}{service.name}"
        await redis.set(key, json.dumps(data, ensure_ascii=False, default=str))
        await redis.sadd(REDIS_KEY_SET, service.name)
        logger.debug("服务 %s 已持久化到 Redis", service.name)
    except Exception as e:
        logger.error("Redis 持久化失败: %s", e)


async def _remove_from_redis(name: str) -> None:
    """从 Redis 删除服务信息"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        key = f"{REDIS_KEY_PREFIX}{name}"
        await redis.delete(key)
        await redis.srem(REDIS_KEY_SET, name)
        logger.debug("服务 %s 已从 Redis 删除", name)
    except Exception as e:
        logger.error("Redis 删除失败: %s", e)


async def _update_heartbeat_redis(name: str, service: ServiceInfo) -> None:
    """更新 Redis 中的心跳时间"""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        data = service.model_dump(mode="json")
        key = f"{REDIS_KEY_PREFIX}{name}"
        await redis.set(key, json.dumps(data, ensure_ascii=False, default=str))
    except Exception as e:
        logger.error("Redis 心跳更新失败: %s", e)


async def _load_from_redis() -> dict[str, ServiceInfo]:
    """从 Redis 加载所有已注册的服务信息"""
    redis = await _get_redis()
    if redis is None:
        return {}
    try:
        names = await redis.smembers(REDIS_KEY_SET)
        services: dict[str, ServiceInfo] = {}
        for name in names:
            key = f"{REDIS_KEY_PREFIX}{name}"
            data_str = await redis.get(key)
            if data_str:
                data = json.loads(data_str)
                services[name] = ServiceInfo.model_validate(data)
        return services
    except Exception as e:
        logger.error("Redis 加载失败: %s", e)
        return {}


@app.on_event("startup")
async def _startup_load_from_redis() -> None:
    """启动时从 Redis 加载已有注册信息"""
    global _registry
    loaded = await _load_from_redis()
    if loaded:
        _registry.update(loaded)
        logger.info("从 Redis 加载了 %d 个已注册服务", len(loaded))


@app.post("/register")
async def register_service(req: RegisterRequest) -> dict[str, Any]:
    """注册 MCP 服务

    MCP Server 启动时调用此接口注册自身信息。
    同时写入内存和 Redis，保证持久化。
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
    await _save_to_redis(service)
    logger.info("服务注册成功: %s (%s)", req.name, req.url)

    return {"success": True, "message": f"服务 {req.name} 注册成功"}


@app.post("/deregister")
async def deregister_service(name: str) -> dict[str, Any]:
    """注销 MCP 服务

    MCP Server 停止时调用此接口注销自身。
    同时从内存和 Redis 删除。
    """
    if name not in _registry:
        raise HTTPException(status_code=404, detail=f"服务 {name} 未注册")

    del _registry[name]
    await _remove_from_redis(name)
    logger.info("服务已注销: %s", name)
    return {"success": True, "message": f"服务 {name} 已注销"}


@app.post("/heartbeat")
async def heartbeat(req: HeartbeatRequest) -> dict[str, Any]:
    """服务心跳

    MCP Server 定期发送心跳，注册中心据此判断服务健康状态。
    同步更新内存和 Redis 中的心跳时间。
    """
    if req.name not in _registry:
        raise HTTPException(status_code=404, detail=f"服务 {req.name} 未注册")

    _registry[req.name].last_heartbeat = datetime.now()
    _registry[req.name].status = "healthy"
    await _update_heartbeat_redis(req.name, _registry[req.name])
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
