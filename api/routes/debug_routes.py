"""调试路由

提供执行轨迹查询、Agent 运行统计、意图标签和路由配置查询接口，用于开发调试。
"""

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from observability.tracing import span_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["Debug"])


class SpanResponse(BaseModel):
    """Span 响应"""

    span_id: str = ""
    span_type: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0


class TraceResponse(BaseModel):
    """执行轨迹响应"""

    session_id: str
    spans: list[SpanResponse]
    total: int


class AgentStatsResponse(BaseModel):
    """Agent 运行统计响应"""

    agent_name: str
    call_count: int = 0
    success_count: int = 0
    error_count: int = 0
    avg_duration_ms: float = 0
    success_rate: float = 0


class IntentDefinitionResponse(BaseModel):
    """意图标签定义响应"""

    name: str = Field(..., description="意图标签名称")
    label: str = Field(default="", description="中文标签")
    description: str = Field(default="", description="意图说明")


class IntentExampleResponse(BaseModel):
    """意图分类示例响应"""

    input: str = Field(..., description="用户输入")
    output: str = Field(..., description="期望意图标签")
    reason: str = Field(default="", description="分类原因")


class IntentListResponse(BaseModel):
    """意图标签列表响应"""

    intents: list[IntentDefinitionResponse] = Field(default_factory=list)
    examples: list[IntentExampleResponse] = Field(default_factory=list)
    total: int = 0


class IntentConfigResponse(BaseModel):
    """意图级路由配置响应"""

    intent: str = Field(..., description="意图标签名称")
    mode: str = Field(default="direct", description="协作模式")
    review: bool = Field(default=False, description="是否需要审核")


class CapabilityCardResponse(BaseModel):
    """能力卡片响应"""

    agent_name: str = Field(..., description="Agent 名称")
    description: str = Field(default="", description="Agent 描述")
    version: str = Field(default="1.0.0", description="版本号")
    category: str = Field(default="domain", description="分类")
    supported_intents: list[str] = Field(default_factory=list, description="支持的意图列表")
    intent_configs: list[IntentConfigResponse] = Field(default_factory=list, description="意图级路由配置")
    required_services: list[str] = Field(default_factory=list, description="依赖的MCP服务")
    security_constraints: list[str] = Field(default_factory=list, description="安全约束")
    priority: int = Field(default=0, description="优先级")
    enabled: bool = Field(default=True, description="是否启用")


class RoutingEntryResponse(BaseModel):
    """路由条目响应"""

    intent: str = Field(..., description="意图标签")
    agent: str = Field(..., description="目标 Agent")
    mode: str = Field(default="direct", description="协作模式")
    review: bool = Field(default=False, description="是否需要审核")


class RoutingTableResponse(BaseModel):
    """路由表响应"""

    routes: list[RoutingEntryResponse] = Field(default_factory=list)
    total: int = 0


@router.get("/trace/{session_id}", response_model=TraceResponse, summary="查询会话追踪")
async def get_session_trace(session_id: str) -> TraceResponse:
    """获取会话的完整执行轨迹

    返回该会话的所有 Span，包括意图分类、工具调用、上下文压缩等。
    """
    spans = await span_cache.get_session_spans(session_id)

    span_responses = []
    for s in spans:
        span_responses.append(
            SpanResponse(
                span_id=s.get("span_id", ""),
                span_type=s.get("span_type", ""),
                input=s.get("input", {}),
                output=s.get("output", {}),
                duration_ms=s.get("duration_ms", 0),
                metadata=s.get("metadata", {}),
                timestamp=s.get("timestamp", 0),
            )
        )

    return TraceResponse(
        session_id=session_id,
        spans=span_responses,
        total=len(span_responses),
    )


