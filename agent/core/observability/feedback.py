"""对话反馈机制

支持用户对 Agent 回复进行点赞/点踩评价，收集反馈用于:
  - 质量监控: 追踪 Agent 回复质量趋势
  - 模型优化: 识别低质量回复，优化 Prompt
  - 运营报表: 统计满意度指标

存储策略:
  - 近期反馈存储在 Redis（快速读写）
  - 历史反馈归档到 PostgreSQL（持久化分析）
"""

import json
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """反馈类型"""

    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"


class FeedbackRequest(BaseModel):
    """反馈请求"""

    session_id: str = Field(..., description="会话ID")
    message_index: int = Field(..., description="消息在会话中的索引位置")
    feedback_type: FeedbackType = Field(..., description="反馈类型")
    comment: str | None = Field(default=None, description="用户补充说明")
    user_id: str = Field(..., description="用户ID")
    agent_name: str | None = Field(default=None, description="Agent名称")
    intent: str | None = Field(default=None, description="意图标签")


class FeedbackStats(BaseModel):
    """反馈统计"""

    total: int = 0
    thumbs_up: int = 0
    thumbs_down: int = 0
    satisfaction_rate: float = 0.0


class FeedbackService:
    """对话反馈服务

    管理用户对 Agent 回复的评价，支持提交反馈和查询统计。
    """

    def __init__(self) -> None:
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端（使用统一连接管理器）"""
        if self._redis is None:
            try:
                from agent.core.infrastructure.redis_manager import get_redis_client
                self._redis = await get_redis_client()
            except Exception as e:
                logger.warning("反馈服务 Redis 连接失败: %s", e)
                return None
        return self._redis

    async def submit_feedback(self, request: FeedbackRequest) -> bool:
        """提交对话反馈

        Args:
            request: 反馈请求

        Returns:
            是否提交成功
        """
        redis = await self._get_redis()
        if redis is None:
            return False

        try:
            feedback_id = f"{request.session_id}:{request.message_index}"
            feedback_key = f"feedback:{feedback_id}"

            # 先读取旧反馈数据（在写入新数据之前）
            old_feedback_raw = await redis.get(feedback_key)
            old_type = ""
            if old_feedback_raw:
                try:
                    old_data = json.loads(old_feedback_raw)
                    old_type = old_data.get("feedback_type", "")
                except Exception:
                    pass

            # 存储反馈详情（覆盖同一消息的旧反馈）
            feedback_data = {
                "session_id": request.session_id,
                "message_index": request.message_index,
                "feedback_type": request.feedback_type.value,
                "comment": request.comment or "",
                "user_id": request.user_id,
                "agent_name": request.agent_name or "",
                "intent": request.intent or "",
                "timestamp": time.time(),
            }

            await redis.setex(
                feedback_key,
                86400 * 30,  # 30 天过期
                json.dumps(feedback_data, ensure_ascii=False),
            )

            # 更新统计计数器
            today = time.strftime("%Y-%m-%d")
            stats_key = f"feedback_stats:{today}"

            # 先减少旧反馈类型的计数
            if old_type:
                if old_type == "thumbs_up":
                    await redis.hincrby(stats_key, "thumbs_up", -1)
                elif old_type == "thumbs_down":
                    await redis.hincrby(stats_key, "thumbs_down", -1)

            # 增加新反馈计数
            if request.feedback_type == FeedbackType.THUMBS_UP:
                await redis.hincrby(stats_key, "thumbs_up", 1)
            else:
                await redis.hincrby(stats_key, "thumbs_down", 1)

            # 如果是首次反馈（无旧反馈），total 加 1；否则 total 不变
            if not old_type:
                await redis.hincrby(stats_key, "total", 1)
            from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
            await redis.expire(stats_key, get_persist_ttl_seconds())

            # 按 Agent 维度统计
            if request.agent_name:
                agent_stats_key = f"feedback_stats:agent:{request.agent_name}:{today}"

                # 减少旧反馈类型的 Agent 计数
                if old_type:
                    if old_type == "thumbs_up":
                        await redis.hincrby(agent_stats_key, "thumbs_up", -1)
                    elif old_type == "thumbs_down":
                        await redis.hincrby(agent_stats_key, "thumbs_down", -1)

                # 增加新反馈的 Agent 计数
                if request.feedback_type == FeedbackType.THUMBS_UP:
                    await redis.hincrby(agent_stats_key, "thumbs_up", 1)
                else:
                    await redis.hincrby(agent_stats_key, "thumbs_down", 1)

                if not old_type:
                    await redis.hincrby(agent_stats_key, "total", 1)
                from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
                await redis.expire(agent_stats_key, get_persist_ttl_seconds())

            logger.info(
                "收到反馈: session=%s index=%d type=%s agent=%s",
                request.session_id,
                request.message_index,
                request.feedback_type.value,
                request.agent_name,
            )

            return True

        except Exception as e:
            logger.error("提交反馈失败: %s", e)
            return False

    async def get_feedback(
        self, session_id: str, message_index: int
    ) -> dict[str, Any] | None:
        """获取指定消息的反馈

        Args:
            session_id: 会话ID
            message_index: 消息索引

        Returns:
            反馈数据或 None
        """
        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            feedback_id = f"{session_id}:{message_index}"
            feedback_key = f"feedback:{feedback_id}"
            data = await redis.get(feedback_key)

            if data is None:
                return None

            return json.loads(data)
        except Exception:
            return None

    async def get_daily_stats(self, date: str | None = None) -> FeedbackStats:
        """获取日反馈统计

        Args:
            date: 日期字符串，默认当天

        Returns:
            FeedbackStats 统计结果
        """
        redis = await self._get_redis()
        if redis is None:
            return FeedbackStats()

        try:
            target_date = date or time.strftime("%Y-%m-%d")
            stats_key = f"feedback_stats:{target_date}"
            data = await redis.hgetall(stats_key)

            thumbs_up = int(data.get("thumbs_up", 0))
            thumbs_down = int(data.get("thumbs_down", 0))
            total = thumbs_up + thumbs_down
            satisfaction = thumbs_up / total if total > 0 else 0.0

            return FeedbackStats(
                total=total,
                thumbs_up=thumbs_up,
                thumbs_down=thumbs_down,
                satisfaction_rate=round(satisfaction, 4),
            )
        except Exception:
            return FeedbackStats()

    async def get_agent_stats(
        self, agent_name: str, date: str | None = None
    ) -> FeedbackStats:
        """获取 Agent 维度的反馈统计

        Args:
            agent_name: Agent 名称
            date: 日期字符串，默认当天

        Returns:
            FeedbackStats 统计结果
        """
        redis = await self._get_redis()
        if redis is None:
            return FeedbackStats()

        try:
            target_date = date or time.strftime("%Y-%m-%d")
            stats_key = f"feedback_stats:agent:{agent_name}:{target_date}"
            data = await redis.hgetall(stats_key)

            thumbs_up = int(data.get("thumbs_up", 0))
            thumbs_down = int(data.get("thumbs_down", 0))
            total = thumbs_up + thumbs_down
            satisfaction = thumbs_up / total if total > 0 else 0.0

            return FeedbackStats(
                total=total,
                thumbs_up=thumbs_up,
                thumbs_down=thumbs_down,
                satisfaction_rate=round(satisfaction, 4),
            )
        except Exception:
            return FeedbackStats()

    async def close(self) -> None:
        """关闭 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# 全局反馈服务单例
_feedback_service: FeedbackService | None = None


def get_feedback_service() -> FeedbackService:
    """获取全局反馈服务

    优先从 AppContext 获取，兼容旧的模块级单例模式。
    """
    global _feedback_service
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_feedback_service() is not None:
            return ctx.get_feedback_service()
    except Exception:
        pass
    if _feedback_service is None:
        _feedback_service = FeedbackService()
    return _feedback_service
