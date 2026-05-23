"""安全与治理模块

Phase 4: 安全与治理
- permission: RBAC 权限模型
- auth: JWT 认证
- desensitize: 数据脱敏（轻量级快速检测）
- guardrails: 安全护栏（编排层，集成注入检测和 PII 深度检测）
- injection_detection: 增强型 Prompt 注入防护（4层防御）
- pii_detection: 扩展型 PII 检测引擎（11种PII + 分级脱敏）
- audit: 审计日志
- sso: SSO 企业身份集成
- encryption: 静态数据加密
- compliance: 合规策略管理
- data_residency: 数据驻留控制
- tenant: 多租户架构
"""

from security.permission import (
    Role,
    ROLE_PERMISSIONS,
    SENSITIVE_ACTIONS,
    PermissionCheckResult,
    check_permission,
    is_sensitive_action,
    get_user_permissions,
)
from security.auth import (
    TokenPayload,
    TokenPair,
    create_token_pair,
    verify_token,
    refresh_access_token,
    extract_token_from_header,
)
from security.desensitize import (
    PIIDetection,
    PII_PATTERNS,
    detect_pii,
    desensitize_content,
    has_pii,
)
from security.guardrails import (
    GuardrailAction,
    GuardrailResult,
    check_input_guardrails,
    check_tool_call_guardrails,
    check_output_guardrails,
)
from security.injection_detection import (
    detect_injection,
    sanitize_input,
    InjectionDetectionResult,
    ThreatLevel,
    DetectionLayer,
)
from security.pii_detection import (
    detect_pii as deep_detect_pii,
    PIIDetectionResult,
    PIICategory,
    PIISensitivity,
    CustomPIIRule,
    add_custom_rule,
    remove_custom_rule,
    list_custom_rules,
)
from security.audit import (
    AuditEvent,
    record_audit,
    record_request_audit,
    record_tool_call_audit,
    record_auth_audit,
    record_guardrail_audit,
)
from security.hallucination_detection import (
    HallucinationDetector,
    HallucinationCheckResult,
    Citation,
)

__all__ = [
    # 权限
    "Role",
    "ROLE_PERMISSIONS",
    "SENSITIVE_ACTIONS",
    "PermissionCheckResult",
    "check_permission",
    "is_sensitive_action",
    "get_user_permissions",
    # 认证
    "TokenPayload",
    "TokenPair",
    "create_token_pair",
    "verify_token",
    "refresh_access_token",
    "extract_token_from_header",
    # 脱敏（轻量级）
    "PIIDetection",
    "PII_PATTERNS",
    "detect_pii",
    "desensitize_content",
    "has_pii",
    # 护栏
    "GuardrailAction",
    "GuardrailResult",
    "check_input_guardrails",
    "check_tool_call_guardrails",
    "check_output_guardrails",
    # 注入检测（增强型4层防御）
    "detect_injection",
    "sanitize_input",
    "InjectionDetectionResult",
    "ThreatLevel",
    "DetectionLayer",
    # PII 深度检测（11种PII + 分级脱敏）
    "deep_detect_pii",
    "PIIDetectionResult",
    "PIICategory",
    "PIISensitivity",
    "CustomPIIRule",
    "add_custom_rule",
    "remove_custom_rule",
    "list_custom_rules",
    # 审计
    "AuditEvent",
    "record_audit",
    "record_request_audit",
    "record_tool_call_audit",
    "record_auth_audit",
    "record_guardrail_audit",
    # 幻觉检测
    "HallucinationDetector",
    "HallucinationCheckResult",
    "Citation",
]
