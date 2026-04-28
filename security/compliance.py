"""合规策略管理

为 SOC 2 Type II / ISO 27001 合规认证提供技术基础：
  - 数据保留策略：定义各类数据的保留期限和自动清理规则
  - 安全策略框架：统一管理安全策略配置和执行
  - 合规报告生成：自动生成合规状态报告

与架构文档安全合规章节对齐，满足企业安全审查要求。
"""

import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DataCategory(str, Enum):
    """数据分类"""

    AUDIT_LOG = "audit_log"
    SESSION_DATA = "session_data"
    CHAT_HISTORY = "chat_history"
    USER_DATA = "user_data"
    TOKEN_DATA = "token_data"
    SYSTEM_LOG = "system_log"
    METRIC_DATA = "metric_data"
    TEMPORARY_DATA = "temporary_data"


class RetentionAction(str, Enum):
    """保留到期后的操作"""

    DELETE = "delete"
    ANONYMIZE = "anonymize"
    ARCHIVE = "archive"
    AGGREGATE = "aggregate"


class DataSensitivity(str, Enum):
    """数据敏感级别"""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RetentionPolicy(BaseModel):
    """数据保留策略

    定义特定数据类型的保留期限和到期操作。
    符合 GDPR 最小化原则和中国数据安全法要求。
    """

    category: DataCategory
    sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    retention_days: int = Field(description="保留天数")
    action: RetentionAction = Field(description="到期操作")
    description: str = Field(default="", description="策略说明")
    legal_basis: str = Field(default="", description="法律依据")
    enabled: bool = True


# 预定义的保留策略（符合 SOC 2 / ISO 27001 要求）
DEFAULT_RETENTION_POLICIES: dict[DataCategory, RetentionPolicy] = {
    DataCategory.AUDIT_LOG: RetentionPolicy(
        category=DataCategory.AUDIT_LOG,
        sensitivity=DataSensitivity.CONFIDENTIAL,
        retention_days=365,
        action=RetentionAction.ARCHIVE,
        description="审计日志保留1年，到期后归档至冷存储",
        legal_basis="SOC 2 CC7.2 / ISO 27001 A.12.4",
    ),
    DataCategory.SESSION_DATA: RetentionPolicy(
        category=DataCategory.SESSION_DATA,
        sensitivity=DataSensitivity.CONFIDENTIAL,
        retention_days=30,
        action=RetentionAction.DELETE,
        description="会话数据保留30天，到期后自动删除",
        legal_basis="数据最小化原则",
    ),
    DataCategory.CHAT_HISTORY: RetentionPolicy(
        category=DataCategory.CHAT_HISTORY,
        sensitivity=DataSensitivity.CONFIDENTIAL,
        retention_days=90,
        action=RetentionAction.ANONYMIZE,
        description="对话历史保留90天，到期后匿名化处理",
        legal_basis="GDPR 第5条(1)(c) / 中国个人信息保护法第19条",
    ),
    DataCategory.USER_DATA: RetentionPolicy(
        category=DataCategory.USER_DATA,
        sensitivity=DataSensitivity.RESTRICTED,
        retention_days=730,
        action=RetentionAction.ANONYMIZE,
        description="用户数据保留2年（劳动合同存续期+1年），到期后匿名化",
        legal_basis="中国劳动合同法第50条 / GDPR 第17条",
    ),
    DataCategory.TOKEN_DATA: RetentionPolicy(
        category=DataCategory.TOKEN_DATA,
        sensitivity=DataSensitivity.RESTRICTED,
        retention_days=7,
        action=RetentionAction.DELETE,
        description="Token 撤销记录保留7天，到期后自动删除",
        legal_basis="安全基线要求",
    ),
    DataCategory.SYSTEM_LOG: RetentionPolicy(
        category=DataCategory.SYSTEM_LOG,
        sensitivity=DataSensitivity.INTERNAL,
        retention_days=90,
        action=RetentionAction.DELETE,
        description="系统日志保留90天，到期后自动删除",
        legal_basis="ISO 27001 A.12.4.1",
    ),
    DataCategory.METRIC_DATA: RetentionPolicy(
        category=DataCategory.METRIC_DATA,
        sensitivity=DataSensitivity.INTERNAL,
        retention_days=180,
        action=RetentionAction.AGGREGATE,
        description="指标数据保留180天，到期后聚合为统计摘要",
        legal_basis="运营分析需要",
    ),
    DataCategory.TEMPORARY_DATA: RetentionPolicy(
        category=DataCategory.TEMPORARY_DATA,
        sensitivity=DataSensitivity.PUBLIC,
        retention_days=1,
        action=RetentionAction.DELETE,
        description="临时数据保留1天，到期后自动删除",
        legal_basis="数据最小化原则",
    ),
}


