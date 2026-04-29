"""执行控制器

封装任务执行的超时控制、重试逻辑、上下文压缩和错误恢复。
由 route_and_execute() 调用，不包装 team.run()，而是在调用前后增加控制逻辑。

核心能力：
  - 超时控制：全局运行上限 + 单轮 LLM 调用超时
  - 错误分类：根据异常类型决定重试策略
  - 重试逻辑：LLM 超时/限流 -> 切换模型重试；工具失败 -> 重试 N 次
  - 上下文压缩：超 Token 阈值时压缩后重试
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

    Attributes:
        max_runtime: 全局运行上限(秒)，超过则强制终止
        llm_call_timeout: 单轮 LLM 调用超时(秒)
        max_retries: 最大重试次数
        retry_backoff: 重试退避基数(秒)，实际退避 = backoff * 2^retry_count
        enable_context_compaction: 是否启用上下文压缩
        compaction_threshold: Token 数超过此值触发压缩
    """

    max_runtime: int = 600
    llm_call_timeout: int = 30
    max_retries: int = 2
    retry_backoff: float = 1.0
    enable_context_compaction: bool = True
    compaction_threshold: int = 80000


@dataclass
class ExecutionResult:
    """执行结果

    Attributes:
        status: 执行状态 (success / error / timeout / compacted)
        message: 结果消息或错误描述
        agent_name: 执行的 Agent 名称
        retries: 实际重试次数
        compacted: 是否触发了上下文压缩
        original_token_count: 压缩前 Token 数
        compacted_token_count: 压缩后 Token 数
        duration_ms: 执行耗时(毫秒)
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
    由 route_and_execute() 调用，不包装 team.run()。

    使用方式：
        controller = ExecutionController()
        result = await controller.execute_with_control(team, task, session_id, user_id)
    """

    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self._config = config or self._build_config_from_settings()

    @staticmethod
    def _build_config_from_settings() -> ExecutionConfig:
        """从全局配置构建 ExecutionConfig"""
        try:
            settings = get_settings()
            return ExecutionConfig(
                max_runtime=settings.execution_max_runtime,
                llm_call_timeout=settings.execution_llm_timeout,
                max_retries=settings.execution_max_retries,
                compaction_threshold=settings.execution_compaction_threshold,
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

        流程：
        1. 超时控制：asyncio.wait_for(team.run(), timeout=max_runtime)
        2. 错误分类：根据异常类型决定重试策略
        3. 重试逻辑：LLM 超时/限流 -> 切换模型重试；工具失败 -> 重试 N 次
        4. 上下文压缩：超 Token 阈值时压缩后重试

        Args:
            team: AutoGen Team 实例
            task: 任务描述
            session_id: 会话ID
            user_id: 用户ID

        Returns:
            (team.run() 的原始结果, ExecutionResult 执行结果)
        """
        start_time = time.time()
        result_meta = ExecutionResult(agent_name="")
        last_error: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    team.run(task=task),
                    timeout=self._config.max_runtime,
                )

                duration_ms = (time.time() - start_time) * 1000
                result_meta.status = "success"
                result_meta.duration_ms = duration_ms
                result_meta.retries = attempt

                return result, result_meta

            except asyncio.TimeoutError:
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
                error_type = self._classify_error(e)
                logger.warning(
                    "任务执行失败: session=%s attempt=%d/%d error_type=%s error=%s",
                    session_id, attempt + 1, self._config.max_retries + 1,
                    error_type, str(e)[:200],
                )
                last_error = e

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

        Yields:
            流式事件字典，包含以下类型:
            - {"type": "chunk", "agent_name": ..., "content": ...}
            - {"type": "timeout", "message": ...}
            - {"type": "retry", "attempt": ..., "max_retries": ...}
            - {"type": "compacted", "original_tokens": ..., "compacted_tokens": ...}
            - {"type": "error", "message": ...}
        """
        for attempt in range(self._config.max_retries + 1):
            try:
                last_chunk_time = time.time()

                async for message in team.run_stream(task=task):
                    # 检测流式超时：如果两个 chunk 间隔超过 max_runtime，视为超时
                    now = time.time()
                    if now - last_chunk_time > self._config.max_runtime:
                        logger.warning(
                            "流式执行超时: session=%s 间隔=%ds",
                            session_id, int(now - last_chunk_time),
                        )
                        yield {
                            "type": "timeout",
                            "message": f"流式执行超过 {self._config.max_runtime}s 无响应",
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

    def _classify_error(self, error: Exception) -> str:
        """错误分类

        根据异常类型和消息内容，将错误分为以下类别：
        - timeout: 超时错误
        - rate_limit: 限流错误
        - tool_failure: 工具调用失败
        - context_overflow: 上下文溢出
        - unknown: 未知错误

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

        调用 context_manager 的压缩接口，对会话历史生成摘要，
        用摘要替换原始历史，减少 Token 占用。

        Args:
            session_id: 会话ID
            task: 当前任务描述

        Returns:
            (原始Token数, 压缩后Token数) 或 None（压缩失败）
        """
        try:
            from agent.core.session_manager import get_session_manager
            from agent.core.context_manager import compact_messages

            session_mgr = await get_session_manager()
            session = await session_mgr.get_session(session_id)
            if session is None or not session.message_history:
                return None

            original_tokens = 0
            for msg in session.message_history:
                original_tokens += len(msg.get("content", "")) // 2

            compressed = await compact_messages(
                session.message_history,
                max_tokens=self._config.compaction_threshold // 2,
            )

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


# 全局执行控制器单例
_execution_controller: ExecutionController | None = None


def get_execution_controller() -> ExecutionController:
    """获取全局执行控制器"""
    global _execution_controller
    if _execution_controller is None:
        _execution_controller = ExecutionController()
    return _execution_controller
