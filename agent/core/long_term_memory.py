"""L3 长期记忆 - PostgreSQL 持久化

提供会话和消息的长期存储能力，作为 L2 Redis 短期记忆的补充。
当会话从 L2 过期后，通过 L3 可以恢复历史上下文。

存储层级:
  - L1 工作记忆: Agent 内存，单次请求生命周期
  - L2 短期记忆: Redis，2h TTL，活跃会话
  - L3 长期记忆: PostgreSQL，永久，历史归档

归档策略:
  - 会话超过 L2 TTL 后自动归档到 L3
  - 用户查询历史会话时从 L3 加载
  - 上下文摘要定期从 L2 同步到 L3
"""

import json
import logging
from datetime import datetime
from typing import Any

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


class LongTermMemory:
    """L3 长期记忆管理器

    使用 PostgreSQL 存储会话历史和上下文摘要，
    支持按用户/会话查询历史记录。
    """

    def __init__(self) -> None:
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        """获取数据库连接池"""
        if self._pool is None:
            try:
                from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

                settings = get_settings()
                engine = create_async_engine(
                    settings.postgres_dsn,
                    pool_size=5,
                    max_overflow=10,
                    pool_pre_ping=True,
                )
                self._pool = async_sessionmaker(engine, expire_on_commit=False)

                # 初始化表结构
                await self._init_tables(engine)

                logger.info("L3 长期记忆 PostgreSQL 连接池初始化完成")
            except Exception as e:
                logger.warning("L3 长期记忆初始化失败，L3 不可用: %s", e)
                return None

        return self._pool

    async def _init_tables(self, engine: Any) -> None:
        """初始化数据库表结构"""
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    channel VARCHAR(32) DEFAULT 'web',
                    context_summary TEXT,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    archived_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(36) NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    role VARCHAR(16) NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_sessions_archived_at ON sessions(archived_at)
            """))

    async def archive_session(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        messages: list[dict[str, Any]],
        context_summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """归档会话到 L3

        将 L2 中即将过期的会话数据持久化到 PostgreSQL。

        Args:
            session_id: 会话ID
            user_id: 用户ID
            channel: 接入渠道
            messages: 消息历史
            context_summary: 上下文摘要
            metadata: 附加元数据

        Returns:
            是否归档成功
        """
        pool = await self._get_pool()
        if pool is None:
            return False

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 插入或更新会话
                await session.execute(
                    text("""
                        INSERT INTO sessions (session_id, user_id, channel, context_summary, metadata, archived_at)
                        VALUES (:sid, :uid, :ch, :cs, :meta, NOW())
                        ON CONFLICT (session_id) DO UPDATE SET
                            context_summary = :cs,
                            metadata = :meta,
                            updated_at = NOW(),
                            archived_at = NOW()
                    """),
                    {
                        "sid": session_id,
                        "uid": user_id,
                        "ch": channel,
                        "cs": context_summary,
                        "meta": json.dumps(metadata or {}, ensure_ascii=False),
                    },
                )

                # 删除旧消息（避免重复）
                await session.execute(
                    text("DELETE FROM messages WHERE session_id = :sid"),
                    {"sid": session_id},
                )

                # 批量插入消息
                for msg in messages:
                    await session.execute(
                        text("""
                            INSERT INTO messages (session_id, role, content, metadata, created_at)
                            VALUES (:sid, :role, :content, :meta, :ts)
                        """),
                        {
                            "sid": session_id,
                            "role": msg.get("role", "user"),
                            "content": msg.get("content", ""),
                            "meta": json.dumps(msg.get("metadata", {}), ensure_ascii=False),
                            "ts": msg.get("timestamp", datetime.now().isoformat()),
                        },
                    )

                await session.commit()

            logger.info("会话归档成功: session_id=%s messages=%d", session_id, len(messages))
            return True

        except Exception as e:
            logger.error("会话归档失败: session_id=%s error=%s", session_id, e)
            return False

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        """从 L3 加载已归档的会话

        Args:
            session_id: 会话ID

        Returns:
            会话数据字典，包含 session_id, user_id, messages, context_summary 等
        """
        pool = await self._get_pool()
        if pool is None:
            return None

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 查询会话
                result = await session.execute(
                    text("SELECT session_id, user_id, channel, context_summary, metadata FROM sessions WHERE session_id = :sid"),
                    {"sid": session_id},
                )
                row = result.mappings().first()
                if row is None:
                    return None

                # 查询消息
                msg_result = await session.execute(
                    text("SELECT role, content, metadata, created_at FROM messages WHERE session_id = :sid ORDER BY created_at"),
                    {"sid": session_id},
                )
                messages = []
                for msg_row in msg_result.mappings().all():
                    messages.append({
                        "role": msg_row["role"],
                        "content": msg_row["content"],
                        "metadata": msg_row["metadata"] if isinstance(msg_row["metadata"], dict) else {},
                        "timestamp": msg_row["created_at"].isoformat() if msg_row["created_at"] else "",
                    })

                return {
                    "session_id": row["session_id"],
                    "user_id": row["user_id"],
                    "channel": row["channel"],
                    "context_summary": row["context_summary"],
                    "metadata": row["metadata"] if isinstance(row["metadata"], dict) else {},
                    "message_history": messages,
                }

        except Exception as e:
            logger.error("加载归档会话失败: session_id=%s error=%s", session_id, e)
            return None

    async def list_user_sessions(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询用户的归档会话列表

        Args:
            user_id: 用户ID
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            会话摘要列表
        """
        pool = await self._get_pool()
        if pool is None:
            return []

        try:
            async with pool() as session:
                from sqlalchemy import text

                result = await session.execute(
                    text("""
                        SELECT s.session_id, s.user_id, s.channel, s.context_summary,
                               s.archived_at, COUNT(m.id) as message_count
                        FROM sessions s
                        LEFT JOIN messages m ON s.session_id = m.session_id
                        WHERE s.user_id = :uid
                        GROUP BY s.session_id
                        ORDER BY s.archived_at DESC
                        LIMIT :limit OFFSET :offset
                    """),
                    {"uid": user_id, "limit": limit, "offset": offset},
                )

                sessions = []
                for row in result.mappings().all():
                    sessions.append({
                        "session_id": row["session_id"],
                        "user_id": row["user_id"],
                        "channel": row["channel"],
                        "context_summary": row["context_summary"],
                        "archived_at": row["archived_at"].isoformat() if row["archived_at"] else "",
                        "message_count": row["message_count"],
                    })

                return sessions

        except Exception as e:
            logger.error("查询用户归档会话失败: user_id=%s error=%s", user_id, e)
            return []

    async def update_context_summary(
        self,
        session_id: str,
        context_summary: str,
    ) -> bool:
        """更新会话的上下文摘要

        Args:
            session_id: 会话ID
            context_summary: 新的上下文摘要

        Returns:
            是否更新成功
        """
        pool = await self._get_pool()
        if pool is None:
            return False

        try:
            async with pool() as session:
                from sqlalchemy import text

                await session.execute(
                    text("""
                        UPDATE sessions SET context_summary = :cs, updated_at = NOW()
                        WHERE session_id = :sid
                    """),
                    {"cs": context_summary, "sid": session_id},
                )
                await session.commit()

            return True

        except Exception as e:
            logger.error("更新上下文摘要失败: session_id=%s error=%s", session_id, e)
            return False

    async def close(self) -> None:
        """关闭连接池"""
        self._pool = None


# 全局 L3 记忆管理器单例
_ltm: LongTermMemory | None = None


def get_long_term_memory() -> LongTermMemory:
    """获取全局 L3 长期记忆管理器"""
    global _ltm
    if _ltm is None:
        _ltm = LongTermMemory()
    return _ltm
