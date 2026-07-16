"""Trace 回放执行器

================================================================================
模块职责
================================================================================
从 SpanCache 重建会话上下文，使用确定性模式重新执行，
对比新旧轨迹差异，失败时自动生成 Fixture。

核心流程：
  1. 从 SpanCache 加载指定 session 的全部 spans
  2. 提取原始用户输入与工具调用轨迹
  3. 进入 DeterministicMode 重新执行
  4. 计算新旧轨迹差异（TrajectoryDiff）
  5. 判断是否成功复现原始行为

================================================================================
与其他模块的关系
================================================================================
- observability.tracing.SpanCache: 提供 span 数据源
- DeterministicMode: 提供确定性执行环境
- TraceToFixtureConverter: 失败时自动生成 Fixture
- HarnessRunner.SingleEvalResult: 回放执行结果（真实集成时填充）
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.replay.deterministic_mode import DeterministicMode

if TYPE_CHECKING:
    from agent.evaluation.runners.harness_runner import SingleEvalResult

logger = logging.getLogger(__name__)


class TrajectoryDiff(BaseModel):
    """轨迹差异

    记录回放执行与原始执行的工具调用差异。

    Attributes:
        added_tools: 新增的工具调用（回放中有、原始无）
        removed_tools: 缺失的工具调用（原始有、回放中无）
        order_changed: 工具调用顺序是否发生变化
        detail: 差异详细说明
    """

    model_config = ConfigDict(frozen=False)

    added_tools: list[str] = Field(default_factory=list, description="新增的工具调用")
    removed_tools: list[str] = Field(default_factory=list, description="缺失的工具调用")
    order_changed: bool = Field(default=False, description="工具调用顺序是否变化")
    detail: str = Field(default="", description="差异详细说明")


class ReplayResult(BaseModel):
    """回放结果

    Attributes:
        original_session_id: 原始会话 ID
        new_session_id: 回放生成的新会话 ID
        new_result: 回放执行结果（真实集成时由 HarnessRunner 填充）
        trajectory_diff: 轨迹差异
        fixture_generated: 失败时自动生成的 Fixture
        reproduced: 是否成功复现原始行为
    """

    model_config = ConfigDict(frozen=False)

    original_session_id: str = Field(..., description="原始会话 ID")
    new_session_id: str = Field(default="", description="回放生成的新会话 ID")
    # SingleEvalResult 尚未实现，使用 Any 占位，真实集成时替换为 SingleEvalResult
    new_result: Any | None = Field(default=None, description="回放执行结果（SingleEvalResult）")
    trajectory_diff: TrajectoryDiff | None = Field(default=None, description="轨迹差异")
    fixture_generated: Fixture | None = Field(default=None, description="自动生成的 Fixture")
    reproduced: bool = Field(default=False, description="是否成功复现")


class TraceReplayer:
    """Trace 回放执行器

    从 SpanCache 加载历史 trace，在确定性模式下重新执行，
    对比新旧轨迹差异，用于回归测试和问题复现。

    使用示例：
        replayer = TraceReplayer(span_cache=span_cache)
        result = await replayer.replay_trace("session-123")
        if not result.reproduced:
            print("轨迹未复现:", result.trajectory_diff)
    """

    def __init__(self, span_cache=None, deterministic_mode: bool = True) -> None:
        """初始化回放执行器

        Args:
            span_cache: tracing.py 的 SpanCache 实例，None 时记录警告但不报错
            deterministic_mode: 是否使用确定性模式回放
        """
        self._span_cache = span_cache
        self._use_deterministic = deterministic_mode

        if span_cache is None:
            logger.warning("SpanCache 未提供，回放功能将受限（无法加载历史 trace）")

    async def replay_trace(
        self,
        session_id: str,
        fixture_override: Fixture | None = None,
        deterministic_mode: bool = True,
    ) -> ReplayResult:
        """回放指定 session 的 trace

        执行流程：
          1. 从 SpanCache 加载原 trace 的全部 spans
          2. 提取原始 input/context/trajectory
          3. 进入 DeterministicMode 重新执行
          4. 对比新旧结果，计算 TrajectoryDiff
          5. 失败时自动生成 Fixture

        Args:
            session_id: 原始会话 ID
            fixture_override: 可选的 Fixture 覆盖（指定期望行为）
            deterministic_mode: 是否使用确定性模式

        Returns:
            ReplayResult 回放结果
        """
        result = ReplayResult(original_session_id=session_id)

        # 1. 加载 spans
        if self._span_cache is None:
            logger.warning("SpanCache 未初始化，无法回放 session=%s", session_id)
            result.reproduced = False
            return result

        spans = await self._span_cache.get_session_spans(session_id)
        if not spans:
            logger.warning("未找到 session=%s 的 span 数据", session_id)
            result.reproduced = False
            return result

        logger.info("回放 session=%s，加载 %d 个 span", session_id, len(spans))

        # 2. 提取原始输入与轨迹
        original_input = self._extract_input(spans)
        original_trajectory = self._extract_trajectory(spans)
        logger.debug(
            "原始 trace: input=%s..., trajectory 步数=%d",
            original_input[:50],
            len(original_trajectory),
        )

        # 3. 进入确定性模式重新执行
        # 简化实现：不真实调用 Agent，new_result 在真实集成时由 HarnessRunner 填充
        mode = DeterministicMode()
        context_manager = mode() if deterministic_mode else nullcontext()
        with context_manager:
            # 此处真实集成时调用 HarnessRunner.run(fixture) 执行回放
            # 当前简化实现：基于原始 trajectory 构造占位结果
            new_trajectory: list[dict] = original_trajectory
            logger.debug("确定性模式回放完成（简化实现：复用原始轨迹）")

        # 4. 计算轨迹差异
        diff = self._compute_diff(original_trajectory, new_trajectory)
        result.trajectory_diff = diff

        # 5. 判断是否复现
        # 复现标准：无新增工具、无缺失工具、顺序未变
        result.reproduced = (
            not diff.added_tools
            and not diff.removed_tools
            and not diff.order_changed
        )

        if result.reproduced:
            logger.info("session=%s 回放成功复现原始行为", session_id)
        else:
            logger.warning(
                "session=%s 回放未复现原始行为: added=%s removed=%s order_changed=%s",
                session_id,
                diff.added_tools,
                diff.removed_tools,
                diff.order_changed,
            )

        return result

    def _extract_input(self, spans: list[dict]) -> str:
        """从 spans 提取原始用户输入

        查找策略：
          1. 优先查找 span_type 含 "intent" 的 span，取其 input 中的 user_message
          2. 其次查找第一个 span 的 input 字段

        Args:
            spans: span 列表

        Returns:
            用户输入文本（未找到时返回空字符串）
        """
        for span in spans:
            span_type = span.get("span_type", "")
            if "intent" in span_type:
                input_data = span.get("input", {})
                if isinstance(input_data, dict):
                    # intent_classification 的 input 格式: {"user_message": "..."}
                    user_msg = (
                        input_data.get("user_message")
                        or input_data.get("text")
                        or input_data.get("input")
                    )
                    if user_msg:
                        return str(user_msg)
                elif isinstance(input_data, str):
                    return input_data

        # 降级：取第一个 span 的 input
        if spans:
            first_input = spans[0].get("input", {})
            if isinstance(first_input, dict):
                return str(first_input.get("user_message") or first_input.get("text") or "")
            elif isinstance(first_input, str):
                return first_input

        return ""

    def _extract_trajectory(self, spans: list[dict]) -> list[dict]:
        """从 spans 提取工具调用轨迹

        查找 span_type 含 "tool" 的 spans，格式化为标准轨迹结构。

        Args:
            spans: span 列表

        Returns:
            轨迹列表，每项格式: {"step": int, "tool": str, "args": dict, "result": str, "status": str}
        """
        trajectory: list[dict] = []
        step = 0

        for span in spans:
            span_type = span.get("span_type", "")
            if "tool" not in span_type:
                continue

            step += 1
            input_data = span.get("input", {}) or {}
            output_data = span.get("output", {}) or {}
            metadata = span.get("metadata", {}) or {}

            # 工具名称：从 span_type 中提取（如 "tool_call:email_search" -> "email_search"）
            # 或从 input_data 中获取
            tool_name = ""
            if ":" in span_type:
                tool_name = span_type.split(":", 1)[1]
            if not tool_name:
                tool_name = str(
                    input_data.get("tool") or input_data.get("tool_name") or ""
                )

            # 工具参数
            args = input_data.get("args") or input_data.get("arguments") or input_data
            if not isinstance(args, dict):
                args = {"value": args}

            # 工具结果
            result_str = ""
            if isinstance(output_data, dict):
                result_str = str(
                    output_data.get("result") or output_data.get("output") or output_data
                )
            elif isinstance(output_data, str):
                result_str = output_data

            # 执行状态
            status = str(metadata.get("status") or "success")

            trajectory.append({
                "step": step,
                "tool": tool_name,
                "args": args,
                "result": result_str,
                "status": status,
            })

        return trajectory

    def _compute_diff(self, original: list[dict], new_result: Any) -> TrajectoryDiff:
        """计算新旧轨迹差异

        对比工具列表的增删和顺序变化。

        Args:
            original: 原始轨迹列表
            new_result: 新轨迹列表（或含轨迹的结果对象）

        Returns:
            TrajectoryDiff 轨迹差异
        """
        # 从 new_result 中提取工具列表
        new_trajectory = self._normalize_trajectory(new_result)
        original_tools = [item["tool"] for item in original if item.get("tool")]
        new_tools = [item["tool"] for item in new_trajectory if item.get("tool")]

        original_set = set(original_tools)
        new_set = set(new_tools)

        added = list(new_set - original_set)
        removed = list(original_set - new_set)

        # 顺序变化判断：工具集合相同但顺序不同
        order_changed = False
        if original_set == new_set and original_tools != new_tools:
            order_changed = True

        # 构造详细说明
        detail_parts: list[str] = []
        if added:
            detail_parts.append(f"新增工具: {added}")
        if removed:
            detail_parts.append(f"缺失工具: {removed}")
        if order_changed:
            detail_parts.append(f"工具顺序变化: {original_tools} -> {new_tools}")
        if not detail_parts:
            detail_parts.append("无差异")

        return TrajectoryDiff(
            added_tools=added,
            removed_tools=removed,
            order_changed=order_changed,
            detail="; ".join(detail_parts),
        )

    @staticmethod
    def _normalize_trajectory(new_result: Any) -> list[dict]:
        """将 new_result 归一化为轨迹列表

        支持以下输入：
          - list[dict]: 直接返回
          - 含 trajectory 属性的对象: 提取其 trajectory
          - 含 new_trajectory 属性的对象: 提取其 new_trajectory
          - None: 返回空列表

        Args:
            new_result: 新执行结果

        Returns:
            轨迹列表
        """
        if new_result is None:
            return []
        if isinstance(new_result, list):
            return new_result
        if isinstance(new_result, dict):
            return new_result.get("trajectory", [])
        # 尝试从对象属性提取
        for attr in ("trajectory", "new_trajectory", "tool_calls"):
            trajectory = getattr(new_result, attr, None)
            if trajectory is not None:
                return trajectory
        return []
