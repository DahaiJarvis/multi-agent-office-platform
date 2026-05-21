"""CRM 系统 MCP 服务

提供客户查询、客户详情、商机跟进等工具，
通过 MCP 协议供 Agent 调用，底层对接企业 CRM 系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, is_mock_mode, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("crm-mcp-server", host="0.0.0.0", port=9004)

# Mock 数据
MOCK_DATA = {
    "customers": [
        {"id": "CUS-001", "name": "北京科技有限公司", "industry": "互联网", "level": "vip", "owner": "张三", "contact_count": 3},
        {"id": "CUS-002", "name": "上海贸易集团", "industry": "贸易", "level": "important", "owner": "李四", "contact_count": 2},
        {"id": "CUS-003", "name": "深圳创新科技", "industry": "科技", "level": "normal", "owner": "王五", "contact_count": 1},
        {"id": "CUS-004", "name": "广州智能制造", "industry": "制造业", "level": "potential", "owner": "张三", "contact_count": 1},
        {"id": "CUS-005", "name": "杭州数据科技", "industry": "互联网", "level": "vip", "owner": "李四", "contact_count": 4},
    ],
    "customer_detail": {
        "id": "CUS-001", "name": "北京科技有限公司", "industry": "互联网", "level": "vip",
        "owner": "张三", "address": "北京市海淀区XX路XX号", "phone": "010-12345678",
        "contacts": [
            {"name": "赵经理", "title": "采购总监", "phone": "13800001111", "email": "zhao@bjtech.com", "is_primary": True},
            {"name": "钱工程师", "title": "技术经理", "phone": "13800002222", "email": "qian@bjtech.com", "is_primary": False},
        ],
        "total_deals": 5, "total_amount": "1280000",
        "last_contact_date": "2026-05-15",
    },
    "opportunities": [
        {"id": "OPP-001", "customer_id": "CUS-001", "title": "ERP系统升级", "stage": "proposal", "amount": "580000", "owner": "张三", "expected_close_date": "2026-07-01"},
        {"id": "OPP-002", "customer_id": "CUS-002", "title": "数据平台建设", "stage": "negotiation", "amount": "1200000", "owner": "李四", "expected_close_date": "2026-06-15"},
        {"id": "OPP-003", "customer_id": "CUS-005", "title": "AI助手定制开发", "stage": "prospect", "amount": "850000", "owner": "李四", "expected_close_date": "2026-09-01"},
        {"id": "OPP-004", "customer_id": "CUS-004", "title": "MES系统实施", "stage": "qualification", "amount": "2000000", "owner": "张三", "expected_close_date": "2026-10-01"},
    ],
}

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("CRM")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_customers(
    keyword: str = "",
    industry: str = "",
    level: str = "",
    owner: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询客户列表

    根据筛选条件查询客户，支持按关键词、行业、等级、负责人筛选。

    Args:
        keyword: 搜索关键词，匹配客户名称和联系人
        industry: 行业筛选
        level: 客户等级，vip/important/normal/potential
        owner: 负责人姓名或工号
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        客户列表 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["customers"], "total": len(MOCK_DATA["customers"]), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if industry:
        params["industry"] = industry
    if level:
        params["level"] = level
    if owner:
        params["owner"] = owner

    result = await client.get("/customers", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询客户列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_customer_detail(customer_id: str) -> str:
    """获取客户详情

    根据客户ID获取客户的完整信息，包括基本信息、联系人、历史交易等。

    Args:
        customer_id: 客户ID

    Returns:
        客户详情 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["customer_detail"])

    client = _get_api_client()
    result = await client.get(f"/customers/{customer_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取客户详情失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_opportunities(
    customer_id: str = "",
    stage: str = "",
    owner: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询商机列表

    根据筛选条件查询商机，支持按客户、阶段、负责人筛选。

    Args:
        customer_id: 客户ID，为空则查全部
        stage: 商机阶段，prospect(初步接触)/qualification(需求确认)/
               proposal(方案报价)/negotiation(商务谈判)/closed_won(赢单)/closed_lost(输单)
        owner: 负责人姓名或工号
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        商机列表 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["opportunities"], "total": len(MOCK_DATA["opportunities"]), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if customer_id:
        params["customer_id"] = customer_id
    if stage:
        params["stage"] = stage
    if owner:
        params["owner"] = owner

    result = await client.get("/opportunities", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询商机列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def update_opportunity(
    opportunity_id: str,
    stage: str = "",
    amount: str = "",
    expected_close_date: str = "",
    remark: str = "",
) -> str:
    """更新商机状态

    更新指定商机的阶段、金额、预计成交日期等信息。

    Args:
        opportunity_id: 商机ID
        stage: 新阶段，prospect/qualification/proposal/negotiation/closed_won/closed_lost
        amount: 商机金额
        expected_close_date: 预计成交日期，格式 YYYY-MM-DD
        remark: 备注

    Returns:
        更新结果 JSON 字符串
    """
    valid_stages = ("prospect", "qualification", "proposal", "negotiation", "closed_won", "closed_lost")
    if stage and stage not in valid_stages:
        return format_result(False, error=f"不支持的商机阶段: {stage}，可选 {valid_stages}")

    if is_mock_mode():
        return format_result(True, data={"opportunity_id": opportunity_id, "status": "updated"})

    client = _get_api_client()
    payload: dict[str, Any] = {}
    if stage:
        payload["stage"] = stage
    if amount:
        payload["amount"] = amount
    if expected_close_date:
        payload["expected_close_date"] = expected_close_date
    if remark:
        payload["remark"] = remark

    if not payload:
        return format_result(False, error="未指定需要更新的字段")

    result = await client.put(f"/opportunities/{opportunity_id}", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "更新商机失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def add_customer_contact(
    customer_id: str,
    name: str,
    title: str = "",
    phone: str = "",
    email: str = "",
    is_primary: bool = False,
) -> str:
    """添加客户联系人

    为指定客户添加新的联系人信息。

    Args:
        customer_id: 客户ID
        name: 联系人姓名
        title: 职位
        phone: 联系电话
        email: 邮箱地址
        is_primary: 是否为主要联系人

    Returns:
        添加结果 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"customer_id": customer_id, "contact_name": name, "status": "added"})

    client = _get_api_client()
    payload: dict[str, Any] = {"name": name}
    if title:
        payload["title"] = title
    if phone:
        payload["phone"] = phone
    if email:
        payload["email"] = email
    payload["is_primary"] = is_primary

    result = await client.post(f"/customers/{customer_id}/contacts", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "添加客户联系人失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
