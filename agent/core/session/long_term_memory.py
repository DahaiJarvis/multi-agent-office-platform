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

from agent.core.infrastructure.config import get_settings

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
                    tenant_id VARCHAR(64) DEFAULT '',
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
                CREATE INDEX IF NOT EXISTS idx_sessions_tenant_id ON sessions(tenant_id)
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_sessions_archived_at ON sessions(archived_at)
            """))

            # 用户知识表（After-turn 知识沉淀）
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_knowledge (
                    id BIGSERIAL PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    tenant_id VARCHAR(64) DEFAULT '',
                    knowledge_type VARCHAR(32) NOT NULL,
                    content TEXT NOT NULL,
                    source_session_id VARCHAR(36) DEFAULT '',
                    ttl_days INT DEFAULT 30,
                    weight FLOAT DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP
                )
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_user_knowledge_user_id ON user_knowledge(user_id)
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_user_knowledge_type ON user_knowledge(knowledge_type)
            """))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_user_knowledge_tenant ON user_knowledge(tenant_id)
            """))

            # pgvector 向量检索升级（spec 02 第 5.1 节）
            # 1. 启用 pgvector 扩展（需 superuser 权限，失败时跳过不影响基础功能）
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                logger.info("pgvector 扩展已启用")
            except Exception as e:
                logger.warning("pgvector 扩展启用失败，向量检索将降级到 ILIKE: %s", e)

            # 2. 为 user_knowledge 增加 embedding 列（1024 维，对应 text-embedding-v3）
            try:
                await conn.execute(text(
                    "ALTER TABLE user_knowledge ADD COLUMN IF NOT EXISTS embedding vector(1024)"
                ))
            except Exception as e:
                logger.warning("embedding 列添加失败（可能已存在或 pgvector 不可用）: %s", e)

            # 3. 创建 HNSW 索引（余弦距离）
            try:
                await conn.execute(text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_knowledge_embedding
                      ON user_knowledge USING hnsw (embedding vector_cosine_ops)
                      WITH (m = 16, ef_construction = 64)
                    """
                ))
                logger.info("user_knowledge HNSW 向量索引已创建")
            except Exception as e:
                logger.warning("HNSW 索引创建失败（可能 pgvector 不可用）: %s", e)

    async def archive_session(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        messages: list[dict[str, Any]],
        context_summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str = "",
    ) -> bool:
        """归档会话到 L3

        将 L2 中即将过期的会话数据持久化到 PostgreSQL。
        多租户模式下，tenant_id 用于数据隔离。

        Args:
            session_id: 会话ID
            user_id: 用户ID
            channel: 接入渠道
            messages: 消息历史
            context_summary: 上下文摘要
            metadata: 附加元数据
            tenant_id: 租户ID（可选，多租户隔离）

        Returns:
            是否归档成功
        """
        # 如果未传入 tenant_id，尝试从上下文获取
        if not tenant_id:
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass

        pool = await self._get_pool()
        if pool is None:
            return False

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 插入或更新会话（含 tenant_id）
                await session.execute(
                    text("""
                        INSERT INTO sessions (session_id, user_id, tenant_id, channel, context_summary, metadata, archived_at)
                        VALUES (:sid, :uid, :tid, :ch, :cs, :meta, NOW())
                        ON CONFLICT (session_id) DO UPDATE SET
                            context_summary = :cs,
                            metadata = :meta,
                            updated_at = NOW(),
                            archived_at = NOW()
                    """),
                    {
                        "sid": session_id,
                        "uid": user_id,
                        "tid": tenant_id,
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

        多租户模式下，自动从上下文获取 tenant_id 进行过滤。

        Args:
            session_id: 会话ID

        Returns:
            会话数据字典，包含 session_id, user_id, tenant_id, messages, context_summary 等
        """
        pool = await self._get_pool()
        if pool is None:
            return None

        # 获取当前租户ID
        tenant_id = ""
        try:
            from security.tenant import get_current_tenant_id
            tenant_id = get_current_tenant_id() or ""
        except Exception:
            pass

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 查询会话（含租户过滤）
                if tenant_id:
                    result = await session.execute(
                        text("SELECT session_id, user_id, tenant_id, channel, context_summary, metadata FROM sessions WHERE session_id = :sid AND tenant_id = :tid"),
                        {"sid": session_id, "tid": tenant_id},
                    )
                else:
                    result = await session.execute(
                        text("SELECT session_id, user_id, tenant_id, channel, context_summary, metadata FROM sessions WHERE session_id = :sid"),
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
                    "tenant_id": row.get("tenant_id", ""),
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
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """查询用户的归档会话列表

        多租户模式下，通过 tenant_id 过滤确保租户隔离。

        Args:
            user_id: 用户ID
            limit: 返回数量上限
            offset: 偏移量
            tenant_id: 租户ID（可选，多租户过滤）

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

        pool = await self._get_pool()
        if pool is None:
            return []

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 构建查询条件（含租户过滤）
                if tenant_id:
                    result = await session.execute(
                        text("""
                            SELECT s.session_id, s.user_id, s.tenant_id, s.channel, s.context_summary,
                                   s.archived_at, s.created_at as session_created_at,
                                   COUNT(m.id) as message_count,
                                   (SELECT m2.content FROM messages m2 WHERE m2.session_id = s.session_id AND m2.role = 'user' ORDER BY m2.created_at ASC LIMIT 1) as first_message
                            FROM sessions s
                            LEFT JOIN messages m ON s.session_id = m.session_id
                            WHERE s.user_id = :uid AND s.tenant_id = :tid
                            GROUP BY s.session_id
                            ORDER BY s.archived_at DESC
                            LIMIT :limit OFFSET :offset
                        """),
                        {"uid": user_id, "tid": tenant_id, "limit": limit, "offset": offset},
                    )
                else:
                    result = await session.execute(
                        text("""
                            SELECT s.session_id, s.user_id, s.tenant_id, s.channel, s.context_summary,
                                   s.archived_at, s.created_at as session_created_at,
                                   COUNT(m.id) as message_count,
                                   (SELECT m2.content FROM messages m2 WHERE m2.session_id = s.session_id AND m2.role = 'user' ORDER BY m2.created_at ASC LIMIT 1) as first_message
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
                        "created_at": (row["session_created_at"].isoformat()
                                       if row.get("session_created_at") and hasattr(row["session_created_at"], "isoformat")
                                       else (row["archived_at"].isoformat() if row["archived_at"] else "")),
                        "updated_at": row["archived_at"].isoformat() if row["archived_at"] else "",
                        "message_count": row["message_count"],
                        "active_agents": [],
                        "first_message": row["first_message"] or "",
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

    async def store_user_knowledge(
        self,
        user_id: str,
        knowledge_type: str,
        content: str,
        source_session_id: str,
        ttl_days: int = 30,
        tenant_id: str = "",
    ) -> bool:
        """存储用户知识

        将 After-turn 提取的知识写入 user_knowledge 表。
        支持偏好、决策、事实、待办等类型。

        改造点（spec 02 第 4.2 节）：
          - 在 INSERT 语句中追加 embedding 列。
          - 写入前调用共享 Embedding 客户端生成向量。
          - Embedding 生成失败时仍写入知识（embedding 为 NULL），
            不阻断主流程，后续可通过回填脚本补齐。

        Args:
            user_id: 用户ID
            knowledge_type: 知识类型 (preference/decision/fact/todo)
            content: 知识内容
            source_session_id: 来源会话ID
            ttl_days: 保留天数，默认30天
            tenant_id: 租户ID

        Returns:
            是否存储成功
        """
        if not tenant_id:
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass

        pool = await self._get_pool()
        if pool is None:
            return False

        # 生成 Embedding 向量（失败不阻断主流程）
        embedding: list[float] = []
        try:
            from agent.core.infrastructure.embedding import get_embedding_client
            embedding_client = get_embedding_client()
            embedding = await embedding_client.get_embedding(content)
        except Exception as e:
            logger.debug("知识 Embedding 生成失败，将写入 NULL: %s", e)
            embedding = []

        try:
            async with pool() as session:
                from sqlalchemy import text
                from datetime import timedelta

                expires_at = datetime.now() + timedelta(days=ttl_days)

                # embedding 非空时写入向量，为空时写入 NULL（后续可回填）
                if embedding:
                    await session.execute(
                        text("""
                            INSERT INTO user_knowledge
                                (user_id, tenant_id, knowledge_type, content, source_session_id, ttl_days, expires_at, embedding)
                            VALUES
                                (:uid, :tid, :ktype, :content, :sid, :ttl, :exp, :emb::vector)
                        """),
                        {
                            "uid": user_id,
                            "tid": tenant_id,
                            "ktype": knowledge_type,
                            "content": content,
                            "sid": source_session_id,
                            "ttl": ttl_days,
                            "exp": expires_at,
                            "emb": str(embedding),
                        },
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO user_knowledge
                                (user_id, tenant_id, knowledge_type, content, source_session_id, ttl_days, expires_at)
                            VALUES
                                (:uid, :tid, :ktype, :content, :sid, :ttl, :exp)
                        """),
                        {
                            "uid": user_id,
                            "tid": tenant_id,
                            "ktype": knowledge_type,
                            "content": content,
                            "sid": source_session_id,
                            "ttl": ttl_days,
                            "exp": expires_at,
                        },
                    )
                await session.commit()

            logger.info(
                "用户知识已存储: user=%s type=%s content=%.50s emb=%s",
                user_id, knowledge_type, content, "已生成" if embedding else "NULL",
            )
            return True

        except Exception as e:
            logger.error("存储用户知识失败: user=%s error=%s", user_id, e)
            return False

    async def query_user_knowledge(
        self,
        user_id: str,
        query: str = "",
        knowledge_type: str | None = None,
        limit: int = 10,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """查询用户知识

        支持按类型过滤和关键词搜索。
        过期知识自动降权（weight 降为 0.5）。

        Args:
            user_id: 用户ID
            query: 搜索关键词（可选）
            knowledge_type: 知识类型过滤（可选）
            limit: 返回数量上限
            tenant_id: 租户ID

        Returns:
            知识列表
        """
        if not tenant_id:
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass

        pool = await self._get_pool()
        if pool is None:
            return []

        try:
            async with pool() as session:
                from sqlalchemy import text

                conditions = ["user_id = :uid"]
                params: dict[str, Any] = {"uid": user_id, "limit": limit}

                if tenant_id:
                    conditions.append("tenant_id = :tid")
                    params["tid"] = tenant_id

                if knowledge_type:
                    conditions.append("knowledge_type = :ktype")
                    params["ktype"] = knowledge_type

                if query:
                    conditions.append("content ILIKE :q")
                    params["q"] = f"%{query}%"

                where_clause = " AND ".join(conditions)

                result = await session.execute(
                    text(f"""
                        SELECT id, user_id, knowledge_type, content,
                               source_session_id, weight, created_at, expires_at
                        FROM user_knowledge
                        WHERE {where_clause}
                        ORDER BY weight DESC, created_at DESC
                        LIMIT :limit
                    """),
                    params,
                )

                knowledge_list = []
                for row in result.mappings().all():
                    knowledge_list.append({
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "knowledge_type": row["knowledge_type"],
                        "content": row["content"],
                        "source_session_id": row["source_session_id"],
                        "weight": row["weight"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else "",
                    })

                return knowledge_list

        except Exception as e:
            logger.error("查询用户知识失败: user=%s error=%s", user_id, e)
            return []

    # ====================================================================
    # 向量语义检索（spec 02 第 4 节）
    # ====================================================================

    # pgvector 可用性缓存（进程内，带 TTL 避免每次查询都探测）
    _pgvector_available_cache: bool | None = None
    _pgvector_cache_time: float = 0
    _pgvector_cache_ttl: float = 300.0  # 5 分钟缓存

    async def _pgvector_available(self) -> bool:
        """探测 pgvector 扩展与 embedding 列是否可用

        检测逻辑（spec 02 第 3.4 节）：
          1. 查询 pg_extension 确认 vector 扩展已安装
          2. 查询 information_schema 确认 embedding 列存在

        结果进程内缓存（TTL 5 分钟），避免每次查询都探测。
        降级时短 TTL 缓存（避免雪崩探测）。

        Returns:
            True 表示 pgvector 可用，False 表示需降级到 ILIKE
        """
        import time

        # 检查缓存
        now = time.time()
        if self._pgvector_available_cache is not None:
            if now - self._pgvector_cache_time < self._pgvector_cache_ttl:
                return self._pgvector_available_cache

        pool = await self._get_pool()
        if pool is None:
            self._pgvector_available_cache = False
            self._pgvector_cache_time = now
            return False

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 1. 检查 vector 扩展
                result = await session.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"),
                )
                ext_row = result.first()
                if ext_row is None:
                    self._pgvector_available_cache = False
                    self._pgvector_cache_time = now
                    logger.info("pgvector 扩展未安装，向量检索降级到 ILIKE")
                    return False

                # 2. 检查 embedding 列是否存在
                result = await session.execute(
                    text("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'user_knowledge' AND column_name = 'embedding'
                    """),
                )
                col_row = result.first()
                if col_row is None:
                    self._pgvector_available_cache = False
                    self._pgvector_cache_time = now
                    logger.info("user_knowledge.embedding 列不存在，向量检索降级到 ILIKE")
                    return False

                self._pgvector_available_cache = True
                self._pgvector_cache_time = now
                return True

        except Exception as e:
            # 降级时使用短 TTL 缓存（60 秒），避免雪崩探测
            self._pgvector_cache_ttl = 60.0
            self._pgvector_available_cache = False
            self._pgvector_cache_time = now
            logger.warning("pgvector 可用性探测失败，降级到 ILIKE: %s", e)
            return False

    async def _vector_recall(
        self,
        user_id: str,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """向量召回：使用 HNSW 索引做余弦近似最近邻搜索

        SQL（spec 02 第 5.3 节）：
          - 使用 <=> 操作符计算余弦距离
          - 1 - (embedding <=> :qvec) 转换为余弦相似度
          - 强制 user_id + tenant_id 过滤（多租户隔离）
          - 排除过期知识
          - 仅检索 embedding 非空的行

        Args:
            user_id: 用户ID
            tenant_id: 租户ID
            query_embedding: 查询向量（1024 维）
            top_k: 返回数量上限

        Returns:
            知识列表，含 vector_score 字段
        """
        pool = await self._get_pool()
        if pool is None:
            return []

        try:
            async with pool() as session:
                from sqlalchemy import text

                result = await session.execute(
                    text("""
                        SELECT
                            id,
                            knowledge_type,
                            content,
                            source_session_id,
                            weight,
                            created_at,
                            expires_at,
                            1 - (embedding <=> :qvec::vector) AS vector_score
                        FROM user_knowledge
                        WHERE user_id = :uid
                          AND tenant_id = :tid
                          AND (expires_at IS NULL OR expires_at > NOW())
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> :qvec::vector
                        LIMIT :k
                    """),
                    {
                        "uid": user_id,
                        "tid": tenant_id,
                        "qvec": str(query_embedding),
                        "k": top_k,
                    },
                )

                rows = []
                for row in result.mappings().all():
                    rows.append({
                        "id": row["id"],
                        "user_id": user_id,
                        "knowledge_type": row["knowledge_type"],
                        "content": row["content"],
                        "source_session_id": row["source_session_id"],
                        "weight": row["weight"],
                        "vector_score": float(row["vector_score"]),
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else "",
                    })
                return rows

        except Exception as e:
            logger.warning("向量召回失败，降级处理: %s", e)
            return []

    async def _keyword_recall(
        self,
        user_id: str,
        tenant_id: str,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """关键词召回：复用既有 ILIKE 逻辑

        SQL（spec 02 第 5.3 节关键词召回部分）：
          - 保持与 query_user_knowledge 相同的 ILIKE 匹配
          - 强制 user_id + tenant_id 过滤
          - 排除过期知识
          - 按 weight DESC, created_at DESC 排序

        Args:
            user_id: 用户ID
            tenant_id: 租户ID
            query: 查询关键词
            top_k: 返回数量上限

        Returns:
            知识列表，含 keyword_score 字段（基于排序位置的占位分）
        """
        pool = await self._get_pool()
        if pool is None:
            return []

        try:
            async with pool() as session:
                from sqlalchemy import text

                result = await session.execute(
                    text("""
                        SELECT
                            id,
                            knowledge_type,
                            content,
                            source_session_id,
                            weight,
                            created_at,
                            expires_at
                        FROM user_knowledge
                        WHERE user_id = :uid
                          AND tenant_id = :tid
                          AND (expires_at IS NULL OR expires_at > NOW())
                          AND content ILIKE :q
                        ORDER BY weight DESC, created_at DESC
                        LIMIT :k
                    """),
                    {
                        "uid": user_id,
                        "tid": tenant_id,
                        "q": f"%{query}%",
                        "k": top_k,
                    },
                )

                rows = []
                for idx, row in enumerate(result.mappings().all()):
                    # 关键词召回使用基于排序位置的占位分（1.0 递减）
                    keyword_score = max(0.0, 1.0 - idx * 0.1)
                    rows.append({
                        "id": row["id"],
                        "user_id": user_id,
                        "knowledge_type": row["knowledge_type"],
                        "content": row["content"],
                        "source_session_id": row["source_session_id"],
                        "weight": row["weight"],
                        "keyword_score": keyword_score,
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else "",
                    })
                return rows

        except Exception as e:
            logger.warning("关键词召回失败: %s", e)
            return []

    @staticmethod
    def _rrf_fuse(
        vector_results: list[dict[str, Any]],
        keyword_results: list[dict[str, Any]],
        top_k: int,
        score_threshold: float,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
        rrf_k: int = 60,
    ) -> list[dict[str, Any]]:
        """RRF（Reciprocal Rank Fusion）融合两路召回结果

        算法（spec 02 第 4.3 节）：
          - 向量结果应用 score_threshold 过滤（vector_score >= threshold）
          - 按 id 去重，合并两路结果
          - RRF 得分 = w_v * 1/(k + rank_v) + w_k * 1/(k + rank_k)
          - 按融合得分降序排序，截断 top_k

        Args:
            vector_results: 向量召回结果列表
            keyword_results: 关键词召回结果列表
            top_k: 最终返回数量上限
            score_threshold: 向量相似度阈值（仅过滤向量结果）
            vector_weight: 向量权重（默认 0.7）
            keyword_weight: 关键词权重（默认 0.3）
            rrf_k: RRF 平滑常数（默认 60）

        Returns:
            融合后的知识列表，按融合得分降序排列，含 score 和 matched_by 字段
        """
        # 1. 向量结果应用阈值过滤
        filtered_vector = [
            r for r in vector_results
            if r.get("vector_score", 0.0) >= score_threshold
        ]

        # 2. 构建索引：id -> 融合信息
        fused: dict[int, dict[str, Any]] = {}

        # 向量结果按 vector_score 降序排名
        for rank, item in enumerate(filtered_vector):
            item_id = item["id"]
            rrf_score = vector_weight * (1.0 / (rrf_k + rank + 1))
            fused[item_id] = {
                **item,
                "score": rrf_score,
                "matched_by": "vector",
                "_vector_rank": rank,
                "_keyword_rank": None,
            }

        # 关键词结果按 keyword_score 降序排名
        for rank, item in enumerate(keyword_results):
            item_id = item["id"]
            rrf_score = keyword_weight * (1.0 / (rrf_k + rank + 1))
            if item_id in fused:
                # 已在向量结果中 -> 标记为 hybrid
                fused[item_id]["score"] += rrf_score
                fused[item_id]["matched_by"] = "hybrid"
                fused[item_id]["_keyword_rank"] = rank
            else:
                fused[item_id] = {
                    **item,
                    "score": rrf_score,
                    "matched_by": "keyword",
                    "_vector_rank": None,
                    "_keyword_rank": rank,
                }

        # 3. 按融合得分降序排序
        sorted_results = sorted(fused.values(), key=lambda x: x["score"], reverse=True)

        # 4. 截断 top_k，清理内部字段
        final_results = []
        for item in sorted_results[:top_k]:
            # 清理内部字段：_ 开头的排名字段 + 召回原始分数字段
            clean = {
                k: v for k, v in item.items()
                if not k.startswith("_") and k not in ("vector_score", "keyword_score")
            }
            final_results.append(clean)

        return final_results

    async def semantic_search(
        self,
        user_id: str,
        query: str,
        top_k: int = 10,
        score_threshold: float = 0.75,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """语义检索用户知识，使用 pgvector 向量近似搜索，融合关键词匹配结果

        pgvector 不可用时降级到 ILIKE（spec 02 第 4.1 节）。

        流程（spec 02 第 6.2 节）：
          1. 解析 tenant_id（保留既有上下文获取逻辑）
          2. query 为空 -> 直接走 weight/created_at 召回
          3. 探测 pgvector 可用性（缓存结果）
          4. 生成 query embedding（复用共享客户端）
          5. 并行执行向量召回 + 关键词召回
          6. RRF 融合 + 阈值过滤
          7. 截断 top_k 返回（不含 embedding 字段）

        Args:
            user_id: 用户ID，必填，用于多租户隔离
            query: 查询文本，为空时按 weight/created_at 召回
            top_k: 返回结果数量上限
            score_threshold: 向量相似度阈值（余弦相似度，0~1）
            tenant_id: 租户ID，未传时从上下文获取

        Returns:
            知识列表，按融合后得分降序排列。单条结构:
            {
                "id": int,
                "user_id": str,
                "knowledge_type": str,
                "content": str,
                "source_session_id": str,
                "weight": float,
                "score": float,           # 融合后得分(0~1)
                "matched_by": str,        # "vector" | "keyword" | "hybrid"
                "created_at": str,        # ISO8601
                "expires_at": str,        # ISO8601
            }
            不包含 embedding 向量字段。
        """
        # 解析 tenant_id（保留既有上下文获取逻辑）
        if not tenant_id:
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass

        pool = await self._get_pool()
        if pool is None:
            return []

        # query 为空 -> 直接走 weight/created_at 召回（复用既有逻辑）
        if not query or not query.strip():
            return await self._keyword_recall_fallback(user_id, tenant_id, top_k)

        # 探测 pgvector 可用性
        pgvector_ok = await self._pgvector_available()
        if not pgvector_ok:
            # pgvector 不可用 -> 仅关键词召回
            keyword_results = await self._keyword_recall(user_id, tenant_id, query, top_k)
            return self._format_keyword_only(keyword_results, top_k)

        # 生成 query embedding（复用共享客户端）
        query_embedding: list[float] = []
        try:
            from agent.core.infrastructure.embedding import get_embedding_client
            embedding_client = get_embedding_client()
            query_embedding = await embedding_client.get_embedding(query)
        except Exception as e:
            logger.debug("查询 Embedding 生成失败: %s", e)
            query_embedding = []

        if not query_embedding:
            # Embedding 生成失败 -> 仅关键词召回
            keyword_results = await self._keyword_recall(user_id, tenant_id, query, top_k)
            return self._format_keyword_only(keyword_results, top_k)

        # 并行执行向量召回 + 关键词召回
        import asyncio

        vector_task = self._vector_recall(user_id, tenant_id, query_embedding, top_k)
        keyword_task = self._keyword_recall(user_id, tenant_id, query, top_k)
        vector_results, keyword_results = await asyncio.gather(
            vector_task, keyword_task, return_exceptions=True,
        )

        # 异常处理：某一路召回失败时使用空列表
        if isinstance(vector_results, Exception):
            logger.warning("向量召回异常: %s", vector_results)
            vector_results = []
        if isinstance(keyword_results, Exception):
            logger.warning("关键词召回异常: %s", keyword_results)
            keyword_results = []

        # 向量召回为空且关键词召回为空 -> 返回空
        if not vector_results and not keyword_results:
            return []

        # 向量召回为空 -> 仅关键词
        if not vector_results:
            return self._format_keyword_only(keyword_results, top_k)

        # RRF 融合
        return self._rrf_fuse(
            vector_results,
            keyword_results,
            top_k=top_k,
            score_threshold=score_threshold,
        )

    async def _keyword_recall_fallback(
        self,
        user_id: str,
        tenant_id: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """query 为空时的降级召回：按 weight/created_at 排序

        复用既有 query_user_knowledge 的无 query 逻辑（spec 02 第 6.2 节）。
        """
        pool = await self._get_pool()
        if pool is None:
            return []

        try:
            async with pool() as session:
                from sqlalchemy import text

                if tenant_id:
                    result = await session.execute(
                        text("""
                            SELECT id, user_id, knowledge_type, content,
                                   source_session_id, weight, created_at, expires_at
                            FROM user_knowledge
                            WHERE user_id = :uid AND tenant_id = :tid
                              AND (expires_at IS NULL OR expires_at > NOW())
                            ORDER BY weight DESC, created_at DESC
                            LIMIT :k
                        """),
                        {"uid": user_id, "tid": tenant_id, "k": top_k},
                    )
                else:
                    result = await session.execute(
                        text("""
                            SELECT id, user_id, knowledge_type, content,
                                   source_session_id, weight, created_at, expires_at
                            FROM user_knowledge
                            WHERE user_id = :uid
                              AND (expires_at IS NULL OR expires_at > NOW())
                            ORDER BY weight DESC, created_at DESC
                            LIMIT :k
                        """),
                        {"uid": user_id, "k": top_k},
                    )

                rows = []
                for row in result.mappings().all():
                    rows.append({
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "knowledge_type": row["knowledge_type"],
                        "content": row["content"],
                        "source_session_id": row["source_session_id"],
                        "weight": row["weight"],
                        "score": float(row["weight"]),
                        "matched_by": "keyword",
                        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else "",
                    })
                return rows

        except Exception as e:
            logger.error("降级召回失败: user=%s error=%s", user_id, e)
            return []

    @staticmethod
    def _format_keyword_only(
        keyword_results: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """格式化仅关键词召回的结果（降级场景）

        将 keyword_score 转换为 score 字段，matched_by 统一为 "keyword"。
        """
        results = []
        for item in keyword_results[:top_k]:
            result = {
                "id": item["id"],
                "user_id": item.get("user_id", ""),
                "knowledge_type": item["knowledge_type"],
                "content": item["content"],
                "source_session_id": item["source_session_id"],
                "weight": item["weight"],
                "score": item.get("keyword_score", 0.0),
                "matched_by": "keyword",
                "created_at": item.get("created_at", ""),
                "expires_at": item.get("expires_at", ""),
            }
            results.append(result)
        return results

    async def expire_user_knowledge(self, user_id: str) -> int:
        """过期知识降权/清除

        将已过期的知识 weight 降为 0.5（降权），
        超过 TTL 2 倍时间的知识直接删除。

        Args:
            user_id: 用户ID

        Returns:
            处理的知识条数
        """
        pool = await self._get_pool()
        if pool is None:
            return 0

        try:
            async with pool() as session:
                from sqlalchemy import text

                # 过期知识降权
                await session.execute(
                    text("""
                        UPDATE user_knowledge SET weight = 0.5
                        WHERE user_id = :uid
                          AND expires_at < NOW()
                          AND weight > 0.5
                    """),
                    {"uid": user_id},
                )

                # 超过 TTL 2 倍时间删除
                result = await session.execute(
                    text("""
                        DELETE FROM user_knowledge
                        WHERE user_id = :uid
                          AND expires_at < NOW() - INTERVAL '1 day' * ttl_days * 2
                    """),
                    {"uid": user_id},
                )

                await session.commit()
                deleted = result.rowcount if result else 0
                if deleted > 0:
                    logger.info("过期知识清理: user=%s deleted=%d", user_id, deleted)
                return deleted

        except Exception as e:
            logger.error("过期知识处理失败: user=%s error=%s", user_id, e)
            return 0

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
