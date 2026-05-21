"""分布式追踪集成

================================================================================
模块职责
================================================================================
提供基于 OpenTelemetry 标准的分布式追踪能力，包括：
  - OpenTelemetry 初始化和配置
  - Langfuse Agent 追踪集成
  - 细粒度 Span 记录
  - 本地 Span 缓存（Redis）

================================================================================
追踪层级
================================================================================
Trace（追踪）：
  - 代表一次完整的用户请求
  - 包含多个 Span
  - 由 trace_id 唯一标识

Span（跨度）：
  - 代表一个操作单元
  - 包含：开始时间、结束时间、输入、输出、元数据
  - 可以嵌套形成调用链

================================================================================
细粒度 Span 类型
================================================================================
intent_classification: 意图分类
  - 输入：用户消息
  - 输出：意图、置信度、目标 Agent

tool_call: 工具调用
  - 输入：工具名称、参数
  - 输出：执行结果、状态

context_compaction: 上下文压缩
  - 输入：原始上下文
  - 输出：压缩后上下文

agent_call: Agent 调用
  - 输入：用户请求
  - 输出：Agent 响应

================================================================================
本地 Span 缓存
================================================================================
用于调试 API 查询会话执行轨迹：
  - Key: trace:{session_id}
  - Field: span_id
  - Value: JSON {span_type, input, output, duration_ms, timestamp, ...}
  - TTL: 24h

================================================================================
与其他模块的关系
================================================================================
- routing.py: 记录意图分类 Span
- domain.py: 记录 Agent 调用 Span
- mcp_integration.py: 记录工具调用 Span
- execution_controller.py: 记录上下文压缩 Span

================================================================================
使用示例
================================================================================
    # 初始化追踪
    setup_tracing("multi-agent-platform", "localhost:4317")

    # 记录 Agent 调用
    tracer = LangfuseTracer()
    tracer.trace_agent_call(
        trace_id="trace-123",
        agent_name="EmailAgent",
        input_text="帮我发邮件",
        output_text="邮件已发送",
    )

    # 记录意图分类
    tracer.trace_intent_classification(
        trace_id="trace-123",
        user_message="帮我发邮件",
        intent="send_email",
        confidence=0.95,
        target_agent="EmailAgent",
        duration_ms=150,
    )
"""

import json
import logging
import time
import uuid

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

_tracer_provider: TracerProvider | None = None


def setup_tracing(service_name: str, endpoint: str, enabled: bool = True) -> None:
    """初始化 OpenTelemetry 追踪

    配置 OpenTelemetry SDK 并连接到 OTLP Exporter。

    初始化流程：
    -------------------------------------------------------------------------
    1. 创建 Resource（服务标识）
    2. 创建 TracerProvider
    3. 若 enabled=True，配置 OTLP Span Exporter 并添加 BatchSpanProcessor
    4. 设置为全局 TracerProvider
    -------------------------------------------------------------------------

    Args:
        service_name: 服务名称，用于标识追踪来源
        endpoint: OTLP Exporter 地址，格式：host:port
        enabled: 是否启用 OTLP 追踪导出，默认 True。当本地无 Collector 时设为 False 可避免连接报错
    """
    global _tracer_provider

    resource = Resource.create({"service.name": service_name})
    _tracer_provider = TracerProvider(resource=resource)

    if not enabled:
        logger.info("OpenTelemetry 追踪导出已禁用（OTEL_ENABLED=false）")
        trace.set_tracer_provider(_tracer_provider)
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("OpenTelemetry 追踪已启用: endpoint=%s", endpoint)
    except Exception as e:
        logger.warning("OpenTelemetry OTLP Exporter 初始化失败，追踪功能受限: %s", e)

    trace.set_tracer_provider(_tracer_provider)


def get_tracer(name: str = __name__) -> trace.Tracer:
    """获取 Tracer 实例

    用于创建和记录 Span。

    Args:
        name: Tracer 名称，通常使用模块名

    Returns:
        OpenTelemetry Tracer 实例
    """
    return trace.get_tracer(name)


