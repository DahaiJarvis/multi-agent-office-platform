"""人工确认管理器

管理任务执行过程中需要人工确认的环节，复用现有 approval_flow.py 的审批机制，
新增"降级决策"和"部分失败继续"两种确认类型。

确认类型：
  - sensitive_action: 敏感操作确认（发送邮件、审批操作等），复用 approval_flow.py
  - degrade_decision: Agent降级/替换时需用户确认
  - partial_failure: 部分步骤失败，需用户决定是否继续

存储设计：
  - 活跃确认单: Redis Hash，TTL 24h
  - 用户确认索引: Redis Sorted Set（按创建时间排序）
"""

import json
import logging
import time
import uuid
from enum import Enum
from typing import Any

import redis.asyncio as aioredis

from agent.core.infrastructure.config import get_settings

logger = logging.getLogger(__name__)


class ConfirmType(str, Enum):
    """确认类型"""

    SENSITIVE_ACTION = "sensitive_action"
    DEGRADE_DECISION = "degrade_decision"
    PARTIAL_FAILURE = "partial_failure"


class ConfirmStatus(str, Enum):
    """确认状态"""

    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ConfirmOption:
    """确认选项

    Attributes:
        label: 选项显示文本
        value: 选项值（continue/skip/cancel/retry）
        description: 选项描述
    """

    def __init__(
        self,
        label: str = "",
        value: str = "",
        description: str = "",
    ) -> None:
        self.label = label
        self.value = value
        self.description = description

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "value": self.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "ConfirmOption":
        return cls(
            label=data.get("label", ""),
            value=data.get("value", ""),
            description=data.get("description", ""),
        )


class ConfirmRequest:
    """确认请求

    Attributes:
        confirm_id: 确认单唯一标识
        execution_id: 关联的任务执行ID
        step_index: 关联的步骤索引
        session_id: 关联的会话ID
        user_id: 发起用户ID
        confirm_type: 确认类型
        reason: 确认原因
        options: 用户可选的操作列表
        status: 当前确认状态
        decision: 用户决策（continue/skip/cancel/retry）
        comment: 用户备注
        agent_name: 相关的Agent名称
        created_at: 创建时间戳
        resolved_at: 处理时间戳
        expires_at: 过期时间戳
    """

    def __init__(
        self,
        confirm_id: str = "",
        execution_id: str = "",
        step_index: int = 0,
        session_id: str = "",
        user_id: str = "",
        confirm_type: ConfirmType = ConfirmType.SENSITIVE_ACTION,
        reason: str = "",
        options: list[ConfirmOption] | None = None,
        status: ConfirmStatus = ConfirmStatus.PENDING,
        decision: str = "",
        comment: str = "",
        agent_name: str = "",
        created_at: float = 0,
        resolved_at: float = 0,
        expires_at: float = 0,
    ) -> None:
        self.confirm_id = confirm_id
        self.execution_id = execution_id
        self.step_index = step_index
        self.session_id = session_id
        self.user_id = user_id
        self.confirm_type = confirm_type
        self.reason = reason
        self.options = options or []
        self.status = status
        self.decision = decision
        self.comment = comment
        self.agent_name = agent_name
        self.created_at = created_at
        self.resolved_at = resolved_at
        self.expires_at = expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirm_id": self.confirm_id,
            "execution_id": self.execution_id,
            "step_index": self.step_index,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "confirm_type": self.confirm_type.value,
            "reason": self.reason,
            "options": [o.to_dict() for o in self.options],
            "status": self.status.value,
            "decision": self.decision,
            "comment": self.comment,
            "agent_name": self.agent_name,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfirmRequest":
        options_data = data.get("options", [])
        return cls(
            confirm_id=data.get("confirm_id", ""),
            execution_id=data.get("execution_id", ""),
            step_index=data.get("step_index", 0),
            session_id=data.get("session_id", ""),
            user_id=data.get("user_id", ""),
            confirm_type=ConfirmType(data.get("confirm_type", "sensitive_action")),
            reason=data.get("reason", ""),
            options=[ConfirmOption.from_dict(o) for o in options_data],
            status=ConfirmStatus(data.get("status", "pending")),
            decision=data.get("decision", ""),
            comment=data.get("comment", ""),
            agent_name=data.get("agent_name", ""),
            created_at=data.get("created_at", 0),
            resolved_at=data.get("resolved_at", 0),
            expires_at=data.get("expires_at", 0),
        )


