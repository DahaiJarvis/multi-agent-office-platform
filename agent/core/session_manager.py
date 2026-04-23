"""会话管理

三级会话存储：
  - L1 工作记忆: Agent 内存，单次请求生命周期
  - L2 短期记忆: Redis，2h TTL，活跃会话
  - L3 长期记忆: PostgreSQL，永久，历史归档
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

        Args:
            session_id: 会话ID

        Returns:
            SessionState 或 None
        """
        redis = await self._get_redis()
        key = self._session_key(session_id)
        data = await redis.get(key)

        if data is None:
            return None

        return SessionState.model_validate_json(data)

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
