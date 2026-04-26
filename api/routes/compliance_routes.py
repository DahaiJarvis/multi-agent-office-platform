"""合规管理路由

提供合规状态查询、数据保留策略管理、安全策略配置、合规报告生成等 API。
仅限管理员访问。
"""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from security.auth import require_roles
from security.compliance import (
    ComplianceManager,
    DataCategory,
    DataSensitivity,
    RetentionAction,
    RetentionPolicy,
    SecurityPolicy,
    get_compliance_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance", tags=["合规管理"])


class RetentionPolicyResponse(BaseModel):
    """保留策略响应"""

    category: str
    sensitivity: str
    retention_days: int
    action: str
    description: str
    legal_basis: str
    enabled: bool


class RetentionCheckResponse(BaseModel):
    """保留检查响应"""

    expired: bool
    action: str
    reason: str = ""
    remaining_days: float | None = None


class ComplianceStatusResponse(BaseModel):
    """合规状态项响应"""

    control_id: str
    control_name: str
    category: str
    status: str
    evidence: str = ""
    gap: str = ""
    remediation: str = ""


class ComplianceReportResponse(BaseModel):
    """合规报告响应"""

    report_id: str
    generated_at: str
    summary: dict
    frameworks: dict
    gaps: list[dict]
    security_policy_version: str
    retention_policies_count: int


class SecurityPolicyResponse(BaseModel):
    """安全策略响应"""

    policy_id: str
    version: str
    updated_at: str
    password_min_length: int
    mfa_enabled: bool
    session_timeout_minutes: int
    login_max_attempts: int
    encryption_at_rest_enabled: bool
    encryption_algorithm: str
    pii_detection_enabled: bool
    audit_log_all_requests: bool
    data_residency_regions: list[str]
    api_rate_limit_per_minute: int


class UpdateSecurityPolicyRequest(BaseModel):
    """更新安全策略请求"""

    password_min_length: int | None = None
    mfa_enabled: bool | None = None
    session_timeout_minutes: int | None = None
    login_max_attempts: int | None = None
    pii_detection_enabled: bool | None = None
    audit_log_all_requests: bool | None = None


@router.get("/status", response_model=list[ComplianceStatusResponse])
async def get_compliance_status(request: Request, framework: str = "") -> list[ComplianceStatusResponse]:
    """获取合规状态

    返回 SOC 2 / ISO 27001 各控制点的合规状态。
    可通过 framework 参数过滤特定框架。
    """
    require_roles(request, ["admin"])

    manager = get_compliance_manager()
    controls = manager.get_compliance_status(framework=framework)

    return [
        ComplianceStatusResponse(
            control_id=c.control_id,
            control_name=c.control_name,
            category=c.category,
            status=c.status,
            evidence=c.evidence,
            gap=c.gap,
            remediation=c.remediation,
        )
        for c in controls
    ]


@router.get("/report", response_model=ComplianceReportResponse)
async def generate_compliance_report(request: Request) -> ComplianceReportResponse:
    """生成合规状态报告

    包含合规率统计、框架覆盖情况、差距分析和改进建议。
    """
    require_roles(request, ["admin"])

    manager = get_compliance_manager()
    report = manager.generate_compliance_report()

    return ComplianceReportResponse(**report)


@router.get("/retention-policies", response_model=list[RetentionPolicyResponse])
async def get_retention_policies(request: Request) -> list[RetentionPolicyResponse]:
    """获取所有数据保留策略"""
    require_roles(request, ["admin"])

    manager = get_compliance_manager()
    policies = manager.get_all_retention_policies()

    return [
        RetentionPolicyResponse(
            category=p.category.value,
            sensitivity=p.sensitivity.value,
            retention_days=p.retention_days,
            action=p.action.value,
            description=p.description,
            legal_basis=p.legal_basis,
            enabled=p.enabled,
        )
        for p in policies
    ]


@router.post("/retention-check", response_model=RetentionCheckResponse)
async def check_data_retention(
    request: Request,
    category: str,
    created_at: float,
) -> RetentionCheckResponse:
    """检查数据是否超过保留期限"""
    require_roles(request, ["admin"])

    manager = get_compliance_manager()

    if category not in [e.value for e in DataCategory]:
        raise AppException(ErrorCode.INVALID_PARAMETER, message=f"无效的数据分类: {category}")

    result = manager.check_data_retention(DataCategory(category), created_at)
    return RetentionCheckResponse(**result)


@router.get("/security-policy", response_model=SecurityPolicyResponse)
async def get_security_policy(request: Request) -> SecurityPolicyResponse:
    """获取当前安全策略配置"""
    require_roles(request, ["admin"])

    manager = get_compliance_manager()
    policy = manager.get_security_policy()

    return SecurityPolicyResponse(
        policy_id=policy.policy_id,
        version=policy.version,
        updated_at=policy.updated_at,
        password_min_length=policy.password_min_length,
        mfa_enabled=policy.mfa_enabled,
        session_timeout_minutes=policy.session_timeout_minutes,
        login_max_attempts=policy.login_max_attempts,
        encryption_at_rest_enabled=policy.encryption_at_rest_enabled,
        encryption_algorithm=policy.encryption_algorithm,
        pii_detection_enabled=policy.pii_detection_enabled,
        audit_log_all_requests=policy.audit_log_all_requests,
        data_residency_regions=policy.data_residency_regions,
        api_rate_limit_per_minute=policy.api_rate_limit_per_minute,
    )


@router.patch("/security-policy", response_model=SecurityPolicyResponse)
async def update_security_policy(
    request: Request,
    body: UpdateSecurityPolicyRequest,
) -> SecurityPolicyResponse:
    """更新安全策略配置"""
    require_roles(request, ["admin"])

    manager = get_compliance_manager()
    current = manager.get_security_policy()

    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        if hasattr(current, key):
            setattr(current, key, value)

    auth_payload = getattr(request.state, "auth_payload", None)
    updated_by = auth_payload.user_id if auth_payload else "system"

    manager.update_security_policy(current, updated_by=updated_by)

    policy = manager.get_security_policy()
    return SecurityPolicyResponse(
        policy_id=policy.policy_id,
        version=policy.version,
        updated_at=policy.updated_at,
        password_min_length=policy.password_min_length,
        mfa_enabled=policy.mfa_enabled,
        session_timeout_minutes=policy.session_timeout_minutes,
        login_max_attempts=policy.login_max_attempts,
        encryption_at_rest_enabled=policy.encryption_at_rest_enabled,
        encryption_algorithm=policy.encryption_algorithm,
        pii_detection_enabled=policy.pii_detection_enabled,
        audit_log_all_requests=policy.audit_log_all_requests,
        data_residency_regions=policy.data_residency_regions,
        api_rate_limit_per_minute=policy.api_rate_limit_per_minute,
    )
