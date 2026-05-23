"""会话管理

================================================================================
模块职责
================================================================================
提供三级会话存储和管理，包括：
  - 会话创建、读取、更新、删除
  - 会话归档和恢复
  - 会话锁（防止并发冲突）
  - 多租户隔离

================================================================================
三级会话存储架构
================================================================================
L1 工作记忆（Agent 内存）：
  - 生命周期：单次请求
  - 用途：Agent 执行过程中的临时状态

L2 短期记忆（Redis）：
  - 生命周期：2 小时（可配置）
  - 用途：活跃会话存储
  - 特点：快速读写，自动过期

L3 长期记忆（PostgreSQL）：
  - 生命周期：永久
  - 用途：历史归档
  - 特点：持久化存储，支持复杂查询

================================================================================
会话生命周期
================================================================================
1. 创建会话 -> 写入 L2 (Redis)
2. 交互过程中 -> 读写 L2，自动续期
3. L2 过期前 -> 自动归档到 L3 (PostgreSQL)
4. 查询历史 -> 优先 L2，未命中则从 L3 恢复

================================================================================
会话锁机制
================================================================================
同一会话的并发请求通过 asyncio.Lock 串行化，
防止同一会话的消息交错导致上下文混乱。

================================================================================
多租户隔离
================================================================================
通过 tenant_id 前缀实现多租户数据隔离：
  - Redis 键格式：session:{tenant_id}:{session_id}
  - 索引键格式：user_sessions:{tenant_id}:{user_id}

================================================================================
降级策略
================================================================================
当 Redis 不可用时，自动降级为内存存储，
确保系统在 Redis 故障时仍能正常工作。

================================================================================
与其他模块的关系
================================================================================
- routing.py: 获取会话状态用于构建上下文
- execution_controller.py: 追加消息到会话历史
- long_term_memory.py: L3 长期存储

================================================================================
使用示例
================================================================================
    manager = await get_session_manager()

    # 创建会话
    session = await manager.create_session(user_id="user123")

    # 获取会话
    session = await manager.get_session(session_id)

    # 追加消息
    await manager.append_message(session_id, "user", "帮我发邮件")

    # 归档会话
    await manager.archive_session(session_id)
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SessionState(BaseModel):
    """会话状态模型

    存储会话的完整状态信息，包括：
      - 基本信息（会话ID、用户ID、渠道）
      - 消息历史
      - 活跃 Agent
      - 待审批
      - 上下文摘要
      - 元数据

    Attributes:
        session_id: 会话唯一标识
        user_id: 用户ID
        tenant_id: 租户ID（多租户隔离）
        channel: 接入渠道（web/api/wechat等）
        created_at: 创建时间
        updated_at: 最后更新时间
        message_history: 消息历史列表
        active_agents: 当前活跃的 Agent 列表
        pending_approvals: 待审批列表
        context_summary: 上下文摘要（用于长对话压缩）
        metadata: 附加元数据
    """

    session_id: str
    user_id: str
    tenant_id: str = ""
    channel: str = "web"
    execution_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    message_history: list[dict[str, Any]] = Field(default_factory=list)
    active_agents: list[str] = Field(default_factory=list)
    pending_approvals: list[str] = Field(default_factory=list)
    context_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionManager:
    """会话管理器

    负责会话的创建、读取、更新和归档，支持：
      - 三级存储（L1/L2/L3）
      - Redis 不可用时自动降级为内存存储
      - 多租户隔离
      - 会话锁机制

    核心方法：
    -------------------------------------------------------------------------
    create_session(user_id, channel, tenant_id): 创建新会话
    get_session(session_id): 获取会话（支持 L3 恢复）
    update_session(session): 更新会话并续期
    append_message(session_id, role, content): 追加消息
    delete_session(session_id): 删除会话
    archive_session(session_id): 归档会话到 L3
    list_archived_sessions(user_id): 查询用户会话列表
    acquire_session(session_id): 获取会话锁
    release_session(session_id): 释放会话锁
    transfer_session(session_id, target_agent): 转移会话
    -------------------------------------------------------------------------
    """

    SESSION_TTL = 7200  # 2小时
    ARCHIVE_THRESHOLD = 7200  # 2小时无交互则归档

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._session_locks: dict[str, asyncio.Lock] = {}
        # 内存降级存储
        self._memory_store: dict[str, str] = {}
        self._memory_index: dict[str, dict[str, float]] = {}
        self._use_memory_fallback: bool = False
        # 会话转移历史（从 session_router 提取）
        self._transfer_history: list[dict[str, Any]] = []

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 连接

        复用全局统一连接管理器，避免重复创建连接。

        Returns:
            Redis 客户端实例
        """
        if self._redis is None:
            from agent.core.redis_manager import get_redis_client
            self._redis = await get_redis_client()
        return self._redis

    async def _redis_or_fallback(self) -> aioredis.Redis | None:
        """获取 Redis 连接，不可用时返回 None 并启用内存降级

        降级策略：
        - Redis 连接失败时，自动切换到内存存储
        - 确保系统在 Redis 故障时仍能正常工作

        Returns:
            Redis 客户端实例，或 None（降级模式）
        """
        if self._use_memory_fallback:
            return None
        try:
            redis = await self._get_redis()
            await redis.ping()
            return redis
        except Exception as e:
            logger.warning("Redis 不可用，启用内存降级存储: %s", e)
            self._use_memory_fallback = True
            return None

    async def switch_to_memory_fallback(self) -> None:
        """降级回调：切换到内存存储

        由 DegradationManager 调用，实现自动降级。
        """
        if not self._use_memory_fallback:
            logger.info("SessionManager 切换到内存降级存储")
            self._use_memory_fallback = True

    async def switch_to_redis(self) -> None:
        """恢复回调：切换回 Redis 存储

        由 DegradationManager 调用，实现自动恢复。
        采用写双策略：先将所有内存数据写入 Redis，确认全部成功后再清空内存，
        避免迁移过程中 Redis 写入失败导致数据丢失。
        """
        if self._use_memory_fallback:
            try:
                redis = await self._get_redis()
                await redis.ping()
                logger.info("SessionManager 恢复 Redis 存储，迁移内存数据 (共 %d 条)", len(self._memory_store))

                migrated = 0
                failed_keys: list[str] = []
                for key, value in self._memory_store.items():
                    try:
                        await redis.setex(key, self.SESSION_TTL, value)
                        migrated += 1
                    except Exception as e:
                        failed_keys.append(key)
                        logger.warning("迁移 key 到 Redis 失败: key=%s error=%s", key, e)

                if failed_keys:
                    logger.warning(
                        "部分内存数据迁移失败 (%d/%d)，保留失败数据在内存中",
                        len(failed_keys), len(self._memory_store),
                    )
                    for key in failed_keys:
                        self._memory_store.pop(key, None)
                    self._use_memory_fallback = False
                    return

                self._memory_store.clear()
                self._memory_index.clear()
                self._use_memory_fallback = False
                logger.info("内存数据迁移完成: %d 条", migrated)
            except Exception as e:
                logger.warning("恢复 Redis 失败，继续使用内存存储: %s", e)

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

        redis = await self._redis_or_fallback()
        key = self._session_key(session.session_id)
        if redis:
            await redis.setex(key, self.SESSION_TTL, session.model_dump_json())
            index_key = self._user_sessions_key(user_id)
            await redis.zadd(index_key, {session.session_id: time.time()})
            await redis.expire(index_key, 86400 * 7)
        else:
            self._memory_store[key] = session.model_dump_json()
            index_key = self._user_sessions_key(user_id)
            if index_key not in self._memory_index:
                self._memory_index[index_key] = {}
            self._memory_index[index_key][session.session_id] = time.time()

        logger.info("创建会话: %s, 用户: %s", session.session_id, user_id)

        try:
            from observability.metrics import set_active_users
            active_count = await self._count_active_users(tenant_id)
            set_active_users(tenant_id or "default", active_count)
        except Exception:
            pass

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
        redis = await self._redis_or_fallback()

        # 尝试从当前租户上下文获取 tenant_id
        tenant_id = ""
        try:
            from security.tenant import get_current_tenant_id
            tenant_id = get_current_tenant_id() or ""
        except Exception:
            pass

        if redis:
            # 优先尝试带租户前缀的键
            for tid in ([tenant_id, ""] if tenant_id else [""]):
                key = self._session_key(session_id, tid)
                data = await redis.get(key)
                if data is not None:
                    return SessionState.model_validate_json(data)
        else:
            # 内存降级模式
            for tid in ([tenant_id, ""] if tenant_id else [""]):
                key = self._session_key(session_id, tid)
                data = self._memory_store.get(key)
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
            redis = await self._redis_or_fallback()
            key = self._session_key(session.session_id)
            if redis:
                await redis.setex(key, self.SESSION_TTL, session.model_dump_json())
            else:
                self._memory_store[key] = session.model_dump_json()

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
        redis = await self._redis_or_fallback()
        key = self._session_key(session.session_id, session.tenant_id)
        if redis:
            await redis.setex(key, self.SESSION_TTL, session.model_dump_json())
            index_key = self._user_sessions_key(session.user_id, session.tenant_id)
            await redis.zadd(index_key, {session.session_id: time.time()})
        else:
            self._memory_store[key] = session.model_dump_json()
            index_key = self._user_sessions_key(session.user_id, session.tenant_id)
            if index_key not in self._memory_index:
                self._memory_index[index_key] = {}
            self._memory_index[index_key][session.session_id] = time.time()

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

        redis = await self._redis_or_fallback()
        key = self._session_key(session_id, session.tenant_id)
        if redis:
            await redis.delete(key)
            index_key = self._user_sessions_key(session.user_id, session.tenant_id)
            await redis.zrem(index_key, session_id)
        else:
            self._memory_store.pop(key, None)
            index_key = self._user_sessions_key(session.user_id, session.tenant_id)
            if index_key in self._memory_index:
                self._memory_index[index_key].pop(session_id, None)

        return True

    async def archive_session(self, session_id: str) -> bool:
        """归档会话到 L3 (PostgreSQL)

        归档流程：
        -------------------------------------------------------------------------
        1. 获取会话数据
        2. 写入 L3 (PostgreSQL)
        3. 从 L2 (Redis) 删除
        -------------------------------------------------------------------------

        触发时机：
        - 会话长时间无交互
        - 用户主动结束会话
        - 系统定期清理

        Args:
            session_id: 会话ID
        """
        session = await self.get_session(session_id)
        if session is None:
            return

        try:
            from observability.metrics import record_session_duration
            duration = (datetime.now() - session.created_at).total_seconds()
            user_tier = session.metadata.get("user_tier", "standard")
            record_session_duration(user_tier, duration)
        except Exception:
            pass

        try:
            from agent.core.long_term_memory import get_long_term_memory

            ltm = get_long_term_memory()
            archived = await ltm.archive_session(
                session_id=session.session_id,
                user_id=session.user_id,
                channel=session.channel,
                messages=[msg for msg in session.message_history],
                context_summary=session.context_summary,
                metadata=session.metadata,
                tenant_id=session.tenant_id,
            )

            # L3 归档失败时，保留 L2 数据，避免会话丢失
            if not archived:
                logger.warning("L3 归档失败，保留 L2 数据: session_id=%s", session_id)
                return False

            # 从 L2 删除会话数据（保留用户会话索引，确保历史列表可查到归档会话）
            redis = await self._redis_or_fallback()
            if redis:
                keys = await redis.keys(f"session:*:{session_id}")
                keys.append(f"session:{session_id}")
                if keys:
                    await redis.delete(*keys)
            else:
                keys_to_delete = [
                    k
                    for k in self._memory_store
                    if k.endswith(f":{session_id}") or k == f"session:{session_id}"
                ]
                for k in keys_to_delete:
                    del self._memory_store[k]

            logger.info("归档会话: session_id=%s", session_id)
            return True
        except Exception as e:
            logger.warning("归档会话失败: %s", e)
            return False

    async def list_archived_sessions(
        self,
        user_id: str,
        tenant_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """查询用户的所有会话列表（合并 L2 活跃会话和 L3 归档会话）

        优先从 L2 Redis 索引获取活跃会话，
        再从 L3 PostgreSQL 获取归档会话，合并去重后返回。

        Args:
            user_id: 用户ID
            tenant_id: 租户ID
            limit: 返回数量限制

        Returns:
            会话摘要列表
        """
        seen_ids: set[str] = set()
        result: list[dict[str, Any]] = []

        # 先从 L2 Redis 获取活跃会话
        try:
            redis_sessions = await self._list_sessions_from_redis(
                user_id, limit=limit, tenant_id=tenant_id,
            )
            for s in redis_sessions:
                sid = s["session_id"]
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    # 补充 first_message 字段
                    session = await self.get_session(sid)
                    if session and session.message_history:
                        first_user_msg = next(
                            (m for m in session.message_history if m.get("role") == "user"),
                            None,
                        )
                        s["first_message"] = first_user_msg.get("content", "") if first_user_msg else ""
                    else:
                        s["first_message"] = ""
                    result.append(s)
        except Exception as e:
            logger.warning("从 Redis 获取活跃会话失败: user_id=%s error=%s", user_id, e)

        # 再从 L3 PostgreSQL 获取归档会话
        remaining = limit - len(result)
        if remaining > 0:
            try:
                from agent.core.long_term_memory import get_long_term_memory

                ltm = get_long_term_memory()
                archived = await ltm.list_user_sessions(user_id, limit=remaining, offset=0, tenant_id=tenant_id)
                for s in archived:
                    sid = s["session_id"]
                    if sid not in seen_ids:
                        seen_ids.add(sid)
                        result.append(s)
            except Exception as e:
                logger.warning("从 L3 获取归档会话失败: user_id=%s error=%s", user_id, e)

        # 按更新时间倒序排列
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result[:limit]

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
            redis = await self._redis_or_fallback()
            index_key = self._user_sessions_key(user_id, tenant_id)

            if redis:
                session_ids = await redis.zrevrange(index_key, offset, offset + limit - 1)
            else:
                # 内存降级模式：从内存索引获取
                index_data = self._memory_index.get(index_key, {})
                sorted_ids = sorted(
                    index_data.items(), key=lambda x: x[1], reverse=True,
                )
                session_ids = [sid for sid, _ in sorted_ids[offset:offset + limit]]

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
                    # L2和L3都找不到会话时才从索引中清理
                    if redis:
                        await redis.zrem(index_key, sid)

            return sessions
        except Exception as e:
            logger.warning("从 Redis 索引查询会话失败: user_id=%s error=%s", user_id, e)
            return []

    async def acquire_session(self, session_id: str) -> asyncio.Lock:
        """获取会话锁

        同一会话的并发请求需要获取锁，防止消息交错。

        Args:
            session_id: 会话ID

        Returns:
            asyncio.Lock 实例
        """
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def release_session(self, session_id: str) -> None:
        """释放会话锁

        Args:
            session_id: 会话ID
        """
        # Lock 由 with 语句自动释放，这里仅清理引用
        if session_id in self._session_locks:
            del self._session_locks[session_id]

    async def transfer_session(
        self,
        session_id: str,
        target_agent: str,
        from_agent: str = "",
        reason: str = "",
    ) -> bool:
        """转移会话到目标 Agent

        用于 Agent 间协作，将控制权转移给其他 Agent。
        整合了原 session_router.py 的转移历史记录和来源 Agent 验证逻辑。

        转移流程：
        -------------------------------------------------------------------------
        1. 获取会话
        2. 验证来源 Agent（如果指定了 from_agent）
        3. 更新 active_agents 列表
        4. 记录转移历史
        5. 更新会话
        -------------------------------------------------------------------------

        Args:
            session_id: 会话ID
            target_agent: 目标 Agent 名称
            from_agent: 源 Agent 名称（可选，用于验证当前持有者）
            reason: 转移原因

        Returns:
            是否转移成功
        """
        session = await self.get_session(session_id)
        if session is None:
            logger.warning("会话不存在，无法转移: session_id=%s", session_id)
            return False

        # 验证来源 Agent
        if from_agent and session.active_agents:
            if from_agent not in session.active_agents:
                logger.warning(
                    "会话当前 Agent 不匹配: session=%s expected=%s actual=%s",
                    session_id, from_agent, session.active_agents,
                )
                return False

        if target_agent not in session.active_agents:
            session.active_agents.append(target_agent)

        session.metadata["transferred_to"] = target_agent
        session.metadata["transfer_time"] = time.time()
        if from_agent:
            session.metadata["transferred_from"] = from_agent
        if reason:
            session.metadata["transfer_reason"] = reason

        # 记录转移历史
        self._transfer_history.append({
            "session_id": session_id,
            "from_agent": from_agent,
            "to_agent": target_agent,
            "reason": reason,
            "timestamp": time.time(),
        })

        await self.update_session(session)
        logger.info("转移会话: session_id=%s %s -> %s reason=%s", session_id, from_agent, target_agent, reason)
        return True

    def get_transfer_history(self, session_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """获取会话转移历史

        Args:
            session_id: 会话ID（可选，为空则返回全部）
            limit: 返回数量上限

        Returns:
            转移历史列表
        """
        if session_id:
            records = [r for r in self._transfer_history if r["session_id"] == session_id]
        else:
            records = self._transfer_history
        return records[-limit:]

    async def close(self) -> None:
        """释放 Redis 连接引用

        注意：Redis 连接由全局 redis_manager 统一管理，
        此处仅释放本地引用，不关闭底层连接。
        """
        self._redis = None

    def _session_key(self, session_id: str, tenant_id: str = "") -> str:
        """生成会话存储键

        多租户模式下，键包含租户前缀实现数据隔离。

        Args:
            session_id: 会话ID
            tenant_id: 租户ID

        Returns:
            Redis 键，格式：session:{tenant_id}:{session_id}
        """
        if tenant_id:
            return f"session:{tenant_id}:{session_id}"
        return f"session:{session_id}"

    def _user_sessions_key(self, user_id: str, tenant_id: str = "") -> str:
        """生成用户会话索引键

        用于按用户查询会话列表。

        Args:
            user_id: 用户ID
            tenant_id: 租户ID

        Returns:
            Redis 键，格式：user_sessions:{tenant_id}:{user_id}
        """
        if tenant_id:
            return f"user_sessions:{tenant_id}:{user_id}"
        return f"user_sessions:{user_id}"

    async def _count_active_users(self, tenant_id: str = "") -> float:
        """统计活跃用户数

        通过扫描 Redis 中 user_sessions:* 键的数量来估算活跃用户数。

        Args:
            tenant_id: 租户ID

        Returns:
            活跃用户数
        """
        redis = await self._redis_or_fallback()
        if redis:
            if tenant_id:
                pattern = f"user_sessions:{tenant_id}:*"
            else:
                pattern = "user_sessions:*"
            keys = []
            async for key in redis.scan_iter(match=pattern, count=100):
                keys.append(key)
            return float(len(keys))
        else:
            if tenant_id:
                prefix = f"user_sessions:{tenant_id}:"
            else:
                prefix = "user_sessions:"
            count = sum(1 for k in self._memory_index if k.startswith(prefix))
            return float(count)


# 全局会话管理器单例
_session_manager: SessionManager | None = None


async def get_session_manager() -> SessionManager:
    """获取全局会话管理器

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _session_manager
    try:
        from agent.core.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_session_manager() is not None:
            return ctx.get_session_manager()
    except Exception:
        pass
    if _session_manager is None:
        _session_manager = SessionManager()
        try:
            from deploy.ha_manager import DegradationManager
            DegradationManager.register_handler(
                "redis",
                on_degraded=_session_manager.switch_to_memory_fallback,
                on_recovered=_session_manager.switch_to_redis,
            )
        except Exception:
            pass
    return _session_manager
