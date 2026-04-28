"""MCP 服务基础模块

提供通用的企业系统 API 客户端与 MCP 服务配置，
各 MCP Server 共享此模块实现与后端企业系统的 HTTP 通信。
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP 服务运行配置"""

    name: str
    description: str
    port: int
    host: str = "0.0.0.0"
    transport: str = "sse"


@dataclass
class EnterpriseAPIConfig:
    """企业系统 API 配置"""

    base_url: str
    token: str = ""
    timeout: float = 10.0


class EnterpriseAPIClient:
    """企业系统 API 客户端

    封装 httpx 异步 HTTP 调用，提供统一的请求/响应处理。
    """

    def __init__(self, config: EnterpriseAPIConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self._config.token:
                headers["Authorization"] = f"Bearer {self._config.token}"
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers=headers,
                timeout=self._config.timeout,
            )
        return self._client

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发送 GET 请求"""
        client = await self._get_client()
        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("API GET %s 失败: %s", path, e)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            logger.error("API GET %s 请求异常: %s", path, e)
            return {"success": False, "error": str(e)}

    async def post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """发送 POST 请求"""
        client = await self._get_client()
        try:
            response = await client.post(path, json=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("API POST %s 失败: %s", path, e)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            logger.error("API POST %s 请求异常: %s", path, e)
            return {"success": False, "error": str(e)}

    async def put(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """发送 PUT 请求"""
        client = await self._get_client()
        try:
            response = await client.put(path, json=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("API PUT %s 失败: %s", path, e)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except httpx.RequestError as e:
            logger.error("API PUT %s 请求异常: %s", path, e)
            return {"success": False, "error": str(e)}

    async def close(self) -> None:
        """关闭 HTTP 客户端连接"""
        if self._client:
            await self._client.aclose()
            self._client = None


def load_enterprise_config(prefix: str) -> EnterpriseAPIConfig:
    """从环境变量加载企业系统 API 配置

    环境变量命名规则: {PREFIX}_API_URL, {PREFIX}_API_TOKEN, {PREFIX}_API_TIMEOUT

    Args:
        prefix: 环境变量前缀，如 OA, EMAIL, CALENDAR, CRM

    Returns:
        EnterpriseAPIConfig 实例
    """
    base_url = os.getenv(f"{prefix}_API_URL", "http://localhost:3000/api")
    token = os.getenv(f"{prefix}_API_TOKEN", "")
    timeout = float(os.getenv(f"{prefix}_API_TIMEOUT", "10.0"))
    return EnterpriseAPIConfig(base_url=base_url, token=token, timeout=timeout)


def format_result(success: bool, data: Any = None, error: str = "") -> str:
    """格式化 MCP 工具返回结果为 JSON 字符串

    Args:
        success: 操作是否成功
        data: 返回数据
        error: 错误信息

    Returns:
        JSON 格式字符串
    """
    result: dict[str, Any] = {"success": success}
    if data is not None:
        result["data"] = data
    if error:
        result["error"] = error
    return json.dumps(result, ensure_ascii=False, default=str)
