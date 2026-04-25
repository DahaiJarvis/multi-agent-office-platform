"""审计日志集中化

提供集中化的审计日志收集、缓冲和持久化能力。

架构:
  - 写入端: 各业务模块调用 audit_log() 写入审计事件
  - 缓冲层: Redis List 作为缓冲队列，高吞吐写入
  - 消费端: 后台任务批量从 Redis 消费，写入 PostgreSQL
  - 查询端: 通过 API 查询审计日志

审计事件类型:
  - auth: 认证授权事件（登录、登出、Token 刷新）
  - agent: Agent 调用事件（意图分类、任务执行）
  - data: 数据访问事件（查询、修改、删除）
  - system: 系统事件（配置变更、服务启停）
"""

import json
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """审计事件类型"""

    AUTH = "auth"
    AGENT = "agent"
    DATA = "data"
    SYSTEM = "system"


class AuditEvent(BaseModel):
    """审计事件"""

    event_type: AuditEventType = Field(..., description="事件类型")
    action: str = Field(..., description="操作动作")
    user_id: str = Field(default="", description="操作用户")
    session_id: str = Field(default="", description="会话ID")
    agent_name: str = Field(default="", description="Agent名称")
    resource: str = Field(default="", description="操作资源")
    detail: dict[str, Any] = Field(default_factory=dict, description="事件详情")
    timestamp: float = Field(default_factory=time.time, description="事件时间戳")
    request_id: str = Field(default="", description="请求ID")
    ip_address: str = Field(default="", description="客户端IP")


