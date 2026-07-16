"""多模式 Agent 编排

在现有 DIRECT/SELECTOR/SWARM 基础上，扩展三种高级编排模式：
  - PARALLEL: 并行执行，多个 Agent 同时处理同一任务，汇总结果
  - DEBATE: 辩论模式，多个 Agent 从不同角度讨论，达成共识
  - VOTE: 投票模式，多个 Agent 独立给出答案，多数决定

适用场景：
  - PARALLEL: 需要多维度信息汇总的复杂查询（如市场分析+财务分析+风险评估）
  - DEBATE: 需要深度推理和多方验证的决策问题（如方案评审、风险评估）
  - VOTE: 需要高准确率的事实性问题（如知识问答、分类判断）

触发条件：
  仅在 IntentResult 显式指定 orchestration_mode 时触发，不再由意图标签自动决定。
  cross_system 和 complex_task 默认使用 SEQUENTIAL 顺序编排（由 TaskExecutionEngine 处理）。
  PARALLEL/DEBATE/VOTE 适用于以下场景：
    - PARALLEL: 多维度并行收集信息，各维度无依赖关系
    - DEBATE: 需要多角度深度推理和验证的决策问题
    - VOTE: 需要高准确率的事实性判断

三种模式是互斥的，每次请求只会选择其中一种模式执行，不会组合使用。

使用方式：
    from agent.teams.advanced_orchestration import create_advanced_team

    team = await create_advanced_team(intent, mode="parallel")
    result = await team.run(task="分析这个项目的可行性")
"""

import asyncio
import difflib
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.agents.domain import create_domain_agent, AGENT_PROMPTS
from agent.agents.supervisor import IntentResult, CollaborationMode
from agent.core.model.model_client import get_supervisor_client, get_domain_agent_client
from agent.core.mcp.mcp_integration import load_agent_tools

logger = logging.getLogger(__name__)


