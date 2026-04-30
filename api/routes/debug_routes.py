"""调试路由

提供执行轨迹查询和 Agent 运行统计接口，用于开发调试。
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
