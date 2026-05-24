"""可观测性模块

提供审计日志、对话反馈、SLA 管理等可观测性能力。
"""

from agent.core.observability.audit import (
    AuditEventType,
    AuditEvent,
    AuditLogger,
    get_audit_logger,
    audit_log,
)
from agent.core.observability.feedback import (
    FeedbackType,
    FeedbackRequest,
    FeedbackStats,
    FeedbackService,
    get_feedback_service,
)
from agent.core.observability.sla import (
    SLATier,
    MetricType,
    SLADefinition,
    MetricSample,
    SLAStatus,
    BenchmarkResult,
    get_sla_definition,
    list_sla_definitions,
    get_budget_for_tier,
    get_budget_config_for_tier,
    get_max_model_tier,
    record_latency,
    record_error,
    get_current_metrics,
    check_sla_compliance,
    run_benchmark,
)

__all__ = [
    "AuditEventType",
    "AuditEvent",
    "AuditLogger",
    "get_audit_logger",
    "audit_log",
    "FeedbackType",
    "FeedbackRequest",
    "FeedbackStats",
    "FeedbackService",
    "get_feedback_service",
    "SLATier",
    "MetricType",
    "SLADefinition",
    "MetricSample",
    "SLAStatus",
    "BenchmarkResult",
    "get_sla_definition",
    "list_sla_definitions",
    "get_budget_for_tier",
    "get_budget_config_for_tier",
    "get_max_model_tier",
    "record_latency",
    "record_error",
    "get_current_metrics",
    "check_sla_compliance",
    "run_benchmark",
]