def _extract_agent_response(result: Any) -> str:
    """从 Agent 执行结果中提取文本内容

    AutoGen 的 Agent.run() 返回 TaskResult 对象，其中 messages 是消息列表。
    每条消息的 content 可能是字符串，也可能是包含多个 part 的列表（多模态场景）。
    此函数统一处理这两种格式，提取纯文本内容。

    过滤策略：
      - 跳过工具调用消息（source 为工具名或包含 function_call 的消息）
      - 跳过纯 JSON 格式的消息（通常是 API 原始返回数据）
      - 只保留 AssistantAgent 的最终回复

    Args:
        result: Agent.run() 的返回值，通常是 AutoGen TaskResult

    Returns:
        提取的文本内容，多条消息以换行拼接
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
                    if not text:
                        continue

                    # 跳过工具调用结果：source 为工具名（通常包含下划线或特定前缀）
                    source = getattr(msg, 'source', '') or ''
                    if _is_tool_source(source):
                        continue

                    # 跳过纯 JSON 格式的消息（API 原始返回数据）
                    if _is_raw_json(text):
                        continue

                    content_parts.append(text)
            if content_parts:
                # 优先返回最后一条非工具消息（即 Agent 的最终回复）
                return content_parts[-1]
    except Exception as e:
        logger.debug("操作失败，已忽略: %s", e)

    return str(result)


def _is_tool_source(source: str) -> bool:
    """判断消息来源是否为工具调用

    AutoGen 中工具调用的 source 通常是工具函数名，
    如 "execute_sql"、"search_knowledge_base" 等。
    AssistantAgent 的 source 通常是 Agent 名称。

    Args:
        source: 消息来源标识

    Returns:
        是否为工具调用结果
    """
    if not source:
        return False
    # Agent 名称通常以 "Agent" 结尾或为已知名称
    agent_suffixes = ("Agent", "Assistant", "Judge", "Aggregator", "Voter", "Reviewer")
    if any(source.endswith(suffix) for suffix in agent_suffixes):
        return False
    # user 是用户消息
    if source == "user":
        return False
    # 其他 source 视为工具调用
    return True


def _is_raw_json(text: str) -> bool:
    """判断文本是否为原始 JSON 数据

    API 返回的原始数据通常是 JSON 格式，如：
      - {"status": "success", "data": {...}}
      - [{"id": 1, "name": "..."}]

    但 Agent 的自然语言回复中也可能包含 JSON 代码块，
    此函数只判断整段文本是否为纯 JSON（以 { 或 [ 开头）。

    Args:
        text: 待检测文本

    Returns:
        是否为原始 JSON 数据
    """
    stripped = text.strip()
    if not stripped:
        return False
    # 只判断整段文本为 JSON 的情况（以 { 或 [ 开头且能解析）
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            import json
            json.loads(stripped)
            return True
        except (json.JSONDecodeError, ValueError):
            pass
    return False


# 投票同义词映射表
# -------------------------------------------------------------------------
# 用于将不同语言/表达的投票结果归一化为统一格式
# key 为小写的中文或英文表达，value 为归一化后的标准英文形式
# -------------------------------------------------------------------------
VOTE_SYNONYM_MAP: dict[str, str] = {
    "同意": "agree",
    "赞同": "agree",
    "赞成": "agree",
    "支持": "agree",
    "是": "yes",
    "对的": "yes",
    "正确": "yes",
    "否": "no",
    "不是": "no",
    "不对": "no",
    "拒绝": "disagree",
    "反对": "disagree",
    "不同意": "disagree",
    "否决": "reject",
    "通过": "approve",
    "批准": "approve",
    "批准": "approve",
    "确认": "confirm",
    "取消": "cancel",
    "撤销": "cancel",
    "高": "high",
    "中": "medium",
    "低": "low",
    "有风险": "risky",
    "无风险": "safe",
    "安全": "safe",
    "危险": "dangerous",
    "存在": "exist",
    "不存在": "not_exist",
    "是": "true",
    "否": "false",
}


def _normalize_vote(vote: str, options: list[str] | None = None) -> str:
    """归一化投票结果

    处理流程：
      1. 去除首尾空白并转为小写
      2. 查找同义词映射表，将中文表达转为标准英文
      3. 如果提供了 options，使用模糊匹配将投票结果映射到最接近的选项

    Args:
        vote: Agent 的原始投票内容
        options: 可选的选项列表，提供时会尝试模糊匹配到选项

    Returns:
        归一化后的投票结果
    """
    normalized = vote.strip().lower()

    # 查找同义词映射
    mapped = VOTE_SYNONYM_MAP.get(normalized)
    if mapped:
        normalized = mapped

    # 如果提供了选项列表，尝试模糊匹配到最接近的选项
    if options:
        # 先尝试精确匹配（忽略大小写）
        for option in options:
            if normalized == option.strip().lower():
                return option

        # 使用 difflib 模糊匹配，找到最接近的选项
        options_lower = [opt.strip().lower() for opt in options]
        matches = difflib.get_close_matches(normalized, options_lower, n=1, cutoff=0.6)
        if matches:
            matched_lower = matches[0]
            # 返回原始选项（保持原始大小写）
            for option in options:
                if option.strip().lower() == matched_lower:
                    return option

    return normalized


class AdvancedMode(str, Enum):
    """高级编排模式枚举

    三种模式互斥，每次请求只选择一种：
      - PARALLEL: 并行执行，多 Agent 同时处理，Aggregator 汇总
      - DEBATE: 辩论模式，多 Agent 多轮讨论，Judge 裁决
      - VOTE: 投票模式，多 Agent 独立回答，多数决定
    """

    PARALLEL = "parallel"
    DEBATE = "debate"
    VOTE = "vote"


class ParallelResult(BaseModel):
    """并行执行结果

    Attributes:
        agent_results: 各 Agent 的独立执行结果，key 为 Agent 名称
        aggregated: Aggregator 汇总后的综合结论
        duration_ms: 从开始执行到汇总完成的总耗时
    """

    agent_results: dict[str, str] = Field(default_factory=dict, description="各 Agent 的结果")
    aggregated: str = Field(default="", description="汇总后的结果")
    duration_ms: float = Field(default=0, description="总耗时")


class DebateResult(BaseModel):
    """辩论结果

    Attributes:
        rounds: 实际执行的辩论轮次
        positions: 各 Agent 在最后一轮的立场/观点
        consensus: Judge 总结的共识部分
        dissent: Judge 识别的少数派意见（如有）
    """

    rounds: int = Field(default=0, description="辩论轮次")
    positions: dict[str, str] = Field(default_factory=dict, description="各 Agent 的立场")
    consensus: str = Field(default="", description="共识结果")
    dissent: str = Field(default="", description="少数派意见")


class VoteResult(BaseModel):
    """投票结果

    Attributes:
        votes: 各 Agent 的原始投票内容
        vote_counts: 各选项的票数统计（已通过同义词映射和模糊匹配归一化）
        winner: 得票最多的选项
        confidence: 置信度 = 胜出票数 / 总票数，越高表示共识越强
    """

    votes: dict[str, str] = Field(default_factory=dict, description="各 Agent 的投票")
    vote_counts: dict[str, int] = Field(default_factory=dict, description="各选项票数")
    winner: str = Field(default="", description="胜出选项")
    confidence: float = Field(default=0.0, description="置信度（胜出票数/总票数）")


class ParallelTeam:
    """并行执行团队

    工作流程：
      1. 初始化阶段：创建多个领域 Agent + 一个 Aggregator（汇总器）
      2. 执行阶段：所有 Agent 并行处理同一任务，通过 Semaphore 控制最大并发数
      3. 汇总阶段：Aggregator 将各 Agent 结果整合为一份综合报告

    适用场景：需要多维度信息汇总的复杂查询
    示例："分析这个项目的可行性" -> KnowledgeAgent(技术可行性) + CRMAgent(市场前景) + FinanceAgent(财务风险)

    并发控制：
      使用 asyncio.Semaphore 限制同时执行的 Agent 数量（默认 3 个），
      避免大量 Agent 同时调用 LLM 导致限流。
    """

    def __init__(
        self,
        agent_names: list[str],
        max_concurrent: int = 3,
    ) -> None:
        """初始化并行团队

        Args:
            agent_names: 参与并行执行的 Agent 名称列表
            max_concurrent: 最大并发数，防止同时调用过多 LLM 导致限流
        """
        self._agent_names = agent_names
        self._max_concurrent = max_concurrent
        self._agents: list[Any] = []
        self._aggregator: Any = None

    async def _initialize(self) -> None:
        """初始化 Agent 实例

        延迟初始化策略：首次 run() 时才创建 Agent 实例，避免构造函数中的异步操作。
        创建流程：
          1. 依次创建各领域 Agent（通过 create_domain_agent 加载 Prompt 和 MCP 工具）
          2. 创建 Aggregator（使用 qwen-max 高推理能力模型，负责汇总多维度结果）

        Raises:
            RuntimeError: 所有 Agent 均初始化失败时抛出
        """
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

        # 创建汇总器：使用 qwen-max 模型，具备较强的信息整合能力
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

    async def run(self, task: str, progress_callback: Any = None) -> ParallelResult:
        """并行执行任务

        执行流程：
          1. 确保 Agent 已初始化
          2. 使用 asyncio.gather 并行执行所有 Agent
          3. 通过 Semaphore 控制并发数，避免 LLM 限流
          4. 收集各 Agent 的执行结果
          5. 调用 Aggregator 汇总结果

        Args:
            task: 任务描述，所有 Agent 处理同一任务
            progress_callback: 进度回调函数，签名为 async callback(agent_name, status, message)

        Returns:
            ParallelResult 包含各 Agent 独立结果和汇总结论
        """
        await self._initialize()
        start_time = time.time()

        semaphore = asyncio.Semaphore(self._max_concurrent)
        agent_results: dict[str, str] = {}

        async def _run_single(agent: Any) -> None:
            """单个 Agent 的执行协程，受 Semaphore 控制并发"""
            async with semaphore:
                try:
                    if progress_callback:
                        await progress_callback(agent.name, "running", f"{agent.name} 正在分析...")
                    result = await agent.run(task=task)
                    agent_results[agent.name] = _extract_agent_response(result)
                    if progress_callback:
                        await progress_callback(agent.name, "completed", f"{agent.name} 分析完成")
                except Exception as e:
                    agent_results[agent.name] = f"执行失败: {e}"
                    logger.error("并行执行 Agent %s 失败: %s", agent.name, e)
                    if progress_callback:
                        await progress_callback(agent.name, "failed", f"{agent.name} 执行失败: {e}")

        tasks = [_run_single(agent) for agent in self._agents]
        await asyncio.gather(*tasks)

        # 汇总结果：将各 Agent 的独立结果交给 Aggregator 整合
        aggregated = await self._aggregate(task, agent_results)

        duration_ms = (time.time() - start_time) * 1000
        return ParallelResult(
            agent_results=agent_results,
            aggregated=aggregated,
            duration_ms=duration_ms,
        )

    async def _aggregate(self, task: str, results: dict[str, str]) -> str:
        """汇总各 Agent 结果

        将各 Agent 的执行结果拼接为 Prompt，交给 Aggregator 生成综合报告。
        如果 Aggregator 不可用，降级为简单拼接格式。

        Args:
            task: 原始任务描述
            results: 各 Agent 的执行结果

        Returns:
            汇总后的文本
        """
        if not self._aggregator:
            # 降级：无 Aggregator 时简单拼接
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
            # 降级：汇总失败时返回简单拼接
            return "\n\n".join(f"【{name}】{result}" for name, result in results.items())


class DebateTeam:
    """辩论团队

    工作流程：
      1. 初始化阶段：创建多个领域 Agent + 一个 Judge（裁判）
      2. 第一轮：各 Agent 独立分析问题，给出初始立场
      3. 后续轮次：每个 Agent 看到其他 Agent 的观点后进行反驳或补充
      4. 裁判总结：Judge 综合各方观点，区分共识和少数派意见

    适用场景：需要深度推理和多方验证的决策问题
    示例："是否应该采用微服务架构" -> KnowledgeAgent(技术可行性) vs OfficeAssistant(实施成本)

    辩论轮次：默认 3 轮，可通过 max_rounds 参数调整。
    每轮中各 Agent 依次发言，看到其他 Agent 的观点摘要后进行反驳/补充。
    """

    def __init__(
        self,
        agent_names: list[str],
        max_rounds: int = 3,
    ) -> None:
        """初始化辩论团队

        Args:
            agent_names: 参与辩论的 Agent 名称列表
            max_rounds: 最大辩论轮次，默认 3 轮（1 轮初始立场 + 2 轮反驳补充）
        """
        self._agent_names = agent_names
        self._max_rounds = max_rounds
        self._agents: list[Any] = []
        self._judge: Any = None

    async def _initialize(self) -> None:
        """初始化 Agent 实例

        创建流程：
          1. 依次创建各领域 Agent
          2. 创建 Judge（使用 qwen-max 模型，负责总结共识和分歧）

        Raises:
            RuntimeError: 所有 Agent 均初始化失败时抛出
        """
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

        # 创建裁判：使用 qwen-max 模型，具备较强的逻辑分析能力
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

    async def run(self, task: str, progress_callback: Any = None) -> DebateResult:
        """执行辩论

        执行流程：
          1. 第一轮：各 Agent 独立分析问题，给出初始立场
          2. 第 2~N 轮：每个 Agent 看到其他 Agent 的观点摘要后进行反驳或补充
          3. 裁判总结：Judge 综合各方观点，输出共识和少数派意见

        注意：辩论是串行执行的（每轮中各 Agent 依次发言），
        因为后续发言需要看到前序 Agent 的观点。

        Args:
            task: 辩论主题/任务描述
            progress_callback: 进度回调函数，签名为 async callback(agent_name, status, message)

        Returns:
            DebateResult 包含辩论轮次、各方立场、共识和少数派意见
        """
        await self._initialize()

        positions: dict[str, str] = {}

        # 第一轮：各 Agent 独立给出初始立场
        if progress_callback:
            await progress_callback("Judge", "running", "辩论开始 - 初始立场阶段")
        for agent in self._agents:
            try:
                if progress_callback:
                    await progress_callback(agent.name, "running", f"{agent.name} 正在给出初始立场...")
                result = await agent.run(task=f"请分析以下问题并给出你的观点: {task}")
                positions[agent.name] = _extract_agent_response(result)
                if progress_callback:
                    await progress_callback(agent.name, "completed", f"{agent.name} 已给出初始立场")
            except Exception as e:
                positions[agent.name] = f"分析失败: {e}"
                if progress_callback:
                    await progress_callback(agent.name, "failed", f"{agent.name} 初始立场分析失败")

        # 后续轮次：每个 Agent 基于其他 Agent 的观点进行反驳或补充
        for round_num in range(1, self._max_rounds):
            if progress_callback:
                await progress_callback("Judge", "running", f"辩论第{round_num + 1}轮开始")
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
                    if progress_callback:
                        await progress_callback(agent.name, "running", f"{agent.name} 第{round_num + 1}轮发言中...")
                    result = await agent.run(task=counter_prompt)
                    positions[agent.name] = _extract_agent_response(result)
                    if progress_callback:
                        await progress_callback(agent.name, "completed", f"{agent.name} 第{round_num + 1}轮发言完成")
                except Exception as e:
                    logger.warning("辩论第%d轮 Agent %s 失败: %s", round_num + 1, agent.name, e)

        # 裁判总结：将各方最终立场交给 Judge，区分共识和少数派意见
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
        """裁判总结辩论结果

        将各 Agent 的最终立场截取前 500 字，交给 Judge 生成总结。
        Judge 的输出中，"少数派意见" 之前的部分视为共识，之后的部分视为少数派意见。

        Args:
            task: 辩论主题
            positions: 各 Agent 的最终立场

        Returns:
            (共识, 少数派意见) 元组
        """
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
            # 简单分割：以"少数派意见"为分界线，前半部分为共识，后半部分为少数派意见
            parts = content.split("少数派意见")
            consensus = parts[0].strip()
            dissent = parts[1].strip() if len(parts) > 1 else ""
            return consensus, dissent
        except Exception as e:
            logger.error("裁判总结失败: %s", e)
            return "\n".join(positions.values()), ""


class VoteTeam:
    """投票团队

    工作流程：
      1. 初始化阶段：创建多个领域 Agent
      2. 投票阶段：各 Agent 独立回答同一问题（互不可见其他 Agent 的回答）
      3. 计票阶段：统计各选项的票数，多数决定最终结果

    适用场景：需要高准确率的事实性问题
    示例："这个合同条款是否存在法律风险" -> KnowledgeAgent/OfficeAssistant/HRAgent 各自独立判断

    与 PARALLEL 的区别：
      - PARALLEL 的 Agent 处理同一任务的不同维度，结果互补
      - VOTE 的 Agent 对同一问题给出相同维度的答案，通过多数决定提高准确率

    与 DEBATE 的区别：
      - DEBATE 的 Agent 之间有交互（看到其他观点后反驳/补充）
      - VOTE 的 Agent 之间完全独立，互不可见
    """

    def __init__(
        self,
        agent_names: list[str],
    ) -> None:
        """初始化投票团队

        Args:
            agent_names: 参与投票的 Agent 名称列表
        """
        self._agent_names = agent_names
        self._agents: list[Any] = []

    async def _initialize(self) -> None:
        """初始化 Agent 实例

        投票模式不需要额外的 Aggregator 或 Judge，
        因为计票逻辑是确定性的（多数决定），不需要 LLM 参与。

        Raises:
            RuntimeError: 所有 Agent 均初始化失败时抛出
        """
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

    async def run(self, task: str, options: list[str] | None = None, progress_callback: Any = None) -> VoteResult:
        """执行投票

        执行流程：
          1. 各 Agent 独立回答问题（串行执行，但互不可见其他 Agent 的回答）
          2. 如果提供了 options，Agent 只需从选项中选择；否则自由回答
          3. 对所有回答进行归一化（同义词映射 + 模糊匹配）后计票
          4. 得票最多的选项胜出，置信度 = 胜出票数 / 总票数

        Args:
            task: 任务/问题描述
            options: 可选的选项列表。提供时 Agent 只需选择，不提供时 Agent 自由回答
            progress_callback: 进度回调函数，签名为 async callback(agent_name, status, message)

        Returns:
            VoteResult 包含各 Agent 的投票、计票结果和胜出选项
        """
        await self._initialize()

        votes: dict[str, str] = {}

        for agent in self._agents:
            try:
                if progress_callback:
                    await progress_callback(agent.name, "running", f"{agent.name} 正在投票...")
                if options:
                    vote_prompt = (
                        f"问题: {task}\n"
                        f"可选选项: {', '.join(options)}\n"
                        f"请严格从上述选项中选择一个，原样输出选项文本，"
                        f"不要翻译、不要改写、不要添加解释或其他内容。\n"
                        f"正确示例: {options[0]}\n"
                        f"错误示例: 同意、我选择{options[0]}、{options[0].lower() if options[0].isalpha() else '改写选项'}"
                    )
                else:
                    vote_prompt = f"请回答以下问题，给出简洁明确的答案: {task}"

                result = await agent.run(task=vote_prompt)
                content = _extract_agent_response(result)
                vote = content.strip().split("\n")[0] if content else "未知"
                votes[agent.name] = vote
                if progress_callback:
                    await progress_callback(agent.name, "completed", f"{agent.name} 投票完成: {vote}")
            except Exception as e:
                votes[agent.name] = f"投票失败: {e}"
                if progress_callback:
                    await progress_callback(agent.name, "failed", f"{agent.name} 投票失败")

        # 计票：使用同义词映射和模糊匹配归一化后统计各选项票数
        vote_counts: dict[str, int] = {}
        for vote in votes.values():
            normalized = _normalize_vote(vote, options)
            vote_counts[normalized] = vote_counts.get(normalized, 0) + 1

        # 确定胜出选项：得票最多的选项胜出
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
# 不同模式适合不同类型的 Agent 组合：
#   - PARALLEL: 需要不同领域视角的 Agent（知识库+CRM+财务），各维度互补
#   - DEBATE: 需要对立视角的 Agent（知识库+办公助手），促进深度推理
#   - VOTE: 需要独立判断的 Agent（知识库+办公助手+HR），多数决定提高准确率
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

    由 team_factory.create_team() 在 IntentResult 显式指定 orchestration_mode 时调用。
    三种模式互斥，每次只创建一种团队实例。

    Args:
        intent: 意图分类结果，用于自动选择 Agent
        mode: 编排模式，必须显式指定：
              - parallel: 多维度并行收集信息
              - debate: 多角度深度推理验证
              - vote: 多数决定提高准确率
        agent_names: 参与的 Agent 名称列表，为空时使用推荐搭配

    Returns:
        高级编排团队实例（ParallelTeam / DebateTeam / VoteTeam 三者之一）

    Raises:
        ValueError: 不支持的编排模式
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
    """根据意图自动选择编排模式（兜底逻辑）

    当 mode 参数为空时的兜底选择逻辑。
    正常情况下 mode 应由调用方显式指定，此函数仅作为默认值。

    默认选择 VOTE 模式，因为它是开销最小的高级编排方式。

    Args:
        intent: 意图分类结果

    Returns:
        推荐的编排模式
    """
    return AdvancedMode.VOTE


def _select_agents(intent: IntentResult, mode: AdvancedMode) -> list[str]:
    """根据意图和模式选择参与的 Agent

    选择逻辑：
      1. 从 ADVANCED_MODE_AGENTS 获取该模式的默认 Agent 列表
      2. 如果意图分类指定的 target_agent 不在默认列表中，将其添加到列表头部
      3. 最多保留 4 个 Agent，避免 Prompt 过长和 Token 消耗过大

    Args:
        intent: 意图分类结果
        mode: 编排模式

    Returns:
        参与的 Agent 名称列表（最多 4 个）
    """
    base_agents = ADVANCED_MODE_AGENTS.get(mode, ["KnowledgeAgent"])

    # 如果目标 Agent 不在默认列表中，添加到列表
    if intent.target_agent not in base_agents:
        base_agents = [intent.target_agent] + base_agents[:2]

    return base_agents[:4]
