"""安全与治理模块

Phase 4: 安全与治理
- permission: RBAC 权限模型
- auth: JWT 认证
- desensitize: 数据脱敏
- guardrails: 安全护栏
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
from security.audit import (
    AuditEvent,
    record_audit,
    record_request_audit,
    record_tool_call_audit,
    record_auth_audit,
    record_guardrail_audit,
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
    # 脱敏
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
    # 审计
    "AuditEvent",
    "record_audit",
    "record_request_audit",
    "record_tool_call_audit",
    "record_auth_audit",
    "record_guardrail_audit",
]