class SecurityPolicy(BaseModel):
    """安全策略配置

    集中管理所有安全相关配置，支持策略版本管理和审计追踪。
    """

    policy_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "1.0.0"
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_by: str = ""

    password_min_length: int = 12
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_digit: bool = True
    password_require_special: bool = True
    password_max_age_days: int = 90
    password_history_count: int = 5

    mfa_enabled: bool = False
    mfa_methods: list[str] = Field(default_factory=lambda: ["totp", "sms"])

    session_timeout_minutes: int = 60
    session_max_concurrent: int = 5
    session_idle_timeout_minutes: int = 30

    login_max_attempts: int = 5
    login_lockout_minutes: int = 30
    login_notify_on_lockout: bool = True

    encryption_at_rest_enabled: bool = True
    encryption_in_transit_enabled: bool = True
    encryption_algorithm: str = "AES-256-GCM"
    encryption_key_rotation_days: int = 90

    pii_detection_enabled: bool = True
    pii_types: list[str] = Field(default_factory=lambda: [
        "phone", "email", "id_card", "bank_card",
        "passport", "social_security", "name", "address",
    ])
    pii_auto_redact: bool = True

    prompt_injection_detection: bool = True
    prompt_injection_max_score: float = 0.7
    prompt_injection_block_threshold: float = 0.9

    audit_log_all_requests: bool = True
    audit_log_all_tool_calls: bool = True
    audit_log_retention_days: int = 365

    data_residency_regions: list[str] = Field(default_factory=lambda: ["cn-north"])
    data_residency_enforced: bool = True

    api_rate_limit_per_minute: int = 60
    api_rate_limit_burst: int = 100

    ip_whitelist_enabled: bool = False
    ip_whitelist: list[str] = Field(default_factory=list)

    cors_allowed_origins: list[str] = Field(default_factory=list)
    cors_allow_credentials: bool = True


DEFAULT_SECURITY_POLICY = SecurityPolicy()


class ComplianceStatus(BaseModel):
    """合规状态项"""

    control_id: str = Field(description="控制点ID，如 SOC2-CC7.1")
    control_name: str = Field(description="控制点名称")
    category: str = Field(description="合规框架类别: SOC2/ISO27001/GDPR")
    status: str = Field(description="状态: compliant/partial/non-compliant/not-applicable")
    evidence: str = Field(default="", description="合规证据")
    gap: str = Field(default="", description="差距描述")
    remediation: str = Field(default="", description="改进措施")


