"""财务系统 MCP 服务

提供报销查询、报销提交、预算查询、发票管理等工具，
通过 MCP 协议供 Agent 调用，底层对接企业财务系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("finance-mcp-server", host="0.0.0.0", port=9009)

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("FINANCE")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_reimbursements(
    status: str = "",
    applicant: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询报销列表

    根据筛选条件查询报销记录，支持按状态、申请人、日期范围筛选。

    Args:
        status: 报销状态，draft(草稿)/submitted(已提交)/approved(已通过)/rejected(已驳回)/paid(已支付)
        applicant: 申请人姓名或工号
        date_from: 起始日期，格式 YYYY-MM-DD
        date_to: 截止日期，格式 YYYY-MM-DD
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        报销列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if status:
        params["status"] = status
    if applicant:
        params["applicant"] = applicant
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    result = await client.get("/reimbursements", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询报销列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def submit_reimbursement(
    title: str,
    amount: str,
    category: str,
    description: str = "",
    invoice_ids: str = "",
) -> str:
    """提交报销申请

    提交一条报销申请，此为敏感操作需确认后执行。

    Args:
        title: 报销标题
        amount: 报销金额
        category: 报销类别，travel(差旅)/office(办公)/meal(餐饮)/other(其他)
        description: 报销说明
        invoice_ids: 关联发票ID，多个用逗号分隔

    Returns:
        提交结果 JSON 字符串
    """
    valid_categories = ("travel", "office", "meal", "other")
    if category not in valid_categories:
        return format_result(False, error=f"不支持的报销类别: {category}，可选 {valid_categories}")

    client = _get_api_client()
    payload: dict[str, Any] = {
        "title": title,
        "amount": amount,
        "category": category,
    }
    if description:
        payload["description"] = description
    if invoice_ids:
        payload["invoice_ids"] = [i.strip() for i in invoice_ids.split(",")]

    result = await client.post("/reimbursements", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "提交报销申请失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_budget(
    department: str = "",
    year: str = "",
    quarter: str = "",
) -> str:
    """查询预算信息

    查询部门或项目的预算使用情况。

    Args:
        department: 部门名称，为空则查询当前用户所在部门
        year: 年度，格式 YYYY，为空则当前年
        quarter: 季度，Q1/Q2/Q3/Q4，为空则全年

    Returns:
        预算信息 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {}
    if department:
        params["department"] = department
    if year:
        params["year"] = year
    if quarter:
        params["quarter"] = quarter

    result = await client.get("/budgets", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询预算信息失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def query_invoices(
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询发票列表

    根据筛选条件查询发票记录。

    Args:
        status: 发票状态，pending(待处理)/verified(已验证)/rejected(已驳回)/reimbursed(已报销)
        date_from: 起始日期，格式 YYYY-MM-DD
        date_to: 截止日期，格式 YYYY-MM-DD
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        发票列表 JSON 字符串
    """
    client = _get_api_client()
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if status:
        params["status"] = status
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    result = await client.get("/invoices", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询发票列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def upload_invoice(
    invoice_type: str,
    amount: str,
    invoice_date: str,
    invoice_number: str = "",
    description: str = "",
) -> str:
    """上传发票信息

    上传一张发票的基本信息用于报销关联。

    Args:
        invoice_type: 发票类型，vat_special(增值税专票)/vat_normal(增值税普票)/receipt(收据)
        amount: 发票金额
        invoice_date: 开票日期，格式 YYYY-MM-DD
        invoice_number: 发票号码
        description: 发票说明

    Returns:
        上传结果 JSON 字符串
    """
    valid_types = ("vat_special", "vat_normal", "receipt")
    if invoice_type not in valid_types:
        return format_result(False, error=f"不支持的发票类型: {invoice_type}，可选 {valid_types}")

    client = _get_api_client()
    payload: dict[str, Any] = {
        "invoice_type": invoice_type,
        "amount": amount,
        "invoice_date": invoice_date,
    }
    if invoice_number:
        payload["invoice_number"] = invoice_number
    if description:
        payload["description"] = description

    result = await client.post("/invoices", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "上传发票失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
