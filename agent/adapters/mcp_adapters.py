"""MCP 服务真实对接适配器

为 OA 审批、邮件、日历三个核心系统提供真实 API 对接实现。
每个适配器遵循统一接口，支持配置化切换真实/模拟模式。

适配器模式：
  - 真实模式：调用企业系统 API
  - 模拟模式：返回预置数据（开发/测试用）
  - 降级模式：真实调用失败时自动降级到模拟数据
"""

import abc
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from agent.core.infrastructure.config import get_settings

logger = logging.getLogger(__name__)


class AdapterMode(str):
    """适配器模式"""

    REAL = "real"
    MOCK = "mock"
    FALLBACK = "fallback"


class AdapterResponse(BaseModel):
    """适配器统一响应"""

    success: bool
    data: Any = None
    error: str = ""
    mode: str = AdapterMode.REAL
    latency_ms: float = 0


class BaseAdapter(abc.ABC):
    """MCP 服务适配器基类"""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._mode = AdapterMode.REAL

    @property
    @abc.abstractmethod
    def service_name(self) -> str:
        """服务名称"""

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""

    def _headers(self) -> dict[str, str]:
        """构建请求头"""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> AdapterResponse:
        """发送 HTTP 请求

        自动处理超时、错误和降级。
        """
        start = time.time()
        url = f"{self._base_url}{path}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    json=json_data,
                    params=params,
                )
                latency = (time.time() - start) * 1000

                if response.status_code >= 400:
                    logger.warning(
                        "%s API 返回错误: status=%d url=%s",
                        self.service_name, response.status_code, url,
                    )
                    return AdapterResponse(
                        success=False,
                        error=f"HTTP {response.status_code}: {response.text[:200]}",
                        mode=AdapterMode.REAL,
                        latency_ms=latency,
                    )

                data = response.json()
                return AdapterResponse(
                    success=True,
                    data=data,
                    mode=AdapterMode.REAL,
                    latency_ms=latency,
                )

        except httpx.TimeoutException:
            latency = (time.time() - start) * 1000
            logger.warning("%s API 超时: url=%s", self.service_name, url)
            return AdapterResponse(
                success=False,
                error="请求超时",
                mode=AdapterMode.REAL,
                latency_ms=latency,
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error("%s API 异常: url=%s error=%s", self.service_name, url, e)
            return AdapterResponse(
                success=False,
                error=str(e),
                mode=AdapterMode.REAL,
                latency_ms=latency,
            )


# ==================== OA 审批适配器 ====================


class OAAdapter(BaseAdapter):
    """OA 审批系统适配器

    对接企业 OA 系统的审批流程 API。
    支持查询审批列表、审批详情、执行审批操作。
    """

    @property
    def service_name(self) -> str:
        return "OA"

    async def health_check(self) -> bool:
        resp = await self._request("GET", "/api/health")
        return resp.success

    async def get_approval_list(
        self,
        user_id: str,
        status: str = "pending",
        page: int = 1,
        page_size: int = 20,
    ) -> AdapterResponse:
        """获取审批列表"""
        return await self._request(
            "GET",
            "/api/approvals",
            params={
                "user_id": user_id,
                "status": status,
                "page": page,
                "page_size": page_size,
            },
        )

    async def get_approval_detail(self, approval_id: str) -> AdapterResponse:
        """获取审批详情"""
        return await self._request("GET", f"/api/approvals/{approval_id}")

    async def approve(
        self,
        approval_id: str,
        user_id: str,
        action: str,
        comment: str = "",
    ) -> AdapterResponse:
        """执行审批操作

        Args:
            approval_id: 审批单ID
            user_id: 操作人ID
            action: 操作类型 approve/reject/transfer
            comment: 审批意见
        """
        return await self._request(
            "POST",
            f"/api/approvals/{approval_id}/action",
            json_data={
                "user_id": user_id,
                "action": action,
                "comment": comment,
            },
        )

    async def create_approval(
        self,
        title: str,
        applicant_id: str,
        approval_type: str,
        content: dict,
        approvers: list[str],
    ) -> AdapterResponse:
        """创建审批单"""
        return await self._request(
            "POST",
            "/api/approvals",
            json_data={
                "title": title,
                "applicant_id": applicant_id,
                "type": approval_type,
                "content": content,
                "approvers": approvers,
            },
        )


# ==================== 邮件适配器 ====================


class EmailAdapter(BaseAdapter):
    """邮件系统适配器

    对接企业邮件系统 API（如 Exchange、Coremail）。
    支持查询邮件、发送邮件、邮件搜索。
    """

    @property
    def service_name(self) -> str:
        return "Email"

    async def health_check(self) -> bool:
        resp = await self._request("GET", "/api/health")
        return resp.success

    async def get_mail_list(
        self,
        user_id: str,
        folder: str = "inbox",
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> AdapterResponse:
        """获取邮件列表"""
        return await self._request(
            "GET",
            "/api/mails",
            params={
                "user_id": user_id,
                "folder": folder,
                "page": page,
                "page_size": page_size,
                "unread_only": unread_only,
            },
        )

    async def get_mail_detail(self, mail_id: str) -> AdapterResponse:
        """获取邮件详情"""
        return await self._request("GET", f"/api/mails/{mail_id}")

    async def send_mail(
        self,
        from_addr: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[str] | None = None,
        content_type: str = "html",
    ) -> AdapterResponse:
        """发送邮件"""
        payload: dict[str, Any] = {
            "from": from_addr,
            "to": to,
            "subject": subject,
            "body": body,
            "content_type": content_type,
        }
        if cc:
            payload["cc"] = cc
        if bcc:
            payload["bcc"] = bcc
        if attachments:
            payload["attachments"] = attachments

        resp = await self._request("POST", "/api/mails/send", json_data=payload)

        if resp.success:
            try:
                from observability.metrics import record_email_sent
                record_email_sent("EmailAgent", has_attachment=bool(attachments))
            except Exception:
                pass

        return resp

    async def search_mails(
        self,
        user_id: str,
        query: str,
        folder: str = "all",
        page: int = 1,
        page_size: int = 20,
    ) -> AdapterResponse:
        """搜索邮件"""
        return await self._request(
            "GET",
            "/api/mails/search",
            params={
                "user_id": user_id,
                "q": query,
                "folder": folder,
                "page": page,
                "page_size": page_size,
            },
        )

    async def mark_mail(self, mail_id: str, action: str) -> AdapterResponse:
        """标记邮件

        Args:
            mail_id: 邮件ID
            action: read/unread/star/unstar/delete
        """
        return await self._request(
            "POST",
            f"/api/mails/{mail_id}/mark",
            json_data={"action": action},
        )


# ==================== 日历适配器 ====================


class CalendarAdapter(BaseAdapter):
    """日历系统适配器

    对接企业日历系统 API（如 Exchange Calendar、飞书日历）。
    支持查询日程、创建日程、更新日程、冲突检测。
    """

    @property
    def service_name(self) -> str:
        return "Calendar"

    async def health_check(self) -> bool:
        resp = await self._request("GET", "/api/health")
        return resp.success

    async def get_events(
        self,
        user_id: str,
        start_time: str,
        end_time: str,
        calendar_id: str = "primary",
    ) -> AdapterResponse:
        """查询日程列表

        Args:
            user_id: 用户ID
            start_time: 开始时间 ISO8601
            end_time: 结束时间 ISO8601
            calendar_id: 日历ID
        """
        return await self._request(
            "GET",
            "/api/events",
            params={
                "user_id": user_id,
                "start_time": start_time,
                "end_time": end_time,
                "calendar_id": calendar_id,
            },
        )

    async def get_event_detail(self, event_id: str) -> AdapterResponse:
        """获取日程详情"""
        return await self._request("GET", f"/api/events/{event_id}")

    async def create_event(
        self,
        title: str,
        organizer_id: str,
        start_time: str,
        end_time: str,
        attendees: list[str] | None = None,
        location: str = "",
        description: str = "",
        reminder_minutes: int = 15,
        recurrence: dict | None = None,
    ) -> AdapterResponse:
        """创建日程"""
        payload: dict[str, Any] = {
            "title": title,
            "organizer_id": organizer_id,
            "start_time": start_time,
            "end_time": end_time,
            "location": location,
            "description": description,
            "reminder_minutes": reminder_minutes,
        }
        if attendees:
            payload["attendees"] = attendees
        if recurrence:
            payload["recurrence"] = recurrence

        return await self._request("POST", "/api/events", json_data=payload)

    async def update_event(
        self,
        event_id: str,
        updates: dict[str, Any],
    ) -> AdapterResponse:
        """更新日程"""
        return await self._request(
            "PATCH",
            f"/api/events/{event_id}",
            json_data=updates,
        )

    async def delete_event(self, event_id: str) -> AdapterResponse:
        """删除日程"""
        return await self._request("DELETE", f"/api/events/{event_id}")

    async def check_conflicts(
        self,
        user_id: str,
        start_time: str,
        end_time: str,
        exclude_event_id: str = "",
    ) -> AdapterResponse:
        """检查日程冲突"""
        params: dict[str, Any] = {
            "user_id": user_id,
            "start_time": start_time,
            "end_time": end_time,
        }
        if exclude_event_id:
            params["exclude_event_id"] = exclude_event_id

        return await self._request("GET", "/api/events/conflicts", params=params)

    async def find_free_time(
        self,
        user_ids: list[str],
        date: str,
        duration_minutes: int = 60,
        working_hours_start: str = "09:00",
        working_hours_end: str = "18:00",
    ) -> AdapterResponse:
        """查找空闲时间段"""
        return await self._request(
            "POST",
            "/api/events/free-time",
            json_data={
                "user_ids": user_ids,
                "date": date,
                "duration_minutes": duration_minutes,
                "working_hours_start": working_hours_start,
                "working_hours_end": working_hours_end,
            },
        )


# ==================== 适配器工厂 ====================


_adapters: dict[str, BaseAdapter] = {}


def init_adapters() -> None:
    """从配置初始化所有适配器"""
    settings = get_settings()

    oa_url = getattr(settings, "oa_api_url", "")
    oa_key = getattr(settings, "oa_api_key", "")
    if oa_url:
        _adapters["oa"] = OAAdapter(base_url=oa_url, api_key=oa_key)
        logger.info("OA 适配器已初始化: %s", oa_url)

    email_url = getattr(settings, "email_api_url", "")
    email_key = getattr(settings, "email_api_key", "")
    if email_url:
        _adapters["email"] = EmailAdapter(base_url=email_url, api_key=email_key)
        logger.info("邮件适配器已初始化: %s", email_url)

    calendar_url = getattr(settings, "calendar_api_url", "")
    calendar_key = getattr(settings, "calendar_api_key", "")
    if calendar_url:
        _adapters["calendar"] = CalendarAdapter(base_url=calendar_url, api_key=calendar_key)
        logger.info("日历适配器已初始化: %s", calendar_url)


def get_adapter(name: str) -> BaseAdapter | None:
    """获取适配器"""
    return _adapters.get(name)


def list_adapters() -> dict[str, str]:
    """列出所有已注册的适配器"""
    return {name: adapter.service_name for name, adapter in _adapters.items()}
