"""多模式 Agent 编排

在现有 DIRECT/SELECTOR/SWARM 基础上，扩展三种高级编排模式：
  - PARALLEL: 并行执行，多个 Agent 同时处理同一任务，汇总结果
  - DEBATE: 辩论模式，多个 Agent 从不同角度讨论，达成共识
  - VOTE: 投票模式，多个 Agent 独立给出答案，多数决定

适用场景：
  - PARALLEL: 需要多维度信息汇总的复杂查询（如市场分析+财务分析+风险评估）
  - DEBATE: 需要深度推理和多方验证的决策问题（如方案评审、风险评估）
  - VOTE: 需要高准确率的事实性问题（如知识问答、分类判断）

使用方式：
    from agent.teams.advanced_orchestration import create_advanced_team

    team = await create_advanced_team(intent, mode="parallel")
    result = await team.run(task="分析这个项目的可行性")
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.agents.domain import create_domain_agent, AGENT_PROMPTS
from agent.agents.supervisor import IntentResult, CollaborationMode
from agent.core.model_client import get_supervisor_client, get_domain_agent_client
from agent.core.mcp_integration import load_agent_tools

logger = logging.getLogger(__name__)


def _extract_agent_response(result: Any) -> str:
    """从 Agent 执行结果中提取文本内容

    兼容 AutoGen TaskResult 和其他可能的返回格式。
    """
    try:
        if hasattr(result, 'messages') and result.messages:
            content_parts = []
            for msg in result.messages:
                if hasattr(msg, 'content') and msg.content:
                    text = msg.content
                    if isinstance(text, list):
                        text = "".join(
                            part.text for part in text if hasattr(part, "text")
                        )
                    if text:
                        content_parts.append(text)
            if content_parts:
                return "\n".join(content_parts)
    except Exception:
        pass

    return str(result)


class AdvancedMode(str, Enum):
    """高级编排模式"""

    PARALLEL = "parallel"
    DEBATE = "debate"
    VOTE = "vote"


class ParallelResult(BaseModel):
    """并行执行结果"""

    agent_results: dict[str, str] = Field(default_factory=dict, description="各 Agent 的结果")
    aggregated: str = Field(default="", description="汇总后的结果")
    duration_ms: float = Field(default=0, description="总耗时")


class DebateResult(BaseModel):
    """辩论结果"""

    rounds: int = Field(default=0, description="辩论轮次")
    positions: dict[str, str] = Field(default_factory=dict, description="各 Agent 的立场")
    consensus: str = Field(default="", description="共识结果")
    dissent: str = Field(default="", description="少数派意见")


class VoteResult(BaseModel):
    """投票结果"""

    votes: dict[str, str] = Field(default_factory=dict, description="各 Agent 的投票")
    vote_counts: dict[str, int] = Field(default_factory=dict, description="各选项票数")
    winner: str = Field(default="", description="胜出选项")
    confidence: float = Field(default=0.0, description="置信度（胜出票数/总票数）")


class ParallelTeam:
    """并行执行团队

    多个 Agent 同时处理同一任务，最后由汇总器整合结果。
    适用于需要多维度信息的复杂查询。
    """

    def __init__(
        self,
        agent_names: list[str],
        max_concurrent: int = 3,
    ) -> None:
        self._agent_names = agent_names
        self._max_concurrent = max_concurrent
        self._agents: list[Any] = []
        self._aggregator: Any = None

    async def _initialize(self) -> None:
        """初始化 Agent 实例"""
        if self._agents:
            return

        for name in self._agent_names:
            try:
                agent = await create_domain_agent(name)
                self._agents.append(agent)
            except Exception as e:
                logger.warning("并行团队初始化 Agent %s 失败: %s", name, e)

        if not self._agents:
            raise RuntimeError(f"团队初始化失败：所有 Agent 均不可用，尝试的 Agent: {self._agent_names}")

        # 创建汇总器
        from autogen_agentchat.agents import AssistantAgent
        self._aggregator = AssistantAgent(
            name="Aggregator",
            model_client=get_supervisor_client(),
            system_message=(
                "你是结果汇总专家。你需要将多个 Agent 的执行结果整合为一份完整、"
                "一致的报告。要求：\n"
                "1. 去除重复信息\n"
                "2. 保留各 Agent 的独特观点\n"
                "3. 标注信息来源\n"
                "4. 如有冲突，列出不同观点\n"
                "5. 给出综合结论"
            ),
        )

    async def run(self, task: str) -> ParallelResult:
        """并行执行任务

        Args:
            task: 任务描述

        Returns:
            ParallelResult 包含各 Agent 结果和汇总
        """
        await self._initialize()
        start_time = time.time()

        # 并行执行
        semaphore = asyncio.Semaphore(self._max_concurrent)
        agent_results: dict[str, str] = {}

        async def _run_single(agent: Any) -> None:
            async with semaphore:
                try:
                    result = await agent.run(task=task)
                    agent_results[agent.name] = _extract_agent_response(result)
                except Exception as e:
                    agent_results[agent.name] = f"执行失败: {e}"
                    logger.error("并行执行 Agent %s 失败: %s", agent.name, e)

        tasks = [_run_single(agent) for agent in self._agents]
        await asyncio.gather(*tasks)

        # 汇总结果
        aggregated = await self._aggregate(task, agent_results)

        duration_ms = (time.time() - start_time) * 1000
        return ParallelResult(
            agent_results=agent_results,
            aggregated=aggregated,
            duration_ms=duration_ms,
        )

    async def _aggregate(self, task: str, results: dict[str, str]) -> str:
        """汇总各 Agent 结果"""
        if not self._aggregator:
            return "\n\n".join(f"【{name}】{result}" for name, result in results.items())

        try:
            from autogen_core.models import UserMessage
            summary_prompt = (
                f"原始任务: {task}\n\n"
                + "\n\n".join(f"【{name}的分析结果】\n{result}" for name, result in results.items())
                + "\n\n请汇总以上分析结果，给出综合结论。"
            )
            response = await self._aggregator.model_client.create(
                messages=[UserMessage(source="user", content=summary_prompt)],
            )
            content = response.content
            if isinstance(content, list):
                content = "".join(part.text for part in content if hasattr(part, "text"))
            return content
        except Exception as e:
            logger.error("汇总结果失败: %s", e)
            return "\n\n".join(f"【{name}】{result}" for name, result in results.items())


class DebateTeam:
    """辩论团队

    多个 Agent 从不同角度讨论同一问题，通过多轮辩论达成共识。
    适用于需要深度推理和多方验证的决策问题。
    """

    def __init__(
        self,
        agent_names: list[str],
        max_rounds: int = 3,
    ) -> None:
        self._agent_names = agent_names
        self._max_rounds = max_rounds
        self._agents: list[Any] = []
        self._judge: Any = None

    async def _initialize(self) -> None:
        """初始化 Agent 实例"""
        if self._agents:
            return

        for name in self._agent_names:
            try:
                agent = await create_domain_agent(name)
                self._agents.append(agent)
            except Exception as e:
                logger.warning("辩论团队初始化 Agent %s 失败: %s", name, e)

        if not self._agents:
            raise RuntimeError(f"团队初始化失败：所有 Agent 均不可用，尝试的 Agent: {self._agent_names}")

        # 创建裁判
        from autogen_agentchat.agents import AssistantAgent
        self._judge = AssistantAgent(
            name="Judge",
            model_client=get_supervisor_client(),
            system_message=(
                "你是辩论裁判。你需要：\n"
                "1. 总结各方观点\n"
                "2. 识别共识和分歧\n"
                "3. 基于事实和逻辑给出最终判断\n"
                "4. 如有少数派意见，单独列出"
            ),
        )

    async def run(self, task: str) -> DebateResult:
        """执行辩论

        Args:
            task: 辩论主题/任务

        Returns:
            DebateResult 包含辩论过程和结果
        """
        await self._initialize()

        positions: dict[str, str] = {}

        # 第一轮：各 Agent 给出初始立场
        for agent in self._agents:
            try:
                result = await agent.run(task=f"请分析以下问题并给出你的观点: {task}")
                positions[agent.name] = _extract_agent_response(result)
            except Exception as e:
                positions[agent.name] = f"分析失败: {e}"

        # 后续轮次：基于其他 Agent 的观点进行反驳/补充
        for round_num in range(1, self._max_rounds):
            for agent in self._agents:
                other_views = {
                    name: pos for name, pos in positions.items() if name != agent.name
                }
                counter_prompt = (
                    f"原始问题: {task}\n\n"
                    f"其他观点:\n"
                    + "\n".join(f"【{name}】{view[:300]}" for name, view in other_views.items())
                    + f"\n\n请基于以上观点进行反驳或补充（第{round_num + 1}轮）。"
                )
                try:
                    result = await agent.run(task=counter_prompt)
                    positions[agent.name] = _extract_agent_response(result)
                except Exception as e:
                    logger.warning("辩论第%d轮 Agent %s 失败: %s", round_num + 1, agent.name, e)

        # 裁判总结
        consensus, dissent = await self._judge_debate(task, positions)

        return DebateResult(
            rounds=self._max_rounds,
            positions=positions,
            consensus=consensus,
            dissent=dissent,
        )

    async def _judge_debate(
        self, task: str, positions: dict[str, str]
    ) -> tuple[str, str]:
        """裁判总结辩论结果"""
        if not self._judge:
            return "\n".join(positions.values()), ""

        try:
            from autogen_core.models import UserMessage
            judge_prompt = (
                f"辩论主题: {task}\n\n"
                + "\n\n".join(f"【{name}的观点】\n{pos[:500]}" for name, pos in positions.items())
                + "\n\n请总结共识和分歧。"
            )
            response = await self._judge.model_client.create(
                messages=[UserMessage(source="user", content=judge_prompt)],
            )
            content = response.content
            if isinstance(content, list):
                content = "".join(part.text for part in content if hasattr(part, "text"))
            # 简单分割共识和少数派意见
            parts = content.split("少数派意见")
            consensus = parts[0].strip()
            dissent = parts[1].strip() if len(parts) > 1 else ""
            return consensus, dissent
        except Exception as e:
            logger.error("裁判总结失败: %s", e)
            return "\n".join(positions.values()), ""


class VoteTeam:
    """投票团队

    多个 Agent 独立给出答案，通过多数决定选择最终结果。
    适用于需要高准确率的事实性问题。
    """

    def __init__(
        self,
        agent_names: list[str],
    ) -> None:
        self._agent_names = agent_names
        self._agents: list[Any] = []

    async def _initialize(self) -> None:
        """初始化 Agent 实例"""
        if self._agents:
            return

        for name in self._agent_names:
            try:
                agent = await create_domain_agent(name)
                self._agents.append(agent)
            except Exception as e:
                logger.warning("投票团队初始化 Agent %s 失败: %s", name, e)

        if not self._agents:
            raise RuntimeError(f"团队初始化失败：所有 Agent 均不可用，尝试的 Agent: {self._agent_names}")

    async def run(self, task: str, options: list[str] | None = None) -> VoteResult:
        """执行投票

        Args:
            task: 任务/问题
            options: 可选的选项列表，为空时 Agent 自由回答

        Returns:
            VoteResult 包含投票结果
        """
        await self._initialize()

        votes: dict[str, str] = {}

        # 各 Agent 独立投票
        for agent in self._agents:
            try:
                if options:
                    vote_prompt = (
                        f"问题: {task}\n"
                        f"选项: {', '.join(options)}\n"
                        f"请只输出你选择的选项，不要输出其他内容。"
                    )
                else:
                    vote_prompt = f"请回答以下问题，给出简洁明确的答案: {task}"

                result = await agent.run(task=vote_prompt)
                content = _extract_agent_response(result)
                vote = content.strip().split("\n")[0] if content else "未知"
                votes[agent.name] = vote
            except Exception as e:
                votes[agent.name] = f"投票失败: {e}"

        # 计票
        vote_counts: dict[str, int] = {}
        for vote in votes.values():
            normalized = vote.strip().lower()
            vote_counts[normalized] = vote_counts.get(normalized, 0) + 1

        # 确定胜出选项
        if vote_counts:
            winner = max(vote_counts, key=vote_counts.get)
            total = sum(vote_counts.values())
            confidence = vote_counts[winner] / total if total > 0 else 0.0
        else:
            winner = ""
            confidence = 0.0

        return VoteResult(
            votes=votes,
            vote_counts=vote_counts,
            winner=winner,
            confidence=confidence,
        )


# ==================== 工厂函数 ====================

# 高级编排模式与 Agent 的推荐搭配
ADVANCED_MODE_AGENTS: dict[AdvancedMode, list[str]] = {
    AdvancedMode.PARALLEL: ["KnowledgeAgent", "CRMAgent", "FinanceAgent"],
    AdvancedMode.DEBATE: ["KnowledgeAgent", "OfficeAssistant"],
    AdvancedMode.VOTE: ["KnowledgeAgent", "OfficeAssistant", "HRAgent"],
}


async def create_advanced_team(
    intent: IntentResult,
    mode: AdvancedMode | None = None,
    agent_names: list[str] | None = None,
) -> ParallelTeam | DebateTeam | VoteTeam:
    """创建高级编排团队

    Args:
        intent: 意图分类结果
        mode: 编排模式，为空时根据意图自动选择
        agent_names: 参与的 Agent 名称列表，为空时使用推荐搭配

    Returns:
        高级编排团队实例
    """
    if mode is None:
        mode = _select_mode(intent)

    if agent_names is None:
        agent_names = _select_agents(intent, mode)

    logger.info("创建高级编排团队: mode=%s agents=%s", mode.value, agent_names)

    if mode == AdvancedMode.PARALLEL:
        return ParallelTeam(agent_names=agent_names)
    elif mode == AdvancedMode.DEBATE:
        return DebateTeam(agent_names=agent_names)
    elif mode == AdvancedMode.VOTE:
        return VoteTeam(agent_names=agent_names)
    else:
        raise ValueError(f"不支持的编排模式: {mode}")


def _select_mode(intent: IntentResult) -> AdvancedMode:
    """根据意图自动选择编排模式"""
    if intent.intent == "cross_system":
        return AdvancedMode.PARALLEL
    if intent.intent == "complex_task":
        return AdvancedMode.DEBATE
    return AdvancedMode.VOTE


def _select_agents(intent: IntentResult, mode: AdvancedMode) -> list[str]:
    """根据意图和模式选择参与的 Agent"""
    base_agents = ADVANCED_MODE_AGENTS.get(mode, ["KnowledgeAgent"])

    # 如果目标 Agent 不在默认列表中，添加到列表
    if intent.target_agent not in base_agents:
        base_agents = [intent.target_agent] + base_agents[:2]

    return base_agents[:4]
