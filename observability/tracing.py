"""分布式追踪集成

基于 OpenTelemetry 标准，集成 Langfuse Agent 追踪。
"""

import logging

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
      - Span: Agent 调用 / 工具调用
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
            trace = client.trace(id=trace_id, metadata=metadata or {})
            trace.span(
                name=agent_name,
                input={"text": input_text},
                output={"text": output_text},
            )
        except Exception as e:
            logger.error("Langfuse 追踪记录失败: %s", e)


# 全局 Langfuse 追踪器
langfuse_tracer = LangfuseTracer()
