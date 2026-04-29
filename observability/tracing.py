"""分布式追踪集成

基于 OpenTelemetry 标准，集成 Langfuse Agent 追踪。

细粒度 Span 记录：
  - trace_intent_classification: 意图分类 Span
  - trace_tool_call: 工具调用 Span
  - trace_context_compaction: 上下文压缩 Span

本地 Span 缓存（Redis）：
  - Key: trace:{session_id}
  - Field: span_id
  - Value: JSON {span_type, input, output, duration_ms, timestamp, ...}
  - TTL: 24h
  - 用于调试 API 查询会话执行轨迹
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


def setup_tracing(service_name: str, endpoint: str) -> None:
    """初始化 OpenTelemetry 追踪

    Args:
        service_name: 服务名称
        endpoint: OTLP Exporter 地址
    """
    global _tracer_provider

    resource = Resource.create({"service.name": service_name})
    _tracer_provider = TracerProvider(resource=resource)

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

    Args:
        name: Tracer 名称

    Returns:
        OpenTelemetry Tracer
    """
    return trace.get_tracer(name)


class LangfuseTracer:
    """Langfuse Agent 追踪集成

    将 Agent 调用链路记录到 Langfuse，支持：
      - Trace: 一次完整用户请求
      - Span: Agent 调用 / 工具调用 / 意图分类 / 上下文压缩
      - Score: 质量评分
    """

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        """延迟初始化 Langfuse 客户端"""
        if self._client is None:
            try:
                from langfuse import Langfuse
                from agent.core.config import get_settings

                settings = get_settings()
                self._client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
            except Exception as e:
                logger.warning("Langfuse 初始化失败: %s", e)
        return self._client

    def trace_agent_call(
        self,
        trace_id: str,
        agent_name: str,
        input_text: str,
        output_text: str,
        metadata: dict | None = None,
    ) -> None:
        """记录 Agent 调用

        Args:
            trace_id: 追踪ID
            agent_name: Agent 名称
            input_text: 输入内容
            output_text: 输出内容
            metadata: 附加元数据
        """
        client = self._get_client()
        if client is None:
            return

        try:
            trace_obj = client.trace(id=trace_id, metadata=metadata or {})
            trace_obj.span(
                name=agent_name,
                input={"text": input_text},
                output={"text": output_text},
            )
        except Exception as e:
            logger.error("Langfuse 追踪记录失败: %s", e)

    def trace_intent_classification(
        self,
        trace_id: str,
        user_message: str,
        intent: str,
        confidence: float,
        target_agent: str,
        duration_ms: float,
    ) -> None:
        """记录意图分类 Span

        Args:
            trace_id: 追踪ID
            user_message: 用户输入消息
            intent: 识别出的意图
            confidence: 置信度
            target_agent: 目标 Agent
            duration_ms: 分类耗时(毫秒)
        """
        client = self._get_client()
        if client is None:
            return

        try:
            trace_obj = client.trace(id=trace_id)
            trace_obj.span(
                name="intent_classification",
                input={"user_message": user_message},
                output={
                    "intent": intent,
                    "confidence": confidence,
                    "target_agent": target_agent,
                },
                metadata={"duration_ms": duration_ms},
            )
        except Exception as e:
            logger.error("Langfuse 意图分类追踪失败: %s", e)

    def trace_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        tool_input: dict,
        tool_output: dict | None,
        duration_ms: float,
        status: str,
    ) -> None:
        """记录工具调用 Span

        Args:
            trace_id: 追踪ID
            tool_name: 工具名称
            tool_input: 工具输入参数
            tool_output: 工具输出结果
            duration_ms: 调用耗时(毫秒)
            status: 调用状态 (success / error / timeout)
        """
        client = self._get_client()
        if client is None:
            return

        try:
            trace_obj = client.trace(id=trace_id)
            trace_obj.span(
                name=f"tool_call:{tool_name}",
                input=tool_input,
                output=tool_output or {},
                metadata={"duration_ms": duration_ms, "status": status},
            )
        except Exception as e:
            logger.error("Langfuse 工具调用追踪失败: %s", e)

    def trace_context_compaction(
        self,
        trace_id: str,
        original_tokens: int,
        compacted_tokens: int,
        strategy: str,
    ) -> None:
        """记录上下文压缩 Span

        Args:
            trace_id: 追踪ID
            original_tokens: 压缩前 Token 数
            compacted_tokens: 压缩后 Token 数
            strategy: 压缩策略
        """
        client = self._get_client()
        if client is None:
            return

        try:
            trace_obj = client.trace(id=trace_id)
            trace_obj.span(
                name="context_compaction",
                input={"original_tokens": original_tokens},
                output={"compacted_tokens": compacted_tokens, "strategy": strategy},
                metadata={
                    "compression_ratio": (
                        round(1 - compacted_tokens / original_tokens, 2)
                        if original_tokens > 0
                        else 0
                    ),
                },
            )
        except Exception as e:
            logger.error("Langfuse 上下文压缩追踪失败: %s", e)


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
