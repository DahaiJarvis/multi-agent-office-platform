"""RBAC 权限模型

基于角色的访问控制（RBAC）+ 敏感操作的属性检查（ABAC）。
角色定义与权限校验逻辑，与架构文档 7.2.2 节对齐。
"""

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """系统角色"""

    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    HR_SPECIALIST = "hr_specialist"
    FINANCE = "finance"


# 角色与权限映射
ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.ADMIN: ["*"],
    Role.MANAGER: ["approval:*", "crm:read", "email:send", "calendar:*"],
    Role.EMPLOYEE: ["approval:read_own", "crm:read_own", "email:send_own", "calendar:*"],
    Role.HR_SPECIALIST: ["hr:*", "approval:read", "employee:read"],
    Role.FINANCE: ["finance:*", "approval:read"],
}

# 敏感操作定义：需要特定角色和/或二次确认
SENSITIVE_ACTIONS: dict[str, dict[str, Any]] = {
    "approval:approve": {"require_role": [Role.MANAGER, Role.ADMIN], "require_confirm": True},
    "finance:transfer": {"require_role": [Role.FINANCE, Role.ADMIN], "require_confirm": True, "amount_limit": 50000},
    "email:send_all": {"require_role": [Role.ADMIN], "require_confirm": True},
    "crm:export": {"require_role": [Role.MANAGER, Role.ADMIN], "require_confirm": False},
    "data:delete": {"require_role": [Role.ADMIN], "require_confirm": True},
    "data:export": {"require_role": [Role.MANAGER, Role.ADMIN], "require_confirm": True},
    "approval:reject": {"require_role": [Role.MANAGER, Role.ADMIN], "require_confirm": True},
    "finance:submit": {"require_role": [Role.FINANCE, Role.ADMIN], "require_confirm": True},
    "system:config_update": {"require_role": [Role.ADMIN], "require_confirm": True},
}


class PermissionCheckResult(BaseModel):
    """权限校验结果"""

    allowed: bool
    reason: str = ""
    require_confirm: bool = False
    sensitive: bool = False


def check_permission(user_role: str, action: str) -> PermissionCheckResult:
    """校验用户是否有权执行指定操作

    采用 RBAC 模型：先检查角色权限，再检查敏感操作约束。

    Args:
        user_role: 用户角色
        action: 操作标识，格式为 "资源:操作"，如 "approval:approve"

    Returns:
        PermissionCheckResult 校验结果
    """
    if not user_role or not action:
        return PermissionCheckResult(allowed=False, reason="角色或操作不能为空")

    # 获取角色权限列表
    role_perms = ROLE_PERMISSIONS.get(user_role, [])
    if not role_perms:
        return PermissionCheckResult(allowed=False, reason=f"未知角色: {user_role}")

    # 管理员拥有全部权限
    if "*" in role_perms:
        return _check_sensitive_action(user_role, action)

    # 检查精确匹配
    if action in role_perms:
        return _check_sensitive_action(user_role, action)

    # 检查通配符匹配（如 approval:* 匹配 approval:approve）
    action_prefix = action.split(":")[0] + ":*"
    if action_prefix in role_perms:
        return _check_sensitive_action(user_role, action)

    return PermissionCheckResult(
        allowed=False,
        reason=f"角色 {user_role} 无权执行操作 {action}",
    )


def _check_sensitive_action(user_role: str, action: str) -> PermissionCheckResult:
    """检查敏感操作的额外约束

    Args:
        user_role: 用户角色
        action: 操作标识

    Returns:
        PermissionCheckResult
    """
    sensitive_config = SENSITIVE_ACTIONS.get(action)
    if sensitive_config is None:
        return PermissionCheckResult(allowed=True)

    # 检查敏感操作的角色要求
    require_roles = sensitive_config.get("require_role", [])
    if require_roles and user_role not in require_roles:
        return PermissionCheckResult(
            allowed=False,
            reason=f"操作 {action} 需要角色: {require_roles}",
            sensitive=True,
        )

    require_confirm = sensitive_config.get("require_confirm", False)
    return PermissionCheckResult(
        allowed=True,
        require_confirm=require_confirm,
        sensitive=True,
    )


def is_sensitive_action(action: str) -> bool:
    """判断操作是否为敏感操作

    Args:
        action: 操作标识

    Returns:
        是否为敏感操作
    """
    return action in SENSITIVE_ACTIONS


def get_user_permissions(user_role: str) -> list[str]:
    """获取用户角色的所有权限

    Args:
        user_role: 用户角色

    Returns:
        权限列表
    """
    return ROLE_PERMISSIONS.get(user_role, [])
