"""会话路由器

负责会话级别的路由决策和治理：
  - 渠道亲和性：同一渠道的请求路由到同一会话
  - 会话转移：将活跃会话从一个 Agent 转移到另一个 Agent
  - 会话亲和性：通过一致性哈希确保会话粘滞
  - 会话负载均衡：根据 Agent 负载情况分配新会话
"""

import logging
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SessionRoute(BaseModel):
    """会话路由信息"""

    session_id: str
    user_id: str
    channel: str = "web"
    current_agent: str = ""
    target_agent: str = ""
    route_reason: str = ""
    created_at: float = Field(default_factory=time.time)


class AgentLoad(BaseModel):
    """Agent 负载信息"""

    agent_name: str
    active_sessions: int = 0
    avg_latency_ms: float = 0
    error_rate: float = 0
    last_updated: float = Field(default_factory=time.time)


class SessionRouter:
    """会话路由器

    核心能力：
      1. 渠道亲和性：相同渠道的会话优先路由到熟悉该渠道的 Agent
      2. 会话转移：支持将活跃会话从 Agent A 转移到 Agent B
      3. 负载均衡：根据 Agent 负载情况分配新会话
      4. 路由记录：记录每次路由决策，用于审计和优化
    """

    # 渠道 -> Agent 亲和映射
    CHANNEL_AFFINITY: dict[str, str] = {
        "web": "GeneralAgent",
        "api": "GeneralAgent",
        "wechat": "GeneralAgent",
        "dingtalk": "GeneralAgent",
        "feishu": "GeneralAgent",
    }

    def __init__(self) -> None:
        self._agent_loads: dict[str, AgentLoad] = {}
        self._session_routes: dict[str, SessionRoute] = {}
        self._transfer_history: list[dict[str, Any]] = []

    def resolve_agent(
        self,
        session_id: str,
        user_id: str,
        channel: str = "web",
        intent_agent: str = "",
    ) -> str:
        """解析会话应该路由到哪个 Agent

        路由优先级：
        1. 意图分类指定的 Agent（最高优先级）
        2. 已有会话的当前 Agent（会话粘滞）
        3. 渠道亲和性匹配的 Agent
        4. 负载最低的 Agent

        Args:
            session_id: 会话ID
            user_id: 用户ID
            channel: 接入渠道
            intent_agent: 意图分类指定的 Agent

        Returns:
            目标 Agent 名称
        """
        # 1. 意图分类指定
        if intent_agent:
            self._record_route(
                session_id, user_id, channel, "", intent_agent, "intent_classification",
            )
            return intent_agent

        # 2. 会话粘滞
        existing = self._session_routes.get(session_id)
        if existing and existing.current_agent:
            self._record_route(
                session_id, user_id, channel, existing.current_agent, existing.current_agent, "session_stickiness",
            )
            return existing.current_agent

        # 3. 渠道亲和性
        affinity_agent = self.CHANNEL_AFFINITY.get(channel, "")
        if affinity_agent:
            self._record_route(
                session_id, user_id, channel, "", affinity_agent, "channel_affinity",
            )
            return affinity_agent

        # 4. 负载最低
        target = self._least_loaded_agent()
        self._record_route(session_id, user_id, channel, "", target, "load_balancing")
        return target

    async def transfer_session(
        self,
        session_id: str,
        from_agent: str,
        to_agent: str,
        reason: str = "",
    ) -> bool:
        """转移会话到另一个 Agent

        转移流程：
        1. 记录转移历史
        2. 更新会话路由信息
        3. 通知 SessionManager 更新会话状态

        Args:
            session_id: 会话ID
            from_agent: 源 Agent
            to_agent: 目标 Agent
            reason: 转移原因

        Returns:
            是否转移成功
        """
        route = self._session_routes.get(session_id)
        if route is None:
            logger.warning("会话路由信息不存在: %s", session_id)
            return False

        if route.current_agent != from_agent:
            logger.warning(
                "会话当前 Agent 不匹配: session=%s expected=%s actual=%s",
                session_id, from_agent, route.current_agent,
            )
            return False

        # 记录转移历史
        transfer_record = {
            "session_id": session_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._transfer_history.append(transfer_record)

        # 更新路由信息
        route.current_agent = to_agent
        route.route_reason = f"transfer:{reason}"

        # 通知 SessionManager 更新会话
        try:
            from agent.core.session_manager import get_session_manager
            session_mgr = await get_session_manager()
            await session_mgr.transfer_session(session_id, to_agent)
        except Exception as e:
            logger.error("会话转移通知失败: session=%s error=%s", session_id, e)

        logger.info(
            "会话转移完成: session=%s %s -> %s reason=%s",
            session_id, from_agent, to_agent, reason,
        )
        return True

    def update_agent_load(
        self,
        agent_name: str,
        active_sessions: int,
        avg_latency_ms: float = 0,
        error_rate: float = 0,
    ) -> None:
        """更新 Agent 负载信息

        Args:
            agent_name: Agent 名称
            active_sessions: 活跃会话数
            avg_latency_ms: 平均延迟（毫秒）
            error_rate: 错误率（0-1）
        """
        self._agent_loads[agent_name] = AgentLoad(
            agent_name=agent_name,
            active_sessions=active_sessions,
            avg_latency_ms=avg_latency_ms,
            error_rate=error_rate,
        )

    def get_route_info(self, session_id: str) -> SessionRoute | None:
        """获取会话路由信息"""
        return self._session_routes.get(session_id)

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

    def get_agent_loads(self) -> dict[str, dict[str, Any]]:
        """获取所有 Agent 负载信息"""
        return {
            name: {
                "active_sessions": load.active_sessions,
                "avg_latency_ms": load.avg_latency_ms,
                "error_rate": load.error_rate,
            }
            for name, load in self._agent_loads.items()
        }

    def _record_route(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        from_agent: str,
        to_agent: str,
        reason: str,
    ) -> None:
        """记录路由决策"""
        self._session_routes[session_id] = SessionRoute(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            current_agent=to_agent,
            target_agent=to_agent,
            route_reason=reason,
        )

    def _least_loaded_agent(self) -> str:
        """选择负载最低的 Agent"""
        if not self._agent_loads:
            return "GeneralAgent"

        # 综合考虑会话数、延迟和错误率
        def load_score(load: AgentLoad) -> float:
            return load.active_sessions + load.avg_latency_ms / 1000 + load.error_rate * 10

        best = min(self._agent_loads.values(), key=load_score)
        return best.agent_name


# 全局会话路由器
_session_router: SessionRouter | None = None


def get_session_router() -> SessionRouter:
    """获取全局会话路由器"""
    global _session_router
    if _session_router is None:
        _session_router = SessionRouter()
    return _session_router
