"""邮件系统 MCP 服务

提供邮件查询、发送、分类等工具，
通过 MCP 协议供 Agent 调用，底层对接企业邮件系统标准 API。
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.base import EnterpriseAPIClient, format_result, is_mock_mode, load_enterprise_config

logger = logging.getLogger(__name__)

mcp = FastMCP("email-mcp-server", host="0.0.0.0", port=9002)

# Mock 数据
MOCK_DATA = {
    "emails": [
        {"id": "EM-2026-001", "subject": "关于项目进度汇报", "sender": "zhangsan@company.com", "folder": "inbox", "date": "2026-05-19 09:30:00", "is_read": False, "priority": "high"},
        {"id": "EM-2026-002", "subject": "下周会议安排", "sender": "lisi@company.com", "folder": "inbox", "date": "2026-05-19 14:00:00", "is_read": True, "priority": "normal"},
        {"id": "EM-2026-003", "subject": "报销审批结果通知", "sender": "system@company.com", "folder": "inbox", "date": "2026-05-18 16:45:00", "is_read": True, "priority": "normal"},
        {"id": "EM-2026-004", "subject": "Q2季度OKR确认", "sender": "zhaozg@company.com", "folder": "inbox", "date": "2026-05-17 10:00:00", "is_read": False, "priority": "high"},
        {"id": "EM-2026-005", "subject": "新员工入职通知", "sender": "hr@company.com", "folder": "inbox", "date": "2026-05-16 09:00:00", "is_read": True, "priority": "low"},
        {"id": "EM-2026-006", "subject": "技术方案评审邀请", "sender": "wangwu@company.com", "folder": "inbox", "date": "2026-05-20 08:15:00", "is_read": False, "priority": "normal"},
    ],
    "email_detail": {
        "id": "EM-2026-001", "subject": "关于项目进度汇报", "sender": "zhangsan@company.com", "to": "me@company.com",
        "date": "2026-05-19 09:30:00", "priority": "high",
        "body": "你好，\n\n关于A项目的进度，目前开发已完成80%，预计6月底可上线。\n\n关键里程碑：\n1. 后端API开发已完成90%\n2. 前端页面开发完成75%\n3. 联调测试计划6月第一周开始\n\n请知悉。\n\n张三",
        "attachments": [{"name": "项目进度表.xlsx", "size": "256KB"}],
    },
    "email_stats": {
        "total": 156, "unread": 12, "sent": 45,
        "by_folder": {"inbox": 89, "sent": 45, "draft": 8, "trash": 14},
    },
}

_api_client: EnterpriseAPIClient | None = None


def _get_api_client() -> EnterpriseAPIClient:
    global _api_client
    if _api_client is None:
        config = load_enterprise_config("EMAIL")
        _api_client = EnterpriseAPIClient(config)
    return _api_client


@mcp.tool()
async def query_emails(
    folder: str = "inbox",
    keyword: str = "",
    sender: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """查询邮件列表

    根据筛选条件查询邮件，支持按文件夹、关键词、发件人、日期范围筛选。

    Args:
        folder: 邮箱文件夹，inbox(收件箱)/sent(已发送)/drafts(草稿箱)/trash(回收站)，默认inbox
        keyword: 搜索关键词，匹配主题和正文
        sender: 发件人筛选
        date_from: 起始日期，格式 YYYY-MM-DD
        date_to: 截止日期，格式 YYYY-MM-DD
        page: 页码，默认1
        page_size: 每页条数，默认20

    Returns:
        邮件列表 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"items": MOCK_DATA["emails"], "total": len(MOCK_DATA["emails"]), "page": page, "page_size": page_size})

    client = _get_api_client()
    params: dict[str, Any] = {"folder": folder, "page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if sender:
        params["sender"] = sender
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    result = await client.get("/emails", params=params)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "查询邮件列表失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def get_email_detail(email_id: str) -> str:
    """获取邮件详情

    根据邮件ID获取邮件的完整内容，包括正文、附件列表等。

    Args:
        email_id: 邮件ID

    Returns:
        邮件详情 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data=MOCK_DATA["email_detail"])

    client = _get_api_client()
    result = await client.get(f"/emails/{email_id}")
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "获取邮件详情失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    priority: str = "normal",
) -> str:
    """发送邮件

    发送一封邮件，此为敏感操作需确认后执行。多个收件人用逗号分隔。

    Args:
        to: 收件人邮箱地址，多个用逗号分隔
        subject: 邮件主题
        body: 邮件正文
        cc: 抄送人邮箱地址，多个用逗号分隔
        bcc: 密送人邮箱地址，多个用逗号分隔
        priority: 优先级，low/normal/high，默认normal

    Returns:
        发送结果 JSON 字符串
    """
    if priority not in ("low", "normal", "high"):
        return format_result(False, error=f"不支持的优先级: {priority}，可选 low/normal/high")

    if is_mock_mode():
        return format_result(True, data={"message_id": "EM-NEW-001", "status": "sent", "to": to, "subject": subject})

    client = _get_api_client()
    payload: dict[str, Any] = {
        "to": to,
        "subject": subject,
        "body": body,
        "priority": priority,
    }
    if cc:
        payload["cc"] = cc
    if bcc:
        payload["bcc"] = bcc

    result = await client.post("/emails/send", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "发送邮件失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def classify_email(email_id: str, category: str = "") -> str:
    """分类邮件

    对指定邮件进行自动分类标记，也可手动指定分类。自动分类时 category 留空。

    Args:
        email_id: 邮件ID
        category: 分类标签，important(重要)/normal(普通)/spam(垃圾)/notification(通知)，
                  为空则自动分类

    Returns:
        分类结果 JSON 字符串
    """
    valid_categories = ("important", "normal", "spam", "notification")
    if category and category not in valid_categories:
        return format_result(False, error=f"不支持的分类: {category}，可选 {valid_categories}")

    if is_mock_mode():
        return format_result(True, data={"email_id": email_id, "category": category or "important", "auto_classified": not category})

    client = _get_api_client()
    payload: dict[str, Any] = {}
    if category:
        payload["category"] = category

    result = await client.post(f"/emails/{email_id}/classify", data=payload)
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "分类邮件失败"))

    return format_result(True, data=result.get("data", result))


@mcp.tool()
async def delete_email(email_id: str) -> str:
    """删除邮件

    将指定邮件移入回收站，此为敏感操作需确认后执行。

    Args:
        email_id: 邮件ID

    Returns:
        操作结果 JSON 字符串
    """
    if is_mock_mode():
        return format_result(True, data={"email_id": email_id, "status": "deleted"})

    client = _get_api_client()
    result = await client.post(f"/emails/{email_id}/delete", data={})
    if result.get("success") is False:
        return format_result(False, error=result.get("error", "删除邮件失败"))

    return format_result(True, data=result.get("data", result))


if __name__ == "__main__":
    mcp.run(transport="sse")
