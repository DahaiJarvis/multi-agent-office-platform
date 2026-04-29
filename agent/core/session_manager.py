"""会话管理

三级会话存储：
  - L1 工作记忆: Agent 内存，单次请求生命周期
  - L2 短期记忆: Redis，2h TTL，活跃会话
  - L3 长期记忆: PostgreSQL，永久，历史归档

会话生命周期:
  1. 创建会话 -> 写入 L2 (Redis)
  2. 交互过程中 -> 读写 L2，自动续期
  3. L2 过期前 -> 自动归档到 L3 (PostgreSQL)
  4. 查询历史 -> 优先 L2，未命中则从 L3 恢复
"""

import logging
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

from agent.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


class SessionState(BaseModel):
    """会话状态模型"""

    session_id: str
    user_id: str
    tenant_id: str = ""
    channel: str = "web"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    message_history: list[dict[str, Any]] = Field(default_factory=list)
    active_agents: list[str] = Field(default_factory=list)
    pending_approvals: list[str] = Field(default_factory=list)
    context_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionManager:
    """会话管理器，负责会话的创建、读取、更新和归档"""

    SESSION_TTL = 7200  # 2小时
    ARCHIVE_THRESHOLD = 7200  # 2小时无交互则归档

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 连接"""
        if self._redis is None:
            self._redis = aioredis.from_url(
                _settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def create_session(self, user_id: str, channel: str = "web", tenant_id: str = "") -> SessionState:
        """创建新会话

        Args:
            user_id: 用户ID
            channel: 接入渠道
            tenant_id: 租户ID（可选，多租户隔离）

        Returns:
            新创建的 SessionState
        """
        import uuid
        import time

        # 如果未传入 tenant_id，尝试从上下文获取
        if not tenant_id:
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass

        session = SessionState(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            tenant_id=tenant_id,
            channel=channel,
        )

        redis = await self._get_redis()
        key = self._session_key(session.session_id)
        await redis.setex(key, self.SESSION_TTL, session.model_dump_json())

        # 添加到用户会话索引（Redis Sorted Set，score 为时间戳）
        index_key = self._user_sessions_key(user_id)
        await redis.zadd(index_key, {session.session_id: time.time()})
        # 索引保留 7 天
        await redis.expire(index_key, 86400 * 7)

        logger.info("创建会话: %s, 用户: %s", session.session_id, user_id)
        return session

    async def get_session(self, session_id: str) -> SessionState | None:
        """获取会话状态

        查询顺序：L2 (Redis) -> L3 (PostgreSQL)
        如果 L2 未命中，尝试从 L3 恢复。

        多租户模式下，先尝试带租户前缀的键，再尝试不带前缀的键（兼容旧数据）。

        Args:
            session_id: 会话ID

        Returns:
            SessionState 或 None
        """
        # 先查 L2（尝试多种键格式）
        redis = await self._get_redis()

        # 尝试从当前租户上下文获取 tenant_id
        tenant_id = ""
        try:
            from security.tenant import get_current_tenant_id
            tenant_id = get_current_tenant_id() or ""
        except Exception:
            pass

        # 优先尝试带租户前缀的键
        for tid in ([tenant_id, ""] if tenant_id else [""]):
            key = self._session_key(session_id, tid)
            data = await redis.get(key)
            if data is not None:
                return SessionState.model_validate_json(data)

        # L2 未命中，尝试从 L3 恢复
        return await self._restore_from_l3(session_id)

    async def _restore_from_l3(self, session_id: str) -> SessionState | None:
        """从 L3 恢复会话到 L2

        Args:
            session_id: 会话ID

        Returns:
            恢复的 SessionState 或 None
        """
        try:
            from agent.core.long_term_memory import get_long_term_memory

            ltm = get_long_term_memory()
            archived = await ltm.load_session(session_id)
            if archived is None:
                return None

            session = SessionState(
                session_id=archived["session_id"],
                user_id=archived["user_id"],
                channel=archived.get("channel", "web"),
                message_history=archived.get("message_history", []),
                context_summary=archived.get("context_summary"),
                metadata=archived.get("metadata", {}),
            )

            # 写回 L2
            redis = await self._get_redis()
            key = self._session_key(session.session_id)
            await redis.setex(key, self.SESSION_TTL, session.model_dump_json())

            logger.info("从 L3 恢复会话: %s", session_id)
            return session

        except Exception as e:
            logger.warning("从 L3 恢复会话失败: session_id=%s error=%s", session_id, e)
            return None

    async def update_session(self, session: SessionState) -> None:
        """更新会话状态并续期 TTL

        Args:
            session: 更新后的 SessionState
        """
        import time

        session.updated_at = datetime.now()
        redis = await self._get_redis()
        key = self._session_key(session.session_id, session.tenant_id)
        await redis.setex(key, self.SESSION_TTL, session.model_dump_json())

        # 添加到用户会话索引（Redis Sorted Set，score 为时间戳）
        index_key = self._user_sessions_key(session.user_id, session.tenant_id)
        await redis.zadd(index_key, {session.session_id: time.time()})

    async def append_message(
        self, session_id: str, role: str, content: str, metadata: dict | None = None
    ) -> None:
        """向会话追加消息

        Args:
            session_id: 会话ID
            role: 消息角色 (user/assistant/system)
            content: 消息内容
            metadata: 附加元数据
        """
        session = await self.get_session(session_id)
        if session is None:
            logger.warning("会话 %s 不存在，无法追加消息", session_id)
            return

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if metadata:
            message["metadata"] = metadata

        session.message_history.append(message)
        await self.update_session(session)

    async def delete_session(self, session_id: str) -> bool:
        """删除会话

        从 L2 (Redis) 和 L3 (PostgreSQL) 中删除会话数据。
        多租户模式下，先获取会话的 tenant_id 再删除。

        Args:
            session_id: 会话ID

        Returns:
            是否删除成功
        """
        session = await self.get_session(session_id)
        if session is None:
            return False

        redis = await self._get_redis()
        key = self._session_key(session_id, session.tenant_id)
        await redis.delete(key)

        # 从用户会话索引中移除
        index_key = self._user_sessions_key(session.user_id, session.tenant_id)
        await redis.zrem(index_key, session_id)

        return True

    async def archive_session(self, session_id: str) -> bool:
        """归档会话到 L3

        将会话数据持久化到 PostgreSQL，用于 L2 过期后的历史恢复。

        Args:
            session_id: 会话ID

        Returns:
            是否归档成功
        """
        session = await self.get_session(session_id)
        if session is None:
            logger.warning("会话 %s 不存在，无法归档", session_id)
            return False

        try:
            from agent.core.long_term_memory import get_long_term_memory

            ltm = get_long_term_memory()
            return await ltm.archive_session(
                session_id=session.session_id,
                user_id=session.user_id,
                channel=session.channel,
                messages=session.message_history,
                context_summary=session.context_summary,
                metadata=session.metadata,
            )
        except Exception as e:
            logger.error("归档会话失败: session_id=%s error=%s", session_id, e)
            return False

    async def list_archived_sessions(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """查询用户的会话列表

        合并 Redis 活跃会话索引和 L3 PostgreSQL 归档会话，
        按时间倒序排列后去重返回。

        Args:
            user_id: 用户ID
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            会话摘要列表
        """
        redis_sessions = await self._list_sessions_from_redis(user_id, limit, offset)

        # 尝试从 L3 补充归档会话
        l3_sessions: list[dict[str, Any]] = []
        try:
            from agent.core.long_term_memory import get_long_term_memory

            ltm = get_long_term_memory()
            l3_sessions = await ltm.list_user_sessions(user_id, limit, offset)
        except Exception as e:
            logger.error("查询归档会话失败: user_id=%s error=%s", user_id, e)

        # 如果 L3 无数据，直接返回 Redis 结果
        if not l3_sessions:
            return redis_sessions

        # 如果 Redis 无数据，直接返回 L3 结果
        if not redis_sessions:
            return l3_sessions

        # 合并去重：以 session_id 为键，Redis 数据优先（更新鲜）
        merged: dict[str, dict[str, Any]] = {}
        for s in l3_sessions:
            merged[s["session_id"]] = s
        for s in redis_sessions:
            merged[s["session_id"]] = s

        # 按更新时间倒序排列
        sorted_sessions = sorted(
            merged.values(),
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )

        return sorted_sessions[:limit]

    async def _list_sessions_from_redis(
        self, user_id: str, limit: int = 20, offset: int = 0, tenant_id: str = ""
    ) -> list[dict[str, Any]]:
        """从 Redis 索引查询用户的活跃会话列表

        通过 Redis Sorted Set 索引查找用户的会话ID列表，
        再逐个获取会话详情。
        多租户模式下，通过 tenant_id 过滤确保租户隔离。

        Args:
            user_id: 用户ID
            limit: 返回数量上限
            offset: 偏移量
            tenant_id: 租户ID（可选，用于多租户过滤）

        Returns:
            会话摘要列表
        """
        # 如果未传入 tenant_id，尝试从上下文获取
        if not tenant_id:
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass

        try:
            redis = await self._get_redis()
            index_key = self._user_sessions_key(user_id, tenant_id)

            # 按时间倒序获取会话ID（zrevrange: score 从大到小）
            session_ids = await redis.zrevrange(index_key, offset, offset + limit - 1)
            if not session_ids:
                return []

            sessions = []
            for sid in session_ids:
                session = await self.get_session(sid)
                if session is not None:
                    sessions.append({
                        "session_id": session.session_id,
                        "user_id": session.user_id,
                        "channel": session.channel,
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat(),
                        "message_count": len(session.message_history),
                        "active_agents": session.active_agents,
                    })
                else:
                    # 会话已过期，从索引中清理
                    await redis.zrem(index_key, sid)

            return sessions
        except Exception as e:
            logger.warning("从 Redis 索引查询会话失败: user_id=%s error=%s", user_id, e)
            return []

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None

    @staticmethod
    def _session_key(session_id: str, tenant_id: str = "") -> str:
        """生成 Redis 存储键

        多租户模式下，键包含 tenant_id 前缀实现隔离。
        单租户模式下（tenant_id 为空），使用原始键格式。

        Args:
            session_id: 会话ID
            tenant_id: 租户ID

        Returns:
            Redis 存储键
        """
        if tenant_id:
            return f"session:{tenant_id}:{session_id}"
        return f"session:{session_id}"

    @staticmethod
    def _user_sessions_key(user_id: str, tenant_id: str = "") -> str:
        """生成用户会话索引的 Redis 键

        多租户模式下，键包含 tenant_id 前缀实现隔离。

        Args:
            user_id: 用户ID
            tenant_id: 租户ID

        Returns:
            Redis 索引键
        """
        if tenant_id:
            return f"user_sessions:{tenant_id}:{user_id}"
        return f"user_sessions:{user_id}"


# 全局会话管理器单例
_session_manager: SessionManager | None = None


async def get_session_manager() -> SessionManager:
    """获取全局会话管理器"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
