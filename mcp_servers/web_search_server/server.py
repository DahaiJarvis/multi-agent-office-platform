"""网络搜索 MCP 服务

提供联网搜索工具，通过 DashScope API 的 enable_search 能力
实现实时网络信息检索，供 Agent 调用获取天气、新闻、股价等实时数据。
"""

import json
import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.base import format_result, is_mock_mode

logger = logging.getLogger(__name__)

mcp = FastMCP("web-search-mcp-server", host="0.0.0.0", port=9011)

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
WEB_SEARCH_MODEL = os.getenv("WEB_SEARCH_MODEL", "qwen-turbo")


async def _call_dashscope_with_search(query: str) -> dict[str, Any]:
    """调用 DashScope API 并启用联网搜索

    使用 DashScope 的 enable_search 参数，让模型在推理时
    自动搜索互联网获取实时信息，并基于搜索结果生成回答。

    Args:
        query: 搜索查询文本

    Returns:
        API 响应字典
    """
    url = f"{DASHSCOPE_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": WEB_SEARCH_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个网络搜索助手。请基于搜索结果，"
                    "用简洁清晰的语言回答用户的问题。"
                    "如果搜索结果包含具体数据（如温度、价格、时间等），"
                    "请准确引用。如果搜索结果不足，请如实说明。"
                ),
            },
            {"role": "user", "content": query},
        ],
        "enable_search": True,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            logger.error("DashScope API 调用失败: status=%d body=%s", response.status_code, response.text[:200])
            return {
                "success": False,
                "error": f"搜索服务调用失败: HTTP {response.status_code}",
            }

        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")

        search_info = []
        if hasattr(choice, "get"):
            search_results = choice.get("search_results", [])
            for sr in search_results:
                search_info.append({
                    "title": sr.get("title", ""),
                    "url": sr.get("url", ""),
                })

        return {
            "success": True,
            "content": content,
            "search_info": search_info,
            "model": data.get("model", WEB_SEARCH_MODEL),
            "usage": data.get("usage", {}),
        }


@mcp.tool()
async def internet_search(query: str) -> str:
    """搜索互联网获取实时信息

    使用网络搜索引擎查询实时信息，适用于以下场景：
    - 天气查询（如"北京明天天气"）
    - 新闻资讯（如"今日科技新闻"）
    - 实时数据（如股价、汇率、航班信息）
    - 最新动态（如"XX公司最新消息"）

    Args:
        query: 搜索查询文本，如"北京明天天气如何"

    Returns:
        搜索结果 JSON 字符串，包含搜索内容和来源信息
    """
    if is_mock_mode():
        return json.dumps(
            {
                "success": True,
                "content": f"根据搜索结果，关于「{query}」的信息如下：\n\n这是一个模拟的搜索结果。在 Mock 模式下，网络搜索将返回此占位内容。\n\n实际部署时，此工具会通过 DashScope API 进行真实的互联网搜索。",
                "sources": [{"title": "模拟搜索结果", "url": "https://example.com/mock"}],
            },
            ensure_ascii=False,
        )

    if not DASHSCOPE_API_KEY:
        return json.dumps(
            {"success": False, "error": "DashScope API Key 未配置，无法进行网络搜索"},
            ensure_ascii=False,
        )

    try:
        result = await _call_dashscope_with_search(query)
        if not result.get("success"):
            return json.dumps(result, ensure_ascii=False)

        return json.dumps(
            {
                "success": True,
                "content": result["content"],
                "sources": result.get("search_info", []),
            },
            ensure_ascii=False,
        )
    except httpx.TimeoutException:
        logger.error("网络搜索超时: query=%s", query[:50])
        return json.dumps(
            {"success": False, "error": "网络搜索超时，请稍后重试"},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error("网络搜索异常: query=%s error=%s", query[:50], e)
        return json.dumps(
            {"success": False, "error": f"网络搜索失败: {str(e)}"},
            ensure_ascii=False,
        )


@mcp.tool()
async def search_news(keyword: str, count: int = 5) -> str:
    """搜索最新新闻资讯

    专门用于搜索新闻类信息，结果侧重于时效性和新闻来源。

    Args:
        keyword: 新闻关键词，如"人工智能"
        count: 返回结果数量，默认5

    Returns:
        新闻搜索结果 JSON 字符串
    """
    if is_mock_mode():
        return json.dumps(
            {
                "success": True,
                "content": f"关于「{keyword}」的最新新闻：\n\n1. 模拟新闻标题一：这是一个模拟的新闻搜索结果。\n2. 模拟新闻标题二：在 Mock 模式下，新闻搜索将返回此占位内容。\n\n实际部署时，此工具会通过 DashScope API 进行真实的新闻搜索。",
                "sources": [{"title": "模拟新闻来源", "url": "https://example.com/mock-news"}],
            },
            ensure_ascii=False,
        )

    news_query = f"最新新闻 {keyword}"
    if not DASHSCOPE_API_KEY:
        return json.dumps(
            {"success": False, "error": "DashScope API Key 未配置，无法搜索新闻"},
            ensure_ascii=False,
        )

    try:
        result = await _call_dashscope_with_search(news_query)
        if not result.get("success"):
            return json.dumps(result, ensure_ascii=False)

        return json.dumps(
            {
                "success": True,
                "content": result["content"],
                "sources": result.get("search_info", []),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error("新闻搜索异常: keyword=%s error=%s", keyword, e)
        return json.dumps(
            {"success": False, "error": f"新闻搜索失败: {str(e)}"},
            ensure_ascii=False,
        )


if __name__ == "__main__":
    mcp.run(transport="sse")