@router.get("/session/{session_id}/spans", response_model=TraceResponse, summary="查询会话Span列表")
async def get_session_spans(
    session_id: str,
    span_type: str = "",
    limit: int = 100,
) -> TraceResponse:
    """获取会话的 Span 列表（支持分页和类型过滤）

    Args:
        session_id: 会话ID
        span_type: Span 类型过滤（intent_classification / tool_call / context_compaction / agent_call）
        limit: 返回数量上限
    """
    spans = await span_cache.get_session_spans(
        session_id,
        span_type=span_type or None,
        limit=limit,
    )

    span_responses = []
    for s in spans:
        span_responses.append(
            SpanResponse(
                span_id=s.get("span_id", ""),
                span_type=s.get("span_type", ""),
                input=s.get("input", {}),
                output=s.get("output", {}),
                duration_ms=s.get("duration_ms", 0),
                metadata=s.get("metadata", {}),
                timestamp=s.get("timestamp", 0),
            )
        )

    return TraceResponse(
        session_id=session_id,
        spans=span_responses,
        total=len(span_responses),
    )


@router.get("/agent/{agent_name}/stats", response_model=AgentStatsResponse, summary="查询Agent统计")
async def get_agent_stats(agent_name: str) -> AgentStatsResponse:
    """获取 Agent 运行统计

    返回调用次数、平均耗时、成功率等指标。
    """
    stats = await span_cache.get_agent_stats(agent_name)

    call_count = int(stats.get("call_count", 0))
    success_count = int(stats.get("success_count", 0))
    error_count = int(stats.get("error_count", 0))
    avg_duration = float(stats.get("avg_duration_ms", 0))

    success_rate = round(success_count / call_count, 4) if call_count > 0 else 0

    return AgentStatsResponse(
        agent_name=agent_name,
        call_count=call_count,
        success_count=success_count,
        error_count=error_count,
        avg_duration_ms=avg_duration,
        success_rate=success_rate,
    )


@router.get("/intents", response_model=IntentListResponse, summary="查询意图标签列表")
async def get_intent_list() -> IntentListResponse:
    """获取所有意图标签定义和分类示例

    从 IntentClassifier.yaml 中加载结构化的意图标签列表，
    支持前端动态展示意图分类配置。
    """
    from agent.core.prompt.prompt_registry import get_prompt_registry

    registry = get_prompt_registry()
    intents = registry.get_intents()
    examples = registry.get_intent_examples()

    return IntentListResponse(
        intents=[
            IntentDefinitionResponse(name=i.name, label=i.label, description=i.description)
            for i in intents
        ],
        examples=[
            IntentExampleResponse(input=e.input, output=e.output, reason=e.reason)
            for e in examples
        ],
        total=len(intents),
    )


@router.get("/capabilities", response_model=list[CapabilityCardResponse], summary="查询能力卡片列表")
async def get_capability_cards() -> list[CapabilityCardResponse]:
    """获取所有 Agent 的能力卡片

    从 config/capabilities/ 目录加载 YAML 配置，
    返回每个 Agent 的能力声明和意图级路由配置。
    """
    from agent.core.skill.capability_card import get_capability_registry

    registry = get_capability_registry()
    cards = registry.list_all()

    return [
        CapabilityCardResponse(
            agent_name=card.agent_name,
            description=card.description,
            version=card.version,
            category=card.category,
            supported_intents=card.supported_intents,
            intent_configs=[
                IntentConfigResponse(intent=cfg.intent, mode=cfg.mode, review=cfg.review)
                for cfg in card.intent_configs
            ],
            required_services=card.required_services,
            security_constraints=card.security_constraints,
            priority=card.priority,
            enabled=card.enabled,
        )
        for card in cards
    ]


@router.get("/routing", response_model=RoutingTableResponse, summary="查询路由表")
async def get_routing_table() -> RoutingTableResponse:
    """获取完整的意图路由表

    基于 CapabilityRegistry 动态生成路由表，
    展示每个意图对应的 Agent、协作模式和审核要求。
    """
    from agent.core.skill.capability_card import get_capability_registry
    from agent.core.prompt.prompt_registry import get_prompt_registry

    cap_registry = get_capability_registry()
    prompt_registry = get_prompt_registry()
    intents = prompt_registry.get_intents()

    routes = []
    for intent_def in intents:
        routing = cap_registry.get_routing_for_intent(intent_def.name)
        if routing:
            routes.append(RoutingEntryResponse(
                intent=intent_def.name,
                agent=routing["agent"],
                mode=routing["mode"],
                review=routing["review"],
            ))

    return RoutingTableResponse(routes=routes, total=len(routes))
