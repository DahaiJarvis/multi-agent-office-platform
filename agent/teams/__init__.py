"""Agent 团队编排模块

包含：
  - team_factory: 根据协作模式创建 Agent 团队
  - routing: 任务路由与执行
"""

from agent.teams.team_factory import create_team, MAX_ROUNDS
from agent.teams.routing import route_and_execute, CONFIDENCE_THRESHOLD

__all__ = [
    "create_team",
    "MAX_ROUNDS",
    "route_and_execute",
    "CONFIDENCE_THRESHOLD",
]
