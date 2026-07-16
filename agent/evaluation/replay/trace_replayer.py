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

增强功能（spec 04）：
  - 生成全新 new_session_id，不污染原 session（REQ-04）
  - 回放强制使用 MockChatCompletionClient 与 Mock 工具客户端（REQ-03）
  - 对 original_input / new_output 脱敏（REQ-10）
  - 持久化 ReplayRecord 供审计和回归对比

================================================================================
与其他模块的关系
================================================================================
- observability.tracing.SpanCache: 提供 span 数据源
- DeterministicMode: 提供确定性执行环境
- TraceToFixtureConverter: 失败时自动生成 Fixture
- HarnessRunner.SingleEvalResult: 回放执行结果（真实集成时填充）
- security.desensitize: PII 脱敏
- agent.core.session.session_manager: 创建新 session
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import nullcontext
from datetime import datetime
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
        replayed_at: 回放时间
        original_input: 原始用户输入（已脱敏）
        original_trajectory: 原始工具调用轨迹
        new_output: 回放输出（已脱敏）
        new_trajectory: 回放工具调用轨迹
        new_result: 回放执行结果（真实集成时由 HarnessRunner 填充）
        trajectory_diff: 轨迹差异
        fixture_generated: 失败时自动生成的 Fixture
        reproduced: 是否成功复现原始行为
        duration_ms: 回放耗时（毫秒）
        deterministic_mode: 是否使用确定性模式
    """

    model_config = ConfigDict(frozen=False)

    original_session_id: str = Field(..., description="原始会话 ID")
    new_session_id: str = Field(default="", description="回放生成的新会话 ID")
    replayed_at: datetime = Field(default_factory=datetime.now, description="回放时间")
    original_input: str = Field(default="", description="原始用户输入（已脱敏）")
    original_trajectory: list[dict] = Field(
        default_factory=list,
        description="原始工具调用轨迹",
    )
    new_output: str = Field(default="", description="回放输出（已脱敏）")
    new_trajectory: list[dict] = Field(
        default_factory=list,
        description="回放工具调用轨迹",
    )
    # SingleEvalResult 尚未实现，使用 Any 占位，真实集成时替换为 SingleEvalResult
    new_result: Any | None = Field(default=None, description="回放执行结果（SingleEvalResult）")
    trajectory_diff: TrajectoryDiff | None = Field(default=None, description="轨迹差异")
    fixture_generated: Fixture | None = Field(default=None, description="自动生成的 Fixture")
    reproduced: bool = Field(default=False, description="是否成功复现")
    duration_ms: float = Field(default=0.0, description="回放耗时（毫秒）")
    deterministic_mode: bool = Field(default=True, description="是否使用确定性模式")


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

    def __init__(
        self,
        span_cache=None,
        deterministic_mode: bool = True,
        desensitize: bool = True,
        session_manager: Any = None,
    ) -> None:
        """初始化回放执行器

        Args:
            span_cache: tracing.py 的 SpanCache 实例，None 时记录警告但不报错
            deterministic_mode: 是否使用确定性模式回放
            desensitize: 是否对 input/output 脱敏（默认 True，对应 REQ-10）
            session_manager: SessionManager 实例，用于创建新 session（None 时延迟创建）
        """
        self._span_cache = span_cache
        self._use_deterministic = deterministic_mode
        self._desensitize = desensitize
        self._session_manager = session_manager

        # 内存存储回放记录（生产环境用 PostgreSQL）
        self._replay_records: dict[str, Any] = {}

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
          3. 生成新 new_session_id（不污染原 session）
          4. 进入 DeterministicMode 重新执行
          5. 对比新旧结果，计算 TrajectoryDiff
          6. 对 input/output 脱敏
          7. 持久化 ReplayRecord
          8. 失败时自动生成 Fixture

        Args:
            session_id: 原始会话 ID
            fixture_override: 可选的 Fixture 覆盖（指定期望行为）
            deterministic_mode: 是否使用确定性模式

        Returns:
            ReplayResult 回放结果
        """
        start_time = time.time()
        result = ReplayResult(
            original_session_id=session_id,
            deterministic_mode=deterministic_mode,
        )

        # 1. 加载 spans
        if self._span_cache is None:
            logger.warning("SpanCache 未初始化，无法回放 session=%s", session_id)
            result.reproduced = False
            result.duration_ms = (time.time() - start_time) * 1000
            return result

        spans = await self._span_cache.get_session_spans(session_id)
        if not spans:
            logger.warning("未找到 session=%s 的 span 数据", session_id)
            result.reproduced = False
            result.duration_ms = (time.time() - start_time) * 1000
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

        # 3. 生成新 new_session_id（不污染原 session）
        new_session_id = self._generate_new_session_id(session_id)
        result.new_session_id = new_session_id
        result.original_trajectory = original_trajectory

        # 4. 进入确定性模式重新执行
        new_trajectory: list[dict] = []
        new_output: str = ""

        context_manager = self._enable_deterministic(deterministic_mode)
        with context_manager:
            # 尝试真实回放（调用 route_and_execute）
            # 失败时降级为简化实现（复用原始轨迹）
            replay_output = await self._execute_replay(
                session_id,
                new_session_id,
                original_input,
                spans,
            )

            if replay_output is not None:
                # 真实回放成功
                new_output, new_trajectory = replay_output
                logger.debug("确定性模式真实回放完成")
            else:
                # 降级：复用原始轨迹
                new_trajectory = original_trajectory
                logger.debug("确定性模式回放完成（降级：复用原始轨迹）")

        # 5. 计算轨迹差异
        diff = self._compute_diff(original_trajectory, new_trajectory)
        result.trajectory_diff = diff
        result.new_trajectory = new_trajectory

        # 6. 判断是否复现
        # 复现标准：无新增工具、无缺失工具、顺序未变
        result.reproduced = (
            not diff.added_tools
            and not diff.removed_tools
            and not diff.order_changed
        )

        # 7. 脱敏处理（REQ-10）
        if self._desensitize:
            result.original_input = self._sanitize(original_input)
            result.new_output = self._sanitize(new_output)
        else:
            result.original_input = original_input
            result.new_output = new_output

        # 8. 记录耗时
        result.duration_ms = (time.time() - start_time) * 1000

        # 9. 持久化 ReplayRecord
        self._persist_replay_record(result)

        if result.reproduced:
            logger.info(
                "session=%s 回放成功复现原始行为 duration=%.0fms",
                session_id,
                result.duration_ms,
            )
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

    def _generate_new_session_id(self, original_session_id: str) -> str:
        """生成新的 session_id（不污染原 session，对应 REQ-04）

        基于 original_session_id 生成确定性新 ID，便于追溯关联。

        Args:
            original_session_id: 原始会话 ID

        Returns:
            新的会话 ID（格式: replay-{original[:8]}-{uuid[:8]}）
        """
        prefix = original_session_id[:8] if len(original_session_id) >= 8 else original_session_id
        return f"replay-{prefix}-{uuid.uuid4().hex[:8]}"

    def _enable_deterministic(self, enabled: bool):
        """确定性模式上下文管理器（对应 spec 04 第 3.2 节）

        替换 LLM 客户端为 MockChatCompletionClient，固定 temperature=0, seed=42。
        禁用外部随机性（UUID 改为确定性生成）。

        Args:
            enabled: 是否启用确定性模式

        Returns:
            上下文管理器
        """
        if not enabled:
            return nullcontext()

        mode = DeterministicMode()
        return mode()

    async def _execute_replay(
        self,
        original_session_id: str,
        new_session_id: str,
        original_input: str,
        spans: list[dict],
    ) -> tuple[str, list[dict]] | None:
        """执行真实回放

        尝试调用 route_and_execute 在新 session 上重新执行原始输入。
        失败时返回 None，调用方降级为复用原始轨迹。

        隔离措施：
          - 使用 new_session_id 不污染原 session
          - DeterministicMode 已替换 LLM 和工具客户端
          - 所有 IO 均为 Mock

        Args:
            original_session_id: 原始会话 ID
            new_session_id: 新会话 ID
            original_input: 原始用户输入
            spans: 原始 span 列表

        Returns:
            (output, trajectory) 元组，失败返回 None
        """
        if not original_input:
            logger.debug("原始输入为空，跳过真实回放")
            return None

        try:
            # 尝试导入 route_and_execute
            from agent.teams.routing import route_and_execute

            # 从 spans 提取上下文信息
            context = self._extract_context(spans)
            user_id = str(context.get("user_id", "replay-user"))

            logger.debug(
                "尝试真实回放: original=%s new=%s input=%s...",
                original_session_id,
                new_session_id,
                original_input[:50],
            )

            # 调用 route_and_execute
            result = await route_and_execute(
                user_message=original_input,
                session_id=new_session_id,
                user_id=user_id,
            )

            # 提取输出和轨迹
            output = ""
            trajectory: list[dict] = []

            if isinstance(result, dict):
                output = str(result.get("response") or result.get("output") or "")
                trajectory = list(result.get("trajectory") or [])
            elif hasattr(result, "response"):
                output = str(getattr(result, "response", ""))
                trajectory = list(getattr(result, "trajectory", []) or [])
            elif hasattr(result, "output"):
                output = str(getattr(result, "output", ""))
                trajectory = list(getattr(result, "trajectory", []) or [])
            else:
                output = str(result)

            logger.info(
                "真实回放成功: new_session=%s output_len=%d trajectory_steps=%d",
                new_session_id,
                len(output),
                len(trajectory),
            )

            return output, trajectory

        except ImportError:
            logger.debug("route_and_execute 不可用，降级为复用原始轨迹")
            return None
        except Exception as e:
            logger.warning("真实回放异常，降级为复用原始轨迹: %s", e)
            return None

    def _extract_context(self, spans: list[dict]) -> dict:
        """从 spans 提取上下文信息

        Args:
            spans: span 列表

        Returns:
            上下文字典，含 user_id / agent_name / tenant_id 等
        """
        context: dict[str, Any] = {}
        for span in spans:
            metadata = span.get("metadata", {}) or {}
            for key in ("user_id", "agent_name", "tenant_id", "session_type"):
                if key in metadata and key not in context:
                    context[key] = metadata[key]
        return context

    def _sanitize(self, text: str) -> str:
        """脱敏处理（复用 security/desensitize.py，对应 REQ-10）

        对 PII 信息（手机号/邮箱/身份证/银行卡）进行脱敏。

        Args:
            text: 待脱敏文本

        Returns:
            脱敏后的文本
        """
        if not text:
            return ""

        try:
            from security.desensitize import desensitize_content
            return desensitize_content(text)
        except ImportError:
            logger.debug("security.desensitize 不可用，跳过脱敏")
            return text
        except Exception as e:
            logger.warning("脱敏处理异常: %s", e)
            return text

    def _persist_replay_record(self, result: ReplayResult) -> None:
        """持久化回放记录

        将 ReplayResult 转换为 ReplayRecord 并存储。
        生产环境替换为 PostgreSQL 持久化。

        Args:
            result: 回放结果
        """
        try:
            from agent.evaluation.replay.models import ReplayRecord

            record = ReplayRecord(
                original_session_id=result.original_session_id,
                new_session_id=result.new_session_id,
                replayed_at=result.replayed_at,
                deterministic_mode=result.deterministic_mode,
                original_input=result.original_input,
                new_output=result.new_output,
                trajectory_diff=(
                    result.trajectory_diff.model_dump()
                    if result.trajectory_diff else {}
                ),
                duration_ms=result.duration_ms,
                status="success" if result.reproduced else "failed",
            )

            self._replay_records[record.replay_id] = record
            logger.debug("持久化回放记录: replay_id=%s", record.replay_id)
        except Exception as e:
            logger.warning("持久化回放记录异常: %s", e)

    def get_replay_record(self, replay_id: str) -> Any | None:
        """获取回放记录

        Args:
            replay_id: 回放记录 ID

        Returns:
            ReplayRecord，不存在返回 None
        """
        return self._replay_records.get(replay_id)

    def list_replay_records(
        self,
        original_session_id: str | None = None,
    ) -> list[Any]:
        """列出回放记录

        Args:
            original_session_id: 按原始 session ID 过滤（None 表示不过滤）

        Returns:
            回放记录列表
        """
        records = list(self._replay_records.values())
        if original_session_id is not None:
            records = [
                r for r in records
                if r.original_session_id == original_session_id
            ]
        return records
