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

import json
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

    async def create_session(self, user_id: str, channel: str = "web") -> SessionState:
        """创建新会话

        Args:
            user_id: 用户ID
            channel: 接入渠道

        Returns:
            新创建的 SessionState
        """
        import uuid

        session = SessionState(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            channel=channel,
        )

        redis = await self._get_redis()
        key = self._session_key(session.session_id)
        await redis.setex(key, self.SESSION_TTL, session.model_dump_json())
        logger.info("创建会话: %s, 用户: %s", session.session_id, user_id)
        return session

    async def get_session(self, session_id: str) -> SessionState | None:
        """获取会话状态

        查询顺序：L2 (Redis) -> L3 (PostgreSQL)
        如果 L2 未命中，尝试从 L3 恢复到 L2。

        Args:
            session_id: 会话ID

        Returns:
            SessionState 或 None
        """
        # 先查 L2
        redis = await self._get_redis()
        key = self._session_key(session_id)
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
        session.updated_at = datetime.now()
        redis = await self._get_redis()
        key = self._session_key(session.session_id)
        await redis.setex(key, self.SESSION_TTL, session.model_dump_json())

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

    async def delete_session(self, session_id: str) -> None:
        """删除会话"""
        redis = await self._get_redis()
        key = self._session_key(session_id)
        await redis.delete(key)
        logger.info("删除会话: %s", session_id)

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
        """查询用户的归档会话列表

        Args:
            user_id: 用户ID
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            会话摘要列表
        """
        try:
            from agent.core.long_term_memory import get_long_term_memory

            ltm = get_long_term_memory()
            return await ltm.list_user_sessions(user_id, limit, offset)
        except Exception as e:
            logger.error("查询归档会话失败: user_id=%s error=%s", user_id, e)
            return []

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None

    @staticmethod
    def _session_key(session_id: str) -> str:
        """生成 Redis 存储键"""
        return f"session:{session_id}"


# 全局会话管理器单例
_session_manager: SessionManager | None = None


async def get_session_manager() -> SessionManager:
    """获取全局会话管理器"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
