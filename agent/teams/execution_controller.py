"""执行控制器

封装任务执行的超时控制、重试逻辑、上下文压缩和错误恢复。

================================================================================
模块职责
================================================================================

执行控制器是任务执行层的核心组件，负责：

1. 超时控制
   - 全局运行上限（默认 600 秒）
   - 单轮 LLM 调用超时（默认 30 秒）
   - 防止任务无限执行

2. 错误分类与重试
   - 根据异常类型决定重试策略
   - LLM 超时/限流 -> 指数退避后重试
   - 工具失败 -> 短暂退避后重试
   - 上下文溢出 -> 压缩后重试

3. 上下文压缩
   - 当 Token 数超过阈值时触发压缩
   - 调用 context_manager 生成摘要
   - 用摘要替换原始历史，减少 Token 占用

4. 熔断器集成
   - 执行前检查熔断器状态
   - 执行成功记录成功
   - 执行失败记录失败

================================================================================
与其他模块的关系
================================================================================

- agent.teams.routing：调用执行控制器执行任务
- agent.core.circuit_breaker：提供熔断器能力
- agent.core.session_manager：提供会话管理能力
- agent.core.context_manager：提供上下文压缩能力
- agent.core.token_budget：提供 Token 预算管理能力

================================================================================
使用方式
================================================================================

    from agent.teams.execution_controller import get_execution_controller

    controller = get_execution_controller()

    # 同步执行
    result, meta = await controller.execute_with_control(
        team=team,
        task="帮我查一下待审批列表",
        session_id="session-123",
        user_id="user-456",
    )

    # 流式执行
    async for event in controller.execute_stream_with_control(
        team=team,
        task="帮我写一封邮件",
        session_id="session-123",
        user_id="user-456",
    ):
        if isinstance(event, dict) and event.get("type") == "chunk":
            print(event["content"], end="")
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ExecutionConfig:
    """执行控制配置

    这些参数控制任务执行的超时、重试和压缩行为。

    Attributes:
        max_runtime: 全局运行上限(秒)
            超过此时间后强制终止任务，防止无限执行
            默认 600 秒（10 分钟）

        llm_call_timeout: 单轮 LLM 调用超时(秒)
            单次 LLM 调用的最大等待时间
            默认 30 秒

        max_retries: 最大重试次数
            任务失败后的最大重试次数
            默认 2 次（共执行 3 次）

        retry_backoff: 重试退避基数(秒)
            重试之间的等待时间基数
            实际退避 = backoff * 2^retry_count（指数退避）
            默认 1.0 秒

        enable_context_compaction: 是否启用上下文压缩
            当 Token 数超过阈值时，自动压缩对话历史
            默认启用

        compaction_threshold: Token 数压缩阈值
            超过此值触发上下文压缩
            默认 80000 Token
    """

    max_runtime: int = 600
    llm_call_timeout: int = 30
    max_retries: int = 2
    retry_backoff: float = 1.0
    enable_context_compaction: bool = True
    compaction_threshold: int = 80000
    stream_idle_timeout: int = 120


@dataclass
class ExecutionResult:
    """执行结果元数据

    记录任务执行的详细状态信息，用于日志、追踪和统计。

    Attributes:
        status: 执行状态
            - success: 执行成功
            - error: 执行失败
            - timeout: 执行超时
            - compacted: 触发了上下文压缩

        message: 结果消息或错误描述
            成功时为空，失败时为错误信息

        agent_name: 执行的 Agent 名称
            用于熔断器标识和日志记录

        retries: 实际重试次数
            0 表示首次执行成功，>0 表示有重试

        compacted: 是否触发了上下文压缩
            True 表示对话历史被压缩过

        original_token_count: 压缩前 Token 数
            仅在 compacted=True 时有意义

        compacted_token_count: 压缩后 Token 数
            仅在 compacted=True 时有意义

        duration_ms: 执行耗时(毫秒)
            从开始执行到结束的总时间
    """

    status: str = "success"
    message: str = ""
    agent_name: str = ""
    retries: int = 0
    compacted: bool = False
    original_token_count: int = 0
    compacted_token_count: int = 0
    duration_ms: float = 0


class ExecutionController:
    """执行控制器

    封装超时、重试、上下文压缩、错误恢复逻辑。
    由 route_and_execute() 调用，不包装 team.run()，而是在调用前后增加控制逻辑。

    核心方法：
    -------------------------------------------------------------------------
    execute_with_control()
        同步执行，等待完整结果后返回

    execute_stream_with_control()
        流式执行，逐 Token 返回中间结果
    -------------------------------------------------------------------------

    执行流程：
    -------------------------------------------------------------------------
    1. 熔断器检查：如果目标 Agent 的熔断器打开，直接返回错误
    2. 超时控制：asyncio.wait_for(team.run(), timeout=max_runtime)
    3. 错误分类：根据异常类型决定重试策略
    4. 重试逻辑：
       - LLM 超时/限流 -> 指数退避后重试
       - 工具失败 -> 短暂退避后重试
    5. 上下文压缩：超 Token 阈值时压缩后重试
    6. 熔断器记录：成功/失败都记录到熔断器
    -------------------------------------------------------------------------
    """

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        """初始化执行控制器

        Args:
            config: 执行控制配置，为空时从全局配置加载
        """
        self._config = config or self._build_config_from_settings()

    @staticmethod
    def _build_config_from_settings() -> ExecutionConfig:
        """从全局配置构建 ExecutionConfig

        从 agent.core.config 的 Settings 中读取配置参数。
        如果读取失败，使用默认值。
        """
        try:
            settings = get_settings()
            return ExecutionConfig(
                max_runtime=settings.execution_max_runtime,
                llm_call_timeout=settings.execution_llm_timeout,
                max_retries=settings.execution_max_retries,
                compaction_threshold=settings.execution_compaction_threshold,
                stream_idle_timeout=settings.execution_stream_idle_timeout,
            )
        except Exception:
            return ExecutionConfig()

    async def execute_with_control(
        self,
        team: Any,
        task: str,
        session_id: str,
        user_id: str,
    ) -> tuple[Any, ExecutionResult]:
        """带控制的同步执行

        执行流程：
        -------------------------------------------------------------------------
        步骤 1：熔断器检查
          - 如果目标 Agent 的熔断器处于 OPEN 状态，直接返回错误
          - 避免向已经故障的服务发送请求

        步骤 2：执行任务
          - 使用 asyncio.wait_for() 包装 team.run()
          - 设置 max_runtime 超时

        步骤 3：错误处理
          - 超时：记录日志，返回 timeout 状态
          - 限流：指数退避后重试
          - 工具失败：短暂退避后重试
          - 上下文溢出：压缩后重试

        步骤 4：记录结果
          - 成功：记录到熔断器成功计数
          - 失败：记录到熔断器失败计数
          - 记录 Token 用量到预算管理器
        -------------------------------------------------------------------------

        Args:
            team: AutoGen Team 实例（SelectorGroupChat / Swarm / AssistantAgent）
            task: 任务描述文本
            session_id: 会话 ID，用于追踪和上下文压缩
            user_id: 用户 ID，用于审计日志

        Returns:
            元组 (team.run() 的原始结果, ExecutionResult 执行结果元数据)
            - 成功时：result 为 TaskResult，ExecutionResult.status = "success"
            - 失败时：result 为 None，ExecutionResult.status = "error"/"timeout"
        """
        start_time = time.time()
        result_meta = ExecutionResult(agent_name="")
        last_error: Exception | None = None

        # 提取 Agent 名称（用于熔断器标识）
        agent_name = self._extract_agent_name(team)

        # 熔断器检查
        # 如果熔断器处于 OPEN 状态，直接拒绝请求
        try:
            from agent.core.circuit_breaker import get_circuit_breaker, CircuitOpenError
            if agent_name:
                cb = get_circuit_breaker(f"agent_{agent_name}")
                if cb.state.value == "open":
                    raise CircuitOpenError(f"agent_{agent_name}", cb.config.recovery_timeout)
        except CircuitOpenError:
            result_meta.status = "error"
            result_meta.message = "服务暂时不可用，请稍后重试"
            result_meta.duration_ms = (time.time() - start_time) * 1000
            logger.warning("熔断器拦截请求: agent=%s", agent_name)
            return None, result_meta
        except Exception:
            pass

        # 重试循环
        for attempt in range(self._config.max_retries + 1):
            try:
                # 执行任务，设置全局超时
                result = await asyncio.wait_for(
                    team.run(task=task),
                    timeout=self._config.max_runtime,
                )

                # 执行成功
                duration_ms = (time.time() - start_time) * 1000
                result_meta.status = "success"
                result_meta.duration_ms = duration_ms
                result_meta.retries = attempt

                # 记录熔断器成功
                await self._record_circuit_success(agent_name)

                # 记录 Token 用量
                await self._record_token_usage(result, agent_name)

                return result, result_meta

            except asyncio.TimeoutError:
                # 超时错误
                logger.warning(
                    "任务执行超时: session=%s attempt=%d/%d timeout=%ds",
                    session_id, attempt + 1, self._config.max_retries + 1,
                    self._config.max_runtime,
                )
                last_error = asyncio.TimeoutError(
                    f"任务执行超过 {self._config.max_runtime}s 上限"
                )
                result_meta.status = "timeout"

            except Exception as e:
                # 其他错误：分类处理
                error_type = self._classify_error(e)
                logger.warning(
                    "任务执行失败: session=%s attempt=%d/%d error_type=%s error=%s",
                    session_id, attempt + 1, self._config.max_retries + 1,
                    error_type, str(e)[:200],
                )
                last_error = e

                # 根据错误类型决定重试策略
                if error_type == "rate_limit" and attempt < self._config.max_retries:
                    # 限流错误：指数退避后重试
                    backoff = self._config.retry_backoff * (2 ** attempt)
                    logger.info("限流退避: %.1fs 后重试", backoff)
                    await asyncio.sleep(backoff)
                    continue

                if error_type == "tool_failure" and attempt < self._config.max_retries:
                    # 工具失败：短暂退避后重试
                    backoff = self._config.retry_backoff
                    logger.info("工具失败退避: %.1fs 后重试", backoff)
                    await asyncio.sleep(backoff)
                    continue

                if error_type == "context_overflow" and self._config.enable_context_compaction:
                    # 上下文溢出：压缩后重试
                    compacted = await self._compact_context(session_id, task)
                    if compacted:
                        result_meta.compacted = True
                        result_meta.status = "compacted"
                        continue

                result_meta.status = "error"
                break

        # 所有重试都失败
        duration_ms = (time.time() - start_time) * 1000
        result_meta.duration_ms = duration_ms
        result_meta.message = str(last_error) if last_error else "未知错误"

        # 记录熔断器失败
        await self._record_circuit_failure(agent_name)

        return None, result_meta

    async def execute_stream_with_control(
        self,
        team: Any,
        task: str,
        session_id: str,
        user_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """带控制的流式执行

        与 execute_with_control 逻辑一致，但使用 team.run_stream()。
        超时控制通过在流式读取中检测间隔实现。

        流式执行的特点：
        - 逐 Token 返回中间结果
        - 用户可以实时看到输出
        - 适用于长文本生成场景

        Yields:
            流式事件字典，包含以下类型：

            1. 内容块事件：
               {"type": "chunk", "agent_name": "...", "content": "..."}

            2. 超时事件：
               {"type": "timeout", "message": "任务执行超时"}

            3. 重试事件：
               {"type": "retry", "attempt": 1, "max_retries": 2}

            4. 压缩事件：
               {"type": "compacted", "original_tokens": 100000, "compacted_tokens": 50000}

            5. 错误事件：
               {"type": "error", "message": "任务执行失败: ..."}
        """
        for attempt in range(self._config.max_retries + 1):
            try:
                last_chunk_time = time.time()

                async for message in team.run_stream(task=task):
                    now = time.time()
                    if now - last_chunk_time > self._config.stream_idle_timeout:
                        logger.warning(
                            "流式执行超时: session=%s 间隔=%ds",
                            session_id, int(now - last_chunk_time),
                        )
                        yield {
                            "type": "timeout",
                            "message": f"流式执行超过 {self._config.stream_idle_timeout}s 无响应",
                        }
                        return

                    last_chunk_time = now
                    yield message

                # 流式执行正常完成
                return

            except asyncio.TimeoutError:
                yield {
                    "type": "timeout",
                    "message": f"任务执行超过 {self._config.max_runtime}s 上限",
                }
                if attempt < self._config.max_retries:
                    yield {
                        "type": "retry",
                        "attempt": attempt + 1,
                        "max_retries": self._config.max_retries,
                    }
                    backoff = self._config.retry_backoff * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue
                return

            except Exception as e:
                error_type = self._classify_error(e)

                if error_type == "rate_limit" and attempt < self._config.max_retries:
                    yield {
                        "type": "retry",
                        "attempt": attempt + 1,
                        "max_retries": self._config.max_retries,
                    }
                    backoff = self._config.retry_backoff * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

                if error_type == "tool_failure" and attempt < self._config.max_retries:
                    yield {
                        "type": "retry",
                        "attempt": attempt + 1,
                        "max_retries": self._config.max_retries,
                    }
                    await asyncio.sleep(self._config.retry_backoff)
                    continue

                if error_type == "context_overflow" and self._config.enable_context_compaction:
                    compacted = await self._compact_context(session_id, task)
                    if compacted:
                        yield {
                            "type": "compacted",
                            "original_tokens": compacted[0],
                            "compacted_tokens": compacted[1],
                        }
                        continue

                yield {
                    "type": "error",
                    "message": f"任务执行失败: {str(e)}",
                }
                return

    def _extract_agent_name(self, team: Any) -> str:
        """从 Team 实例中提取 Agent 名称

        尝试从 team 的 participants 中获取第一个 Agent 的名称。
        用于熔断器标识和日志记录。

        Args:
            team: AutoGen Team 实例

        Returns:
            Agent 名称，提取失败时返回空字符串
        """
        try:
            participants = getattr(team, "participants", None)
            if participants and len(participants) > 0:
                return getattr(participants[0], "name", "")
        except Exception:
            pass
        return ""

    async def _record_circuit_success(self, agent_name: str) -> None:
        """记录熔断器成功

        成功执行后调用，使熔断器从 HALF_OPEN 状态恢复到 CLOSED 状态。

        Args:
            agent_name: Agent 名称
        """
        if not agent_name:
            return
        try:
            from agent.core.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker(f"agent_{agent_name}")
            await cb.record_success()
        except Exception:
            pass

    async def _record_circuit_failure(self, agent_name: str) -> None:
        """记录熔断器失败

        执行失败后调用，增加熔断器的失败计数。
        连续失败达到阈值后，熔断器进入 OPEN 状态。

        Args:
            agent_name: Agent 名称
        """
        if not agent_name:
            return
        try:
            from agent.core.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker(f"agent_{agent_name}")
            await cb.record_failure()
        except Exception:
            pass

    async def _record_token_usage(self, result: Any, agent_name: str) -> None:
        """记录 Token 用量到预算管理器

        从执行结果中提取 Token 用量，记录到 Token 预算管理器。
        用于成本控制和预算追踪。

        Args:
            result: team.run() 的返回结果
            agent_name: Agent 名称
        """
        try:
            usage = getattr(result, "usage", None)
            if usage is None:
                return

            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            if prompt_tokens == 0 and completion_tokens == 0:
                return

            from agent.core.token_budget import get_token_budget_manager
            manager = get_token_budget_manager()
            await manager.record_usage(
                user_id="system",
                session_id="auto",
                model="unknown",
                tier="plus",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                agent_name=agent_name,
            )
        except Exception:
            pass

    def _classify_error(self, error: Exception) -> str:
        """错误分类

        根据异常类型和消息内容，将错误分为以下类别：

        - timeout: 超时错误
          触发条件：asyncio.TimeoutError 或消息中包含 "timeout"

        - rate_limit: 限流错误
          触发条件：消息中包含 "rate limit"、"429"、"throttle"
          处理策略：指数退避后重试

        - tool_failure: 工具调用失败
          触发条件：消息中包含 "tool" + "fail/error" 或 "mcp" + "error"
          处理策略：短暂退避后重试

        - context_overflow: 上下文溢出
          触发条件：消息中包含 "token" + "limit/exceed/overflow" 或 "context_length"
          处理策略：压缩上下文后重试

        - unknown: 未知错误
          处理策略：直接返回失败

        Args:
            error: 异常对象

        Returns:
            错误类别字符串
        """
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()

        if isinstance(error, asyncio.TimeoutError) or "timeout" in error_type:
            return "timeout"

        if "rate" in error_msg and "limit" in error_msg:
            return "rate_limit"
        if "429" in error_msg:
            return "rate_limit"
        if "throttl" in error_msg:
            return "rate_limit"

        if "tool" in error_msg and ("fail" in error_msg or "error" in error_msg):
            return "tool_failure"
        if "mcp" in error_msg and "error" in error_msg:
            return "tool_failure"

        if "token" in error_msg and ("limit" in error_msg or "exceed" in error_msg or "overflow" in error_msg):
            return "context_overflow"
        if "context_length" in error_msg:
            return "context_overflow"
        if "maximum context" in error_msg:
            return "context_overflow"

        return "unknown"

    async def _compact_context(
        self,
        session_id: str,
        task: str,
    ) -> tuple[int, int] | None:
        """上下文压缩

        当对话历史过长导致 Token 溢出时，调用此方法压缩上下文。

        压缩流程：
        -------------------------------------------------------------------------
        1. 从 SessionManager 获取会话历史
        2. 调用 context_manager.compact_messages() 生成摘要
        3. 用摘要替换原始历史
        4. 更新会话状态
        -------------------------------------------------------------------------

        Args:
            session_id: 会话 ID
            task: 当前任务描述（用于生成摘要时的上下文）

        Returns:
            成功时返回 (原始Token数, 压缩后Token数)
            失败时返回 None
        """
        try:
            from agent.core.session_manager import get_session_manager
            from agent.core.context_manager import compact_messages

            session_mgr = await get_session_manager()
            session = await session_mgr.get_session(session_id)
            if session is None or not session.message_history:
                return None

            # 计算原始 Token 数（粗略估计：字符数 / 2）
            original_tokens = 0
            for msg in session.message_history:
                original_tokens += len(msg.get("content", "")) // 2

            # 调用压缩函数
            compressed = await compact_messages(
                session.message_history,
                max_tokens=self._config.compaction_threshold // 2,
            )

            # 计算压缩后 Token 数
            compressed_tokens = 0
            for msg in compressed:
                compressed_tokens += len(msg.get("content", "")) // 2

            # 更新会话历史
            session.message_history = compressed
            await session_mgr.update_session(session)

            logger.info(
                "上下文压缩完成: session=%s %d -> %d tokens",
                session_id, original_tokens, compressed_tokens,
            )

            return (original_tokens, compressed_tokens)

        except Exception as e:
            logger.error("上下文压缩失败: session=%s error=%s", session_id, e)
            return None


    async def execute_step_with_control(
        self,
        team: Any,
        task: str,
        session_id: str,
        user_id: str,
        step_name: str = "",
        step_index: int = -1,
    ) -> tuple[Any, ExecutionResult]:
        """步骤级执行（带控制）

        与 execute_with_control 逻辑一致，但用于任务编排引擎的步骤级执行。
        支持单个Agent或Team实例执行，额外参数用于日志标识和故障隔离追踪。

        Args:
            team: AutoGen Team 实例或单个 Agent 实例
            task: 任务描述文本
            session_id: 会话 ID
            user_id: 用户 ID
            step_name: 步骤名称（用于日志）
            step_index: 步骤索引（用于故障隔离追踪，-1表示未指定）

        Returns:
            元组 (执行结果, ExecutionResult 执行结果元数据)
        """
        step_label = step_name or f"step_{step_index}"
        logger.info("步骤级执行开始: step=%s session=%s", step_label, session_id)
        result, meta = await self.execute_with_control(team, task, session_id, user_id)
        logger.info(
            "步骤级执行完成: step=%s status=%s retries=%d",
            step_label, meta.status, meta.retries,
        )
        return result, meta


# 全局执行控制器单例
# 使用单例模式避免重复创建，确保配置一致性
_execution_controller: ExecutionController | None = None


def get_execution_controller() -> ExecutionController:
    """获取全局执行控制器单例

    首次调用时创建实例，后续调用返回同一实例。

    Returns:
        ExecutionController 实例
    """
    global _execution_controller
    if _execution_controller is None:
        _execution_controller = ExecutionController()
    return _execution_controller