# SOC 2 Type II 控制点映射
SOC2_CONTROLS: list[ComplianceStatus] = [
    ComplianceStatus(
        control_id="CC6.1",
        control_name="逻辑与物理访问控制",
        category="SOC2",
        status="compliant",
        evidence="JWT认证 + RBAC权限模型 + SSO集成",
    ),
    ComplianceStatus(
        control_id="CC6.2",
        control_name="用户身份认证",
        category="SOC2",
        status="compliant",
        evidence="SSO(Entra ID/Okta/企业微信/钉钉) + MFA策略支持",
    ),
    ComplianceStatus(
        control_id="CC6.3",
        control_name="访问权限管理",
        category="SOC2",
        status="compliant",
        evidence="RBAC + 敏感操作ABAC + 最小权限原则",
    ),
    ComplianceStatus(
        control_id="CC6.7",
        control_name="数据加密",
        category="SOC2",
        status="compliant",
        evidence="传输加密(HTTPS) + 静态加密(AES-256-GCM) + 密钥管理",
    ),
    ComplianceStatus(
        control_id="CC7.1",
        control_name="系统监控与检测",
        category="SOC2",
        status="compliant",
        evidence="Prometheus指标 + OpenTelemetry追踪 + Langfuse审计",
    ),
    ComplianceStatus(
        control_id="CC7.2",
        control_name="事件响应",
        category="SOC2",
        status="partial",
        evidence="审计日志记录完整，事件响应流程待文档化",
        gap="缺少正式的事件响应流程文档和演练记录",
        remediation="建立事件响应SOP，定期进行安全演练",
    ),
    ComplianceStatus(
        control_id="CC7.3",
        control_name="安全事件评估",
        category="SOC2",
        status="partial",
        evidence="安全护栏实时检测，风险等级自动判定",
        gap="缺少安全事件的定期回顾和趋势分析",
        remediation="建立月度安全回顾机制，生成安全态势报告",
    ),
    ComplianceStatus(
        control_id="CC8.1",
        control_name="变更管理",
        category="SOC2",
        status="partial",
        evidence="灰度发布框架 + Agent版本管理",
        gap="缺少完整的变更审批流程和回滚验证",
        remediation="建立变更审批SOP，完善回滚验证自动化",
    ),
]

# ISO 27001 控制点映射
ISO27001_CONTROLS: list[ComplianceStatus] = [
    ComplianceStatus(
        control_id="A.5.15",
        control_name="访问控制",
        category="ISO27001",
        status="compliant",
        evidence="RBAC + ABAC + SSO集成",
    ),
    ComplianceStatus(
        control_id="A.5.33",
        control_name="隐私保护",
        category="ISO27001",
        status="compliant",
        evidence="PII检测 + 自动脱敏 + 数据保留策略",
    ),
    ComplianceStatus(
        control_id="A.8.1",
        control_name="用户终端设备",
        category="ISO27001",
        status="compliant",
        evidence="CORS策略 + CSP头 + 安全中间件",
    ),
    ComplianceStatus(
        control_id="A.8.2",
        control_name="特权访问权限",
        category="ISO27001",
        status="compliant",
        evidence="admin角色最小权限 + 敏感操作二次确认",
    ),
    ComplianceStatus(
        control_id="A.8.10",
        control_name="信息删除",
        category="ISO27001",
        status="compliant",
        evidence="数据保留策略 + 自动清理 + 匿名化",
    ),
    ComplianceStatus(
        control_id="A.8.11",
        control_name="数据脱敏",
        category="ISO27001",
        status="compliant",
        evidence="PII自动脱敏 + 免脱敏角色控制",
    ),
    ComplianceStatus(
        control_id="A.8.12",
        control_name="数据防泄漏",
        category="ISO27001",
        status="partial",
        evidence="输出护栏 + PII检测",
        gap="缺少DLP策略引擎和端点防护",
        remediation="引入DLP策略引擎，完善数据流出控制",
    ),
    ComplianceStatus(
        control_id="A.8.24",
        control_name="密码学",
        category="ISO27001",
        status="compliant",
        evidence="AES-256-GCM加密 + HKDF密钥派生 + 密钥轮换",
    ),
]