class LangfuseTracer:
    """Langfuse Agent 追踪集成

    将 Agent 调用链路记录到 Langfuse 平台，支持：
      - Trace: 一次完整用户请求
      - Span: Agent 调用 / 工具调用 / 意图分类 / 上下文压缩
      - Score: 质量评分

    基于 Langfuse SDK v4（OpenTelemetry 原生），使用 start_observation API。

    容错机制：
      - 初始化失败时自动禁用，不影响主流程
      - 连续失败超过阈值自动熔断，定期尝试恢复
      - 所有追踪方法均为 fire-and-forget，不阻塞业务逻辑

    使用方式：
        tracer = LangfuseTracer()
        tracer.trace_agent_call(trace_id, agent_name, input_text, output_text)
    """

    _MAX_CONSECUTIVE_FAILURES = 5
    _CIRCUIT_RESET_SECONDS = 60

    def __init__(self) -> None:
        self._client = None
        self._initialized = False
        self._disabled = False
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _get_client(self):
        """延迟初始化 Langfuse 客户端

        首次调用时创建客户端，避免启动时依赖问题。
        初始化失败或密钥未配置时自动禁用，后续调用直接返回 None。
        """
        if self._disabled:
            return None

        if self._client is not None:
            return self._client

        if self._initialized:
            return None

        self._initialized = True

        try:
            from agent.core.config import get_settings

            settings = get_settings()

            if not settings.langfuse_public_key or not settings.langfuse_secret_key:
                logger.debug("Langfuse 密钥未配置，追踪功能禁用")
                self._disabled = True
                return None

            import os
            os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
            os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
            os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)

            from langfuse import Langfuse

            client = Langfuse()

            if not hasattr(client, "start_observation"):
                logger.warning("Langfuse SDK 版本不兼容，缺少 start_observation 方法，追踪功能禁用")
                self._disabled = True
                return None

            self._client = client
            logger.info("Langfuse 客户端初始化成功")
        except ImportError:
            logger.debug("Langfuse SDK 未安装，追踪功能禁用")
            self._disabled = True
        except Exception as e:
            logger.warning("Langfuse 初始化失败，追踪功能禁用: %s", e)
            self._disabled = True

        return self._client

    def _is_circuit_open(self) -> bool:
        """检查熔断器是否开启

        连续失败次数超过阈值时打开熔断器，阻止后续请求。
        熔断器打开后，每隔一段时间允许一次探测请求以检测服务是否恢复。
        """
        if self._consecutive_failures < self._MAX_CONSECUTIVE_FAILURES:
            return False

        now = time.time()
        if now >= self._circuit_open_until:
            logger.info("Langfuse 熔断器半开，尝试恢复")
            return False

        return True

    def _on_success(self) -> None:
        """追踪成功，重置失败计数"""
        self._consecutive_failures = 0

    def _on_failure(self) -> None:
        """追踪失败，累计失败计数，达到阈值时打开熔断器"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES:
            self._circuit_open_until = time.time() + self._CIRCUIT_RESET_SECONDS
            logger.warning(
                "Langfuse 连续失败 %d 次，熔断器打开，%ds 后重试",
                self._consecutive_failures,
                self._CIRCUIT_RESET_SECONDS,
            )

    def _to_compliant_trace_id(self, trace_id: str) -> str:
        """将任意 trace_id 转换为 Langfuse 要求的 32 位小写十六进制格式"""
        import re
        if re.fullmatch(r"[0-9a-f]{32}", trace_id):
            return trace_id
        import hashlib
        return hashlib.md5(trace_id.encode()).hexdigest()

    def _record_span(self, trace_id: str, span_name: str, span_input: dict, span_output: dict, metadata: dict | None = None) -> None:
        """记录 Span 到 Langfuse

        使用 v4 SDK 的 start_observation API，通过 TraceContext 关联到指定 trace。
        """
        client = self._get_client()
        if client is None:
            return

        if self._is_circuit_open():
            return

        try:
            from langfuse.types import TraceContext

            compliant_trace_id = self._to_compliant_trace_id(trace_id)
            obs = client.start_observation(
                name=span_name,
                as_type="span",
                trace_context=TraceContext(trace_id=compliant_trace_id),
                input=span_input,
                output=span_output,
                metadata=metadata or {},
            )
            obs.end()
            self._on_success()
        except Exception as e:
            self._on_failure()
            logger.debug("Langfuse 追踪记录失败: %s", e)

    def trace_agent_call(
        self,
        trace_id: str,
        agent_name: str,
        input_text: str,
        output_text: str,
        metadata: dict | None = None,
    ) -> None:
        """记录 Agent 调用"""
        self._record_span(
            trace_id=trace_id,
            span_name=agent_name,
            span_input={"text": input_text},
            span_output={"text": output_text},
            metadata=metadata,
        )

    def trace_intent_classification(
        self,
        trace_id: str,
        user_message: str,
        intent: str,
        confidence: float,
        target_agent: str,
        duration_ms: float,
    ) -> None:
        """记录意图分类 Span"""
        self._record_span(
            trace_id=trace_id,
            span_name="intent_classification",
            span_input={"user_message": user_message},
            span_output={
                "intent": intent,
                "confidence": confidence,
                "target_agent": target_agent,
            },
            metadata={"duration_ms": duration_ms},
        )

    def trace_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        tool_input: dict,
        tool_output: dict | None,
        duration_ms: float,
        status: str,
    ) -> None:
        """记录工具调用 Span"""
        self._record_span(
            trace_id=trace_id,
            span_name=f"tool_call:{tool_name}",
            span_input=tool_input,
            span_output=tool_output or {},
            metadata={"duration_ms": duration_ms, "status": status},
        )

    def trace_context_compaction(
        self,
        trace_id: str,
        original_tokens: int,
        compacted_tokens: int,
        strategy: str,
    ) -> None:
        """记录上下文压缩 Span"""
        self._record_span(
            trace_id=trace_id,
            span_name="context_compaction",
            span_input={"original_tokens": original_tokens},
            span_output={"compacted_tokens": compacted_tokens, "strategy": strategy},
            metadata={
                "compression_ratio": (
                    round(1 - compacted_tokens / original_tokens, 2)
                    if original_tokens > 0
                    else 0
                ),
            },
        )


class SpanCache:
    """本地 Span 缓存

    将 Span 数据缓存到 Redis，用于调试 API 查询会话执行轨迹。

    存储结构：
      - Key: trace:{session_id}  (Redis Hash)
      - Field: span_id
      - Value: JSON {span_type, input, output, duration_ms, timestamp, ...}
      - TTL: 24h
    """

    def __init__(self) -> None:
        self._redis = None

    async def _get_redis(self):
        """获取 Redis 连接"""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                from agent.core.config import get_settings

                settings = get_settings()
                self._redis = aioredis.from_url(
                    settings.redis_url, decode_responses=True
                )
            except Exception as e:
                logger.warning("SpanCache Redis 连接失败: %s", e)
        return self._redis

    async def store_span(
        self,
        session_id: str,
        span_type: str,
        input_data: dict | None = None,
        output_data: dict | None = None,
        duration_ms: float = 0,
        metadata: dict | None = None,
    ) -> str:
        """存储 Span 到缓存

        Args:
            session_id: 会话ID
            span_type: Span 类型 (intent_classification / tool_call / context_compaction / agent_call)
            input_data: 输入数据
            output_data: 输出数据
            duration_ms: 耗时(毫秒)
            metadata: 附加元数据

        Returns:
            span_id
        """
        redis = await self._get_redis()
        if redis is None:
            return ""

        span_id = str(uuid.uuid4())[:12]
        span_data = {
            "span_id": span_id,
            "span_type": span_type,
            "input": input_data or {},
            "output": output_data or {},
            "duration_ms": duration_ms,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }

        key = f"trace:{session_id}"
        try:
            await redis.hset(key, span_id, json.dumps(span_data, ensure_ascii=False))
            await redis.expire(key, 86400)
        except Exception as e:
            logger.warning("SpanCache 存储失败: %s", e)

        return span_id

    async def get_session_spans(
        self,
        session_id: str,
        span_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """获取会话的所有 Span

        Args:
            session_id: 会话ID
            span_type: 按 Span 类型过滤（可选）
            limit: 返回数量上限

        Returns:
            Span 列表，按时间戳排序
        """
        redis = await self._get_redis()
        if redis is None:
            return []

        key = f"trace:{session_id}"
        try:
            raw_spans = await redis.hgetall(key)
        except Exception as e:
            logger.warning("SpanCache 读取失败: %s", e)
            return []

        spans = []
        for span_json in raw_spans.values():
            try:
                span = json.loads(span_json)
                if span_type and span.get("span_type") != span_type:
                    continue
                spans.append(span)
            except (json.JSONDecodeError, TypeError):
                continue

        spans.sort(key=lambda s: s.get("timestamp", 0))
        return spans[:limit]

    async def get_agent_stats(self, agent_name: str) -> dict:
        """获取 Agent 运行统计

        从 Redis 中的 agent_stats:{agent_name} 读取统计数据。

        Args:
            agent_name: Agent 名称

        Returns:
            统计数据字典
        """
        redis = await self._get_redis()
        if redis is None:
            return {}

        key = f"agent_stats:{agent_name}"
        try:
            data = await redis.hgetall(key)
            if data:
                return {k: float(v) if "." in v else int(v) for k, v in data.items()}
        except Exception as e:
            logger.warning("SpanCache 读取 Agent 统计失败: %s", e)

        return {}

    async def increment_agent_stats(
        self,
        agent_name: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        """更新 Agent 运行统计

        Args:
            agent_name: Agent 名称
            duration_ms: 执行耗时(毫秒)
            success: 是否成功
        """
        redis = await self._get_redis()
        if redis is None:
            return

        key = f"agent_stats:{agent_name}"
        try:
            await redis.hincrby(key, "call_count", 1)
            if success:
                await redis.hincrby(key, "success_count", 1)
            else:
                await redis.hincrby(key, "error_count", 1)

            # 更新平均耗时（滑动平均）
            current_avg = float(await redis.hget(key, "avg_duration_ms") or 0)
            current_count = int(await redis.hget(key, "call_count") or 1)
            new_avg = (current_avg * (current_count - 1) + duration_ms) / current_count
            await redis.hset(key, "avg_duration_ms", round(new_avg, 2))

            await redis.expire(key, 86400 * 30)
        except Exception as e:
            logger.warning("SpanCache 更新 Agent 统计失败: %s", e)


# 全局 Langfuse 追踪器
langfuse_tracer = LangfuseTracer()

# 全局 Span 缓存
span_cache = SpanCache()
