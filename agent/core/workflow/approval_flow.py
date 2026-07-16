"""审批流管理

实现审批状态机，与 Guardrails 联动，支持敏感操作自动挂起和审批回调。

审批单存储：
  - 活跃审批单: Redis Hash，TTL 24h
  - 审批索引: Redis Sorted Set（按创建时间排序）
  - 归档审批单: PostgreSQL（审批完成后归档）

审批状态机：
  PENDING -> APPROVED -> EXECUTED
  PENDING -> REJECTED
  PENDING -> EXPIRED（超时自动标记）
  PENDING -> CANCELLED（用户取消）

支持多级审批链：审批单可包含多个审批步骤，每步需指定审批人角色。
"""

import json
import logging
import time
import uuid
from enum import Enum
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    """审批状态"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ApprovalRequest:
    """审批单

    Attributes:
        approval_id: 审批单唯一标识
        session_id: 关联的会话ID
        user_id: 发起用户ID
        agent_name: 执行操作的 Agent 名称
        tool_name: 敏感操作工具名称
        tool_input: 工具输入参数
        reason: 审批原因
        status: 当前审批状态
        approver: 审批人
        approver_role: 审批人角色
        created_at: 创建时间戳
        resolved_at: 审批处理时间戳
        expires_at: 过期时间戳
        approval_chain: 多级审批链配置
        current_step: 当前审批步骤（从0开始）
    """

    def __init__(
        self,
        approval_id: str = "",
        session_id: str = "",
        user_id: str = "",
        agent_name: str = "",
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        reason: str = "",
        status: ApprovalStatus = ApprovalStatus.PENDING,
        approver: str = "",
        approver_role: str = "",
        created_at: float = 0,
        resolved_at: float = 0,
        expires_at: float = 0,
        approval_chain: list[dict[str, Any]] | None = None,
        current_step: int = 0,
    ) -> None:
        self.approval_id = approval_id
        self.session_id = session_id
        self.user_id = user_id
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.tool_input = tool_input or {}
        self.reason = reason
        self.status = status
        self.approver = approver
        self.approver_role = approver_role
        self.created_at = created_at
        self.resolved_at = resolved_at
        self.expires_at = expires_at
        self.approval_chain = approval_chain or []
        self.current_step = current_step

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "approval_id": self.approval_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "agent_name": self.agent_name,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "reason": self.reason,
            "status": self.status.value,
            "approver": self.approver,
            "approver_role": self.approver_role,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "expires_at": self.expires_at,
            "approval_chain": self.approval_chain,
            "current_step": self.current_step,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        """从字典反序列化"""
        return cls(
            approval_id=data.get("approval_id", ""),
            session_id=data.get("session_id", ""),
            user_id=data.get("user_id", ""),
            agent_name=data.get("agent_name", ""),
            tool_name=data.get("tool_name", ""),
            tool_input=data.get("tool_input", {}),
            reason=data.get("reason", ""),
            status=ApprovalStatus(data.get("status", "pending")),
            approver=data.get("approver", ""),
            approver_role=data.get("approver_role", ""),
            created_at=data.get("created_at", 0),
            resolved_at=data.get("resolved_at", 0),
            expires_at=data.get("expires_at", 0),
            approval_chain=data.get("approval_chain", []),
            current_step=data.get("current_step", 0),
        )


class ApprovalFlowManager:
    """审批流管理器

    审批单存储：Redis（活跃，TTL 24h）+ PostgreSQL（归档）
    """

    # Redis 键前缀
    APPROVAL_KEY_PREFIX = "approval:"
    APPROVAL_INDEX_KEY = "approval_index"
    APPROVAL_USER_INDEX_PREFIX = "approval_user:"

    # 默认过期时间（秒）
    DEFAULT_TIMEOUT_HOURS = 24

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 连接

        复用全局统一连接管理器，避免重复创建连接。
        """
        if self._redis is None:
            from agent.core.infrastructure.redis_manager import get_redis_client
            self._redis = await get_redis_client()
        return self._redis

    async def create_approval(
        self,
        session_id: str,
        user_id: str,
        agent_name: str,
        tool_name: str,
        tool_input: dict[str, Any],
        reason: str,
        approval_chain: list[dict[str, Any]] | None = None,
        timeout_hours: int = 24,
    ) -> ApprovalRequest:
        """创建审批单

        Args:
            session_id: 会话ID
            user_id: 发起用户ID
            agent_name: Agent 名称
            tool_name: 敏感操作工具名称
            tool_input: 工具输入参数
            reason: 审批原因
            approval_chain: 多级审批链配置，每项包含 {"role": "manager", "name": ""}
            timeout_hours: 审批超时时间（小时）

        Returns:
            创建的 ApprovalRequest
        """
        now = time.time()
        approval_id = f"apr-{uuid.uuid4().hex[:12]}"

        approval = ApprovalRequest(
            approval_id=approval_id,
            session_id=session_id,
            user_id=user_id,
            agent_name=agent_name,
            tool_name=tool_name,
            tool_input=tool_input,
            reason=reason,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=now + timeout_hours * 3600,
            approval_chain=approval_chain or [],
            current_step=0,
        )

        # 存储到 Redis
        redis = await self._get_redis()
        key = f"{self.APPROVAL_KEY_PREFIX}{approval_id}"
        ttl = timeout_hours * 3600 + 3600  # 比过期时间多1小时，确保过期检查能执行
        await redis.setex(key, ttl, json.dumps(approval.to_dict(), ensure_ascii=False))

        # 添加到审批索引（按创建时间排序）
        await redis.zadd(self.APPROVAL_INDEX_KEY, {approval_id: now})

        # 添加到用户审批索引
        user_index_key = f"{self.APPROVAL_USER_INDEX_PREFIX}{user_id}"
        await redis.zadd(user_index_key, {approval_id: now})
        await redis.expire(user_index_key, 86400 * 7)

        # 如果有审批链，添加到审批人角色的待审批索引
        if approval.approval_chain:
            first_step = approval.approval_chain[0]
            role = first_step.get("role", "")
            if role:
                role_index_key = f"approval_role:{role}"
                await redis.zadd(role_index_key, {approval_id: now})
                await redis.expire(role_index_key, 86400 * 7)

        logger.info(
            "审批单已创建: id=%s tool=%s user=%s chain_steps=%d",
            approval_id, tool_name, user_id, len(approval.approval_chain),
        )

        return approval

    async def approve(
        self,
        approval_id: str,
        approver: str,
        comment: str = "",
    ) -> ApprovalRequest | None:
        """审批通过

        如果是多级审批，推进到下一步；
        如果是最后一步，状态变为 APPROVED。

        Args:
            approval_id: 审批单ID
            approver: 审批人
            comment: 审批备注

        Returns:
            更新后的 ApprovalRequest 或 None
        """
        approval = await self.get_approval(approval_id)
        if approval is None:
            return None

        if approval.status != ApprovalStatus.PENDING:
            logger.warning("审批单状态非 PENDING，无法通过: id=%s status=%s", approval_id, approval.status)
            return None

        # 记录审批人
        approval.approver = approver
        approval.resolved_at = time.time()

        # 多级审批：推进到下一步
        if approval.approval_chain and approval.current_step < len(approval.approval_chain) - 1:
            approval.current_step += 1
            approval.approver = ""  # 清空，等待下一级审批人
            approval.resolved_at = 0

            # 添加到下一级审批人角色的待审批索引
            next_step = approval.approval_chain[approval.current_step]
            role = next_step.get("role", "")
            if role:
                redis = await self._get_redis()
                role_index_key = f"approval_role:{role}"
                await redis.zadd(role_index_key, {approval_id: time.time()})

            logger.info(
                "审批单推进到下一步: id=%s step=%d/%d",
                approval_id, approval.current_step + 1, len(approval.approval_chain),
            )
        else:
            # 最后一步或无审批链：标记为 APPROVED
            approval.status = ApprovalStatus.APPROVED
            logger.info("审批单已通过: id=%s approver=%s", approval_id, approver)

            try:
                from observability.metrics import record_approval_action
                record_approval_action("approve")
            except Exception as e:
                logger.debug("操作失败，已忽略: %s", e)

        # 更新 Redis
        await self._update_approval(approval)
        return approval

    async def reject(
        self,
        approval_id: str,
        approver: str,
        reason: str = "",
    ) -> ApprovalRequest | None:
        """审批拒绝

        Args:
            approval_id: 审批单ID
            approver: 审批人
            reason: 拒绝原因

        Returns:
            更新后的 ApprovalRequest 或 None
        """
        approval = await self.get_approval(approval_id)
        if approval is None:
            return None

        if approval.status != ApprovalStatus.PENDING:
            logger.warning("审批单状态非 PENDING，无法拒绝: id=%s status=%s", approval_id, approval.status)
            return None

        approval.status = ApprovalStatus.REJECTED
        approval.approver = approver
        approval.resolved_at = time.time()

        await self._update_approval(approval)

        try:
            from observability.metrics import record_approval_action
            record_approval_action("reject")
        except Exception as e:
            logger.debug("操作失败，已忽略: %s", e)

        logger.info("审批单已拒绝: id=%s approver=%s reason=%s", approval_id, approver, reason)
        return approval

    async def check_expired(self) -> list[ApprovalRequest]:
        """检查过期审批，自动标记为 EXPIRED

        Returns:
            过期的审批单列表
        """
        redis = await self._get_redis()
        now = time.time()
        expired_list: list[ApprovalRequest] = []

        # 扫描所有审批索引
        all_ids = await redis.zrange(self.APPROVAL_INDEX_KEY, 0, -1)
        for approval_id in all_ids:
            approval = await self.get_approval(approval_id)
            if approval and approval.status == ApprovalStatus.PENDING:
                if approval.expires_at > 0 and now > approval.expires_at:
                    approval.status = ApprovalStatus.EXPIRED
                    approval.resolved_at = now
                    await self._update_approval(approval)
                    expired_list.append(approval)
                    logger.info("审批单已过期: id=%s tool=%s", approval_id, approval.tool_name)

        return expired_list

    async def get_pending_approvals(
        self,
        approver_role: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
    ) -> list[ApprovalRequest]:
        """查询待审批列表

        Args:
            approver_role: 按审批人角色过滤
            user_id: 按发起用户过滤
            limit: 返回数量上限

        Returns:
            待审批列表
        """
        redis = await self._get_redis()
        pending_list: list[ApprovalRequest] = []

        # 按角色查询
        if approver_role:
            role_index_key = f"approval_role:{approver_role}"
            approval_ids = await redis.zrevrange(role_index_key, 0, limit - 1)
        elif user_id:
            user_index_key = f"{self.APPROVAL_USER_INDEX_PREFIX}{user_id}"
            approval_ids = await redis.zrevrange(user_index_key, 0, limit - 1)
        else:
            approval_ids = await redis.zrevrange(self.APPROVAL_INDEX_KEY, 0, limit - 1)

        for approval_id in approval_ids:
            approval = await self.get_approval(approval_id)
            if approval and approval.status == ApprovalStatus.PENDING:
                pending_list.append(approval)

        return pending_list

    async def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        """获取审批单详情

        Args:
            approval_id: 审批单ID

        Returns:
            ApprovalRequest 或 None
        """
        redis = await self._get_redis()
        key = f"{self.APPROVAL_KEY_PREFIX}{approval_id}"
        data = await redis.get(key)

        if data is None:
            return None

        try:
            return ApprovalRequest.from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("解析审批单数据失败: id=%s error=%s", approval_id, e)
            return None

    async def mark_executed(self, approval_id: str) -> ApprovalRequest | None:
        """标记审批单为已执行

        审批通过后，工具执行完毕时调用此方法。

        Args:
            approval_id: 审批单ID

        Returns:
            更新后的 ApprovalRequest 或 None
        """
        approval = await self.get_approval(approval_id)
        if approval is None:
            return None

        if approval.status != ApprovalStatus.APPROVED:
            logger.warning("审批单状态非 APPROVED，无法标记执行: id=%s status=%s", approval_id, approval.status)
            return None

        approval.status = ApprovalStatus.EXECUTED
        await self._update_approval(approval)

        logger.info("审批单已执行: id=%s tool=%s", approval_id, approval.tool_name)
        return approval

    async def cancel(self, approval_id: str) -> ApprovalRequest | None:
        """取消审批单

        Args:
            approval_id: 审批单ID

        Returns:
            更新后的 ApprovalRequest 或 None
        """
        approval = await self.get_approval(approval_id)
        if approval is None:
            return None

        if approval.status != ApprovalStatus.PENDING:
            logger.warning("审批单状态非 PENDING，无法取消: id=%s status=%s", approval_id, approval.status)
            return None

        approval.status = ApprovalStatus.CANCELLED
        approval.resolved_at = time.time()
        await self._update_approval(approval)

        logger.info("审批单已取消: id=%s", approval_id)
        return approval

    async def _update_approval(self, approval: ApprovalRequest) -> None:
        """更新审批单到 Redis

        Args:
            approval: 审批单对象
        """
        redis = await self._get_redis()
        key = f"{self.APPROVAL_KEY_PREFIX}{approval.approval_id}"

        # 计算剩余 TTL
        ttl = int(approval.expires_at - time.time()) if approval.expires_at > 0 else 86400
        if ttl < 60:
            ttl = 3600  # 最少保留1小时

        await redis.setex(key, ttl, json.dumps(approval.to_dict(), ensure_ascii=False))


# 全局审批流管理器单例
_approval_flow_manager: ApprovalFlowManager | None = None


def get_approval_flow_manager() -> ApprovalFlowManager:
    """获取全局审批流管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _approval_flow_manager
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_approval_flow_manager() is not None:
            return ctx.get_approval_flow_manager()
    except Exception as e:
        logger.debug("操作失败，已忽略: %s", e)
    if _approval_flow_manager is None:
        _approval_flow_manager = ApprovalFlowManager()
    return _approval_flow_manager