class ComplianceManager:
    """合规管理器

    提供合规状态查询、策略管理、报告生成能力。
    """

    def __init__(self):
        self._retention_policies: dict[DataCategory, RetentionPolicy] = dict(DEFAULT_RETENTION_POLICIES)
        self._security_policy: SecurityPolicy = DEFAULT_SECURITY_POLICY.model_copy()

    def get_retention_policy(self, category: DataCategory) -> RetentionPolicy | None:
        """获取数据保留策略"""
        return self._retention_policies.get(category)

    def get_all_retention_policies(self) -> list[RetentionPolicy]:
        """获取所有数据保留策略"""
        return list(self._retention_policies.values())

    def update_retention_policy(self, policy: RetentionPolicy) -> None:
        """更新数据保留策略"""
        self._retention_policies[policy.category] = policy
        logger.info("数据保留策略已更新: category=%s retention_days=%d", policy.category.value, policy.retention_days)

    def get_security_policy(self) -> SecurityPolicy:
        """获取安全策略"""
        return self._security_policy

    def update_security_policy(self, policy: SecurityPolicy, updated_by: str = "") -> None:
        """更新安全策略"""
        policy.updated_at = datetime.now().isoformat()
        policy.updated_by = updated_by
        self._security_policy = policy
        logger.info("安全策略已更新: version=%s updated_by=%s", policy.version, updated_by)

    def get_compliance_status(self, framework: str = "") -> list[ComplianceStatus]:
        """获取合规状态

        Args:
            framework: 合规框架过滤（SOC2/ISO27001/GDPR），空字符串返回全部

        Returns:
            合规状态列表
        """
        all_controls = SOC2_CONTROLS + ISO27001_CONTROLS
        if framework:
            return [c for c in all_controls if c.category == framework]
        return all_controls

    def generate_compliance_report(self) -> dict[str, Any]:
        """生成合规状态报告

        Returns:
            合规报告字典
        """
        all_controls = self.get_compliance_status()

        total = len(all_controls)
        compliant = sum(1 for c in all_controls if c.status == "compliant")
        partial = sum(1 for c in all_controls if c.status == "partial")
        non_compliant = sum(1 for c in all_controls if c.status == "non-compliant")

        gaps = [
            {
                "control_id": c.control_id,
                "control_name": c.control_name,
                "category": c.category,
                "gap": c.gap,
                "remediation": c.remediation,
            }
            for c in all_controls
            if c.status in ("partial", "non-compliant")
        ]

        return {
            "report_id": str(uuid.uuid4()),
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_controls": total,
                "compliant": compliant,
                "partial": partial,
                "non_compliant": non_compliant,
                "compliance_rate": round(compliant / total * 100, 1) if total > 0 else 0,
            },
            "frameworks": {
                "SOC2": {
                    "total": sum(1 for c in SOC2_CONTROLS),
                    "compliant": sum(1 for c in SOC2_CONTROLS if c.status == "compliant"),
                },
                "ISO27001": {
                    "total": sum(1 for c in ISO27001_CONTROLS),
                    "compliant": sum(1 for c in ISO27001_CONTROLS if c.status == "compliant"),
                },
            },
            "gaps": gaps,
            "security_policy_version": self._security_policy.version,
            "retention_policies_count": len(self._retention_policies),
        }

    def check_data_retention(self, category: DataCategory, created_at: float) -> dict[str, Any]:
        """检查数据是否超过保留期限

        Args:
            category: 数据分类
            created_at: 数据创建时间戳

        Returns:
            检查结果
        """
        policy = self._retention_policies.get(category)
        if policy is None:
            return {"expired": False, "action": "none", "reason": "无保留策略"}

        age_days = (time.time() - created_at) / 86400
        if age_days > policy.retention_days:
            return {
                "expired": True,
                "action": policy.action.value,
                "reason": f"数据已超过保留期限({policy.retention_days}天)，当前{age_days:.1f}天",
                "policy": policy.model_dump(),
            }

        remaining_days = policy.retention_days - age_days
        return {
            "expired": False,
            "action": "none",
            "remaining_days": round(remaining_days, 1),
            "policy": policy.model_dump(),
        }


_compliance_manager: ComplianceManager | None = None


def get_compliance_manager() -> ComplianceManager:
    """获取全局合规管理器实例"""
    global _compliance_manager
    if _compliance_manager is None:
        _compliance_manager = ComplianceManager()
    return _compliance_manager