class AuditLogger:
    """审计日志管理器

    写入: 审计事件 -> Redis List（缓冲队列）
    消费: 后台任务 -> 批量写入 PostgreSQL
    查询: 从 PostgreSQL 查询历史审计日志
    """

    BUFFER_KEY = "audit_log:buffer"
    BUFFER_BATCH_SIZE = 100
    BUFFER_MAX_LEN = 100000

    def __init__(self) -> None:
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端"""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                from agent.core.config import get_settings

                settings = get_settings()
                self._redis = aioredis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                )
            except Exception as e:
                logger.warning("审计日志 Redis 连接失败: %s", e)
                return None
        return self._redis

    async def log(
        self,
        event_type: AuditEventType,
        action: str,
        user_id: str = "",
        session_id: str = "",
        agent_name: str = "",
        resource: str = "",
        detail: dict[str, Any] | None = None,
        request_id: str = "",
        ip_address: str = "",
    ) -> bool:
        """记录审计事件

        将审计事件写入 Redis 缓冲队列，由后台任务消费持久化。

        Args:
            event_type: 事件类型
            action: 操作动作
            user_id: 操作用户
            session_id: 会话ID
            agent_name: Agent名称
            resource: 操作资源
            detail: 事件详情
            request_id: 请求ID
            ip_address: 客户端IP

        Returns:
            是否写入成功
        """
        event = AuditEvent(
            event_type=event_type,
            action=action,
            user_id=user_id,
            session_id=session_id,
            agent_name=agent_name,
            resource=resource,
            detail=detail or {},
            request_id=request_id,
            ip_address=ip_address,
        )

        return await self._write_to_buffer(event)

    async def _write_to_buffer(self, event: AuditEvent) -> bool:
        """写入 Redis 缓冲队列"""
        redis = await self._get_redis()
        if redis is None:
            # Redis 不可用时降级到标准日志
            logger.info(
                "AUDIT: type=%s action=%s user=%s session=%s agent=%s resource=%s",
                event.event_type.value,
                event.action,
                event.user_id,
                event.session_id,
                event.agent_name,
                event.resource,
            )
            return False

        try:
            # 检查缓冲队列长度，防止无限增长
            current_len = await redis.llen(self.BUFFER_KEY)
            if current_len >= self.BUFFER_MAX_LEN:
                # 丢弃最旧的一半日志
                await redis.ltrim(self.BUFFER_KEY, self.BUFFER_MAX_LEN // 2, -1)
                logger.warning("审计日志缓冲队列过长，已清理旧数据")

            await redis.rpush(
                self.BUFFER_KEY,
                event.model_dump_json(),
            )
            return True

        except Exception as e:
            logger.error("审计日志写入缓冲失败: %s", e)
            return False

    async def flush_buffer(self) -> int:
        """消费缓冲队列，批量写入持久化存储

        Returns:
            消费的日志条数
        """
        redis = await self._get_redis()
        if redis is None:
            return 0

        try:
            # 批量取出日志
            events_raw = await redis.lpop(self.BUFFER_KEY, self.BUFFER_BATCH_SIZE)
            if not events_raw:
                return 0

            # 单条也包装为列表
            if isinstance(events_raw, str):
                events_raw = [events_raw]

            # 写入 PostgreSQL
            count = await self._persist_events(events_raw)

            logger.debug("审计日志持久化: %d 条", count)
            return count

        except Exception as e:
            logger.error("审计日志消费失败: %s", e)
            return 0

    async def _persist_events(self, events_raw: list[str]) -> int:
        """将审计事件批量写入 PostgreSQL"""
        try:
            from agent.core.config import get_settings
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text

            settings = get_settings()
            engine = create_async_engine(settings.postgres_dsn, pool_size=2, max_overflow=5)
            pool = async_sessionmaker(engine, expire_on_commit=False)

            # 确保表存在
            async with engine.begin() as conn:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id BIGSERIAL PRIMARY KEY,
                        event_type VARCHAR(32) NOT NULL,
                        action VARCHAR(128) NOT NULL,
                        user_id VARCHAR(64) DEFAULT '',
                        session_id VARCHAR(36) DEFAULT '',
                        agent_name VARCHAR(64) DEFAULT '',
                        resource VARCHAR(256) DEFAULT '',
                        detail JSONB DEFAULT '{}',
                        request_id VARCHAR(64) DEFAULT '',
                        ip_address VARCHAR(45) DEFAULT '',
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """))

                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type)
                """))

                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)
                """))

                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)
                """))

            # 批量插入
            count = 0
            async with pool() as session:
                for raw in events_raw:
                    try:
                        event = json.loads(raw)
                        await session.execute(
                            text("""
                                INSERT INTO audit_logs (event_type, action, user_id, session_id,
                                    agent_name, resource, detail, request_id, ip_address, created_at)
                                VALUES (:et, :act, :uid, :sid, :agent, :res, :detail, :rid, :ip, to_timestamp(:ts))
                            """),
                            {
                                "et": event.get("event_type", ""),
                                "act": event.get("action", ""),
                                "uid": event.get("user_id", ""),
                                "sid": event.get("session_id", ""),
                                "agent": event.get("agent_name", ""),
                                "res": event.get("resource", ""),
                                "detail": json.dumps(event.get("detail", {}), ensure_ascii=False),
                                "rid": event.get("request_id", ""),
                                "ip": event.get("ip_address", ""),
                                "ts": event.get("timestamp", time.time()),
                            },
                        )
                        count += 1
                    except Exception:
                        continue

                await session.commit()

            await engine.dispose()
            return count

        except Exception as e:
            logger.error("审计日志持久化失败: %s", e)
            return 0

    async def query_logs(
        self,
        event_type: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询审计日志

        Args:
            event_type: 事件类型过滤
            user_id: 用户ID过滤
            action: 操作动作过滤
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            审计日志列表
        """
        try:
            from agent.core.config import get_settings
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text

            settings = get_settings()
            engine = create_async_engine(settings.postgres_dsn, pool_size=2, max_overflow=5)
            pool = async_sessionmaker(engine, expire_on_commit=False)

            conditions = []
            params: dict[str, Any] = {"limit": limit, "offset": offset}

            if event_type:
                conditions.append("event_type = :et")
                params["et"] = event_type
            if user_id:
                conditions.append("user_id = :uid")
                params["uid"] = user_id
            if action:
                conditions.append("action LIKE :act")
                params["act"] = f"%{action}%"

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT id, event_type, action, user_id, session_id, agent_name,
                       resource, detail, request_id, ip_address, created_at
                FROM audit_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """

            async with pool() as session:
                result = await session.execute(text(query), params)
                rows = result.mappings().all()

            await engine.dispose()

            logs = []
            for row in rows:
                logs.append({
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "action": row["action"],
                    "user_id": row["user_id"],
                    "session_id": row["session_id"],
                    "agent_name": row["agent_name"],
                    "resource": row["resource"],
                    "detail": row["detail"] if isinstance(row["detail"], dict) else {},
                    "request_id": row["request_id"],
                    "ip_address": row["ip_address"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                })

            return logs

        except Exception as e:
            logger.error("查询审计日志失败: %s", e)
            return []

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# 全局审计日志管理器
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志管理器"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


async def audit_log(
    event_type: AuditEventType,
    action: str,
    **kwargs: Any,
) -> bool:
    """便捷函数：记录审计事件

    用法:
        await audit_log(AuditEventType.AUTH, "user_login", user_id="u001")
        await audit_log(AuditEventType.AGENT, "intent_classify", user_id="u001", agent_name="Supervisor")
    """
    audit = get_audit_logger()
    return await audit.log(event_type=event_type, action=action, **kwargs)
