"""MCP Registry 持久化单元测试

验证 MCP 注册中心的注册、注销、心跳和 Redis 持久化功能。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from mcp_servers.registry import ServiceInfo, RegisterRequest, HeartbeatRequest


class TestServiceInfo:
    """ServiceInfo 模型测试"""

    def test_create_service_info(self):
        """测试创建服务信息"""
        service = ServiceInfo(
            name="test-service",
            description="测试服务",
            url="http://localhost:9001/sse",
            transport="sse",
            tools=["tool1", "tool2"],
        )
        assert service.name == "test-service"
        assert service.url == "http://localhost:9001/sse"
        assert len(service.tools) == 2
        assert service.status == "healthy"
        assert service.registered_at is not None

    def test_service_info_serialization(self):
        """测试服务信息序列化"""
        service = ServiceInfo(
            name="test-service",
            url="http://localhost:9001/sse",
        )
        data = service.model_dump(mode="json")
        assert data["name"] == "test-service"
        assert data["url"] == "http://localhost:9001/sse"
        assert "registered_at" in data


class TestRegisterRequest:
    """RegisterRequest 模型测试"""

    def test_create_register_request(self):
        """测试创建注册请求"""
        req = RegisterRequest(
            name="test-service",
            url="http://localhost:9001/sse",
        )
        assert req.name == "test-service"
        assert req.transport == "sse"
        assert req.tools == []

    def test_register_request_with_tools(self):
        """测试带工具的注册请求"""
        req = RegisterRequest(
            name="test-service",
            url="http://localhost:9001/sse",
            tools=["search", "create"],
        )
        assert len(req.tools) == 2


class TestRegistryInMemory:
    """内存存储模式测试（不依赖 Redis）"""

    @pytest.fixture
    def registry_state(self):
        """清空注册中心状态"""
        import mcp_servers.registry as reg_module
        reg_module._registry.clear()
        yield reg_module._registry
        reg_module._registry.clear()

    @pytest.mark.asyncio
    async def test_register_service(self, registry_state):
        """测试服务注册"""
        from mcp_servers.registry import register_service

        req = RegisterRequest(
            name="test-service",
            description="测试服务",
            url="http://localhost:9001/sse",
            tools=["tool1"],
        )

        with patch("mcp_servers.registry._save_to_redis", new_callable=AsyncMock):
            result = await register_service(req)

        assert result["success"] is True
        assert "test-service" in registry_state
        assert registry_state["test-service"].url == "http://localhost:9001/sse"

    @pytest.mark.asyncio
    async def test_register_duplicate_service(self, registry_state):
        """测试重复注册服务（更新）"""
        from mcp_servers.registry import register_service

        req1 = RegisterRequest(name="test-service", url="http://localhost:9001/sse")
        req2 = RegisterRequest(name="test-service", url="http://localhost:9002/sse")

        with patch("mcp_servers.registry._save_to_redis", new_callable=AsyncMock):
            await register_service(req1)
            await register_service(req2)

        assert registry_state["test-service"].url == "http://localhost:9002/sse"

    @pytest.mark.asyncio
    async def test_deregister_service(self, registry_state):
        """测试服务注销"""
        from mcp_servers.registry import register_service, deregister_service

        req = RegisterRequest(name="test-service", url="http://localhost:9001/sse")

        with patch("mcp_servers.registry._save_to_redis", new_callable=AsyncMock):
            await register_service(req)
        with patch("mcp_servers.registry._remove_from_redis", new_callable=AsyncMock):
            result = await deregister_service("test-service")

        assert result["success"] is True
        assert "test-service" not in registry_state

    @pytest.mark.asyncio
    async def test_deregister_nonexistent_service(self, registry_state):
        """测试注销不存在的服务"""
        from mcp_servers.registry import deregister_service
        from fastapi import HTTPException

        with patch("mcp_servers.registry._remove_from_redis", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await deregister_service("nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_heartbeat(self, registry_state):
        """测试服务心跳"""
        from mcp_servers.registry import register_service, heartbeat

        req = RegisterRequest(name="test-service", url="http://localhost:9001/sse")

        with patch("mcp_servers.registry._save_to_redis", new_callable=AsyncMock):
            await register_service(req)

        old_heartbeat = registry_state["test-service"].last_heartbeat

        with patch("mcp_servers.registry._update_heartbeat_redis", new_callable=AsyncMock):
            result = await heartbeat(HeartbeatRequest(name="test-service"))

        assert result["success"] is True
        assert registry_state["test-service"].last_heartbeat >= old_heartbeat

    @pytest.mark.asyncio
    async def test_heartbeat_nonexistent_service(self, registry_state):
        """测试不存在服务的心跳"""
        from mcp_servers.registry import heartbeat
        from fastapi import HTTPException

        with patch("mcp_servers.registry._update_heartbeat_redis", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await heartbeat(HeartbeatRequest(name="nonexistent"))
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_services(self, registry_state):
        """测试列出所有服务"""
        from mcp_servers.registry import register_service, list_services

        with patch("mcp_servers.registry._save_to_redis", new_callable=AsyncMock):
            await register_service(RegisterRequest(name="service-a", url="http://a:9001/sse"))
            await register_service(RegisterRequest(name="service-b", url="http://b:9002/sse"))

        result = await list_services()
        assert result["success"] is True
        assert result["total"] == 2
        services = result["data"]
        names = [s["name"] for s in services]
        assert "service-a" in names
        assert "service-b" in names

    @pytest.mark.asyncio
    async def test_get_service(self, registry_state):
        """测试获取单个服务"""
        from mcp_servers.registry import register_service, get_service

        with patch("mcp_servers.registry._save_to_redis", new_callable=AsyncMock):
            await register_service(RegisterRequest(
                name="test-service",
                url="http://localhost:9001/sse",
                tools=["tool1", "tool2"],
            ))

        result = await get_service("test-service")
        assert result["success"] is True
        service_data = result["data"]
        assert service_data["name"] == "test-service"
        assert len(service_data["tools"]) == 2