class HumanConfirmManager:
    """人工确认管理器

    复用现有 approval_flow.py 的存储机制（Redis Hash + Sorted Set），
    新增降级决策和部分失败确认类型。
    """

    CONFIRM_KEY_PREFIX = "human_confirm:"
    CONFIRM_USER_INDEX_PREFIX = "human_confirm_user:"
    CONFIRM_EXEC_INDEX_PREFIX = "human_confirm_exec:"

    DEFAULT_TIMEOUT_HOURS = 24

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def request_confirm(
        self,
        execution_id: str,
        step_index: int,
        session_id: str,
        user_id: str,
        confirm_type: ConfirmType,
        reason: str,
        options: list[ConfirmOption] | None = None,
        agent_name: str = "",
        timeout_hours: int = 24,
    ) -> ConfirmRequest:
        """创建人工确认请求

        Args:
            execution_id: 任务执行ID
            step_index: 步骤索引
            session_id: 会话ID
            user_id: 用户ID
            confirm_type: 确认类型
            reason: 确认原因
            options: 用户可选的操作列表
            agent_name: 相关的Agent名称
            timeout_hours: 超时时间（小时）

        Returns:
            创建的 ConfirmRequest
        """
        now = time.time()
        confirm_id = f"cfm-{uuid.uuid4().hex[:12]}"

        if options is None:
            options = self._default_options(confirm_type)

        confirm = ConfirmRequest(
            confirm_id=confirm_id,
            execution_id=execution_id,
            step_index=step_index,
            session_id=session_id,
            user_id=user_id,
            confirm_type=confirm_type,
            reason=reason,
            options=options,
            status=ConfirmStatus.PENDING,
            agent_name=agent_name,
            created_at=now,
            expires_at=now + timeout_hours * 3600,
        )

        redis = await self._get_redis()
        key = f"{self.CONFIRM_KEY_PREFIX}{confirm_id}"
        ttl = timeout_hours * 3600 + 3600
        await redis.setex(key, ttl, json.dumps(confirm.to_dict(), ensure_ascii=False))

        user_index_key = f"{self.CONFIRM_USER_INDEX_PREFIX}{user_id}"
        await redis.zadd(user_index_key, {confirm_id: now})
        await redis.expire(user_index_key, 86400 * 7)

        exec_index_key = f"{self.CONFIRM_EXEC_INDEX_PREFIX}{execution_id}"
        await redis.zadd(exec_index_key, {confirm_id: now})
        await redis.expire(exec_index_key, 86400 * 7)

        logger.info(
            "人工确认请求已创建: id=%s type=%s exec=%s step=%d",
            confirm_id, confirm_type.value, execution_id, step_index,
        )

        return confirm

    async def handle_confirm(
        self,
        confirm_id: str,
        decision: str,
        comment: str = "",
    ) -> ConfirmRequest | None:
        """处理人工确认结果

        Args:
            confirm_id: 确认单ID
            decision: 决策（continue/skip/cancel/retry）
            comment: 备注

        Returns:
            更新后的 ConfirmRequest 或 None
        """
        confirm = await self.get_confirm(confirm_id)
        if confirm is None:
            return None

        if confirm.status != ConfirmStatus.PENDING:
            logger.warning(
                "确认单状态非 PENDING，无法处理: id=%s status=%s",
                confirm_id, confirm.status.value,
            )
            return None

        confirm.status = ConfirmStatus.RESOLVED
        confirm.decision = decision
        confirm.comment = comment
        confirm.resolved_at = time.time()

        await self._update_confirm(confirm)

        logger.info(
            "人工确认已处理: id=%s decision=%s exec=%s step=%d",
            confirm_id, decision, confirm.execution_id, confirm.step_index,
        )

        return confirm

    async def get_confirm(self, confirm_id: str) -> ConfirmRequest | None:
        """获取确认单详情

        Args:
            confirm_id: 确认单ID

        Returns:
            ConfirmRequest 或 None
        """
        redis = await self._get_redis()
        key = f"{self.CONFIRM_KEY_PREFIX}{confirm_id}"
        data = await redis.get(key)

        if data is None:
            return None

        try:
            return ConfirmRequest.from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("解析确认单数据失败: id=%s error=%s", confirm_id, e)
            return None

    async def get_pending_confirms(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[ConfirmRequest]:
        """查询用户的待确认列表

        Args:
            user_id: 用户ID
            limit: 返回数量上限

        Returns:
            待确认列表
        """
        redis = await self._get_redis()
        user_index_key = f"{self.CONFIRM_USER_INDEX_PREFIX}{user_id}"
        confirm_ids = await redis.zrevrange(user_index_key, 0, limit - 1)

        pending_list: list[ConfirmRequest] = []
        for confirm_id in confirm_ids:
            confirm = await self.get_confirm(confirm_id)
            if confirm and confirm.status == ConfirmStatus.PENDING:
                pending_list.append(confirm)

        return pending_list

    async def get_pending_confirm_by_step(
        self,
        execution_id: str,
        step_index: int,
    ) -> ConfirmRequest | None:
        """查询指定步骤的待确认请求

        Args:
            execution_id: 任务执行ID
            step_index: 步骤索引

        Returns:
            ConfirmRequest 或 None
        """
        redis = await self._get_redis()
        exec_index_key = f"{self.CONFIRM_EXEC_INDEX_PREFIX}{execution_id}"
        confirm_ids = await redis.zrevrange(exec_index_key, 0, -1)

        for confirm_id in confirm_ids:
            confirm = await self.get_confirm(confirm_id)
            if (
                confirm
                and confirm.status == ConfirmStatus.PENDING
                and confirm.step_index == step_index
            ):
                return confirm

        return None

    async def check_expired(self) -> list[ConfirmRequest]:
        """检查过期确认，自动标记为 EXPIRED

        Returns:
            过期的确认单列表
        """
        redis = await self._get_redis()
        now = time.time()
        expired_list: list[ConfirmRequest] = []

        pattern = f"{self.CONFIRM_KEY_PREFIX}*"
        async for key in redis.scan_iter(match=pattern, count=100):
            data = await redis.get(key)
            if data is None:
                continue
            try:
                confirm = ConfirmRequest.from_dict(json.loads(data))
                if (
                    confirm.status == ConfirmStatus.PENDING
                    and confirm.expires_at > 0
                    and now > confirm.expires_at
                ):
                    confirm.status = ConfirmStatus.EXPIRED
                    confirm.resolved_at = now
                    await self._update_confirm(confirm)
                    expired_list.append(confirm)
            except (json.JSONDecodeError, KeyError):
                continue

        return expired_list

    async def cancel_confirm(self, confirm_id: str) -> ConfirmRequest | None:
        """取消确认单

        Args:
            confirm_id: 确认单ID

        Returns:
            更新后的 ConfirmRequest 或 None
        """
        confirm = await self.get_confirm(confirm_id)
        if confirm is None:
            return None

        if confirm.status != ConfirmStatus.PENDING:
            return None

        confirm.status = ConfirmStatus.CANCELLED
        confirm.resolved_at = time.time()
        await self._update_confirm(confirm)

        logger.info("确认单已取消: id=%s", confirm_id)
        return confirm

    def _default_options(self, confirm_type: ConfirmType) -> list[ConfirmOption]:
        """根据确认类型生成默认选项

        Args:
            confirm_type: 确认类型

        Returns:
            默认选项列表
        """
        if confirm_type == ConfirmType.SENSITIVE_ACTION:
            return [
                ConfirmOption(label="确认执行", value="continue", description="确认执行此敏感操作"),
                ConfirmOption(label="跳过此步骤", value="skip", description="跳过当前操作，继续执行后续步骤"),
                ConfirmOption(label="取消任务", value="cancel", description="取消整个任务"),
            ]
        elif confirm_type == ConfirmType.DEGRADE_DECISION:
            return [
                ConfirmOption(label="接受降级", value="continue", description="使用降级方案继续执行"),
                ConfirmOption(label="重试原Agent", value="retry", description="重新尝试原始Agent"),
                ConfirmOption(label="跳过此步骤", value="skip", description="跳过当前步骤继续后续"),
                ConfirmOption(label="取消任务", value="cancel", description="取消整个任务"),
            ]
        elif confirm_type == ConfirmType.PARTIAL_FAILURE:
            return [
                ConfirmOption(label="继续执行", value="continue", description="标记失败步骤，继续后续步骤"),
                ConfirmOption(label="重试", value="retry", description="重试失败的步骤"),
                ConfirmOption(label="跳过", value="skip", description="跳过失败步骤"),
                ConfirmOption(label="取消任务", value="cancel", description="取消整个任务"),
            ]
        return [
            ConfirmOption(label="继续", value="continue", description="继续执行"),
            ConfirmOption(label="取消", value="cancel", description="取消任务"),
        ]

    async def _update_confirm(self, confirm: ConfirmRequest) -> None:
        """更新确认单到 Redis

        Args:
            confirm: 确认单对象
        """
        redis = await self._get_redis()
        key = f"{self.CONFIRM_KEY_PREFIX}{confirm.confirm_id}"

        ttl = int(confirm.expires_at - time.time()) if confirm.expires_at > 0 else 86400
        if ttl < 60:
            ttl = 3600

        await redis.setex(key, ttl, json.dumps(confirm.to_dict(), ensure_ascii=False))


_human_confirm_manager: HumanConfirmManager | None = None


def get_human_confirm_manager() -> HumanConfirmManager:
    """获取全局人工确认管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _human_confirm_manager
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_human_confirm_manager() is not None:
            return ctx.get_human_confirm_manager()
    except Exception as e:
        logger.debug("操作失败，已忽略: %s", e)
    if _human_confirm_manager is None:
        _human_confirm_manager = HumanConfirmManager()
    return _human_confirm_manager
