"""知识库 MCP 服务

提供知识搜索、知识查询、知识推荐等工具，
通过 MCP 协议供 Agent 调用，底层对接企业知识库系统（Milvus + PostgreSQL）。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("knowledge-mcp-server", host="0.0.0.0", port=9010)

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("KNOWLEDGE")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def search_knowledge(
    query: str,
    category: str = "",
    top_k: int = 5,
    threshold: float = 0.7,
) -> str:
    """语义搜索知识库

    使用自然语言查询在知识库中进行语义搜索，返回最相关的知识条目。

    Args:
        query: 查询文本
        category: 知识分类，product(产品)/process(流程)/faq(常见问题)/policy(政策)/technical(技术)，为空则搜全部
        top_k: 返回结果数量，默认5
        threshold: 相似度阈值，0-1之间，默认0.7

    Returns:
        搜索结果 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "threshold": threshold,
    }
    if category:
        params["category"] = category

    result = await client.get("/knowledge/search", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "搜索知识库失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_knowledge_detail(knowledge_id: str) -> str:
    """获取知识条目详情

    根据知识ID获取完整的知识条目内容。

    Args:
        knowledge_id: 知识条目ID

    Returns:
        知识详情 JSON 字符串
    """
    client = _get_api_client()
    result = await client.get(f"/knowledge/{knowledge_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取知识详情失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_faq(
    question: str,
    top_k: int = 3,
) -> str:
    """查询常见问题

    在FAQ知识库中查找与问题最匹配的答案。

    Args:
        question: 用户问题
        top_k: 返回结果数量，默认3

    Returns:
        FAQ 匹配结果 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"question": question, "top_k": top_k}

    result = await client.get("/knowledge/faq", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询常见问题失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def search_by_keywords(
    keywords: str,
    category: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """关键词搜索知识库

    使用关键词在知识库中进行精确搜索，适合查找特定术语或标题。

    Args:
        keywords: 搜索关键词，多个用空格分隔
        category: 知识分类筛选，为空则搜全部
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        搜索结果 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"keywords": keywords, "page": page, "page_size": page_size}
    if category:
        params["category"] = category

    result = await client.get("/knowledge/keywords", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "关键词搜索失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_related_knowledge(
    knowledge_id: str,
    top_k: int = 5,
) -> str:
    """获取相关知识推荐

    根据指定知识条目，推荐相关的其他知识。

    Args:
        knowledge_id: 知识条目ID
        top_k: 返回结果数量，默认5

    Returns:
        相关知识列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"top_k": top_k}

    result = await client.get(f"/knowledge/{knowledge_id}/related", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取相关知识失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
