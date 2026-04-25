"""灰度发布与功能开关

支持按用户分组灰度发布新功能，实现平滑上线。

核心能力:
  - 功能开关: 按功能名称控制启用/禁用
  - 灰度路由: 按用户ID哈希分配到灰度组或稳定组
  - 灰度比例: 支持从 0% 到 100% 渐进式放量
  - 用户白名单: 指定用户始终使用新功能

使用方式:
  from deploy.canary import is_feature_enabled, get_variant

  if is_feature_enabled("new_agent_router", user_id):
      # 使用新逻辑
  else:
      # 使用旧逻辑
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FeatureFlag:
    """功能开关定义"""

    name: str
    description: str = ""
    enabled: bool = True
    rollout_percentage: float = 0.0
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)


# 功能开关注册表
# rollout_percentage: 0.0 = 全部旧逻辑, 100.0 = 全部新逻辑
_FEATURE_FLAGS: dict[str, FeatureFlag] = {
    "new_agent_router": FeatureFlag(
        name="new_agent_router",
        description="新 Agent 路由逻辑（基于意图分类优化）",
        enabled=True,
        rollout_percentage=10.0,
        whitelist=[],
    ),
    "stream_response": FeatureFlag(
        name="stream_response",
        description="流式响应模式",
        enabled=True,
        rollout_percentage=50.0,
        whitelist=[],
    ),
    "hr_agent": FeatureFlag(
        name="hr_agent",
        description="HR 人事 Agent",
        enabled=True,
        rollout_percentage=100.0,
        whitelist=[],
    ),
    "finance_agent": FeatureFlag(
        name="finance_agent",
        description="财务 Agent",
        enabled=True,
        rollout_percentage=100.0,
        whitelist=[],
    ),
    "knowledge_search": FeatureFlag(
        name="knowledge_search",
        description="知识库语义搜索",
        enabled=True,
        rollout_percentage=30.0,
        whitelist=[],
    ),
    "performance_cache": FeatureFlag(
        name="performance_cache",
        description="多级缓存优化",
        enabled=True,
        rollout_percentage=100.0,
        whitelist=[],
    ),
}


def _hash_user_id(user_id: str) -> int:
    """对用户ID进行哈希，返回 0-99 的整数

    使用 MD5 哈希确保分布均匀。

    Args:
        user_id: 用户ID

    Returns:
        0-99 之间的整数
    """
    digest = hashlib.md5(user_id.encode()).hexdigest()
    return int(digest, 16) % 100


def is_feature_enabled(feature_name: str, user_id: str = "") -> bool:
    """判断指定功能对某用户是否启用

    判断逻辑:
    1. 功能开关未注册或全局禁用 -> False
    2. 用户在黑名单 -> False
    3. 用户在白名单 -> True
    4. 按灰度比例哈希判断

    Args:
        feature_name: 功能名称
        user_id: 用户ID，为空时仅检查全局开关

    Returns:
        是否启用
    """
    flag = _FEATURE_FLAGS.get(feature_name)
    if flag is None:
        logger.warning("功能开关未注册: %s", feature_name)
        return False

    if not flag.enabled:
        return False

    if not user_id:
        return True

    if user_id in flag.blacklist:
        return False

    if user_id in flag.whitelist:
        return True

    hash_value = _hash_user_id(user_id)
    return hash_value < flag.rollout_percentage


def get_variant(feature_name: str, user_id: str) -> str:
    """获取用户在灰度实验中的变体

    Args:
        feature_name: 功能名称
        user_id: 用户ID

    Returns:
        "treatment"（新逻辑）或 "control"（旧逻辑）
    """
    if is_feature_enabled(feature_name, user_id):
        return "treatment"
    return "control"


def update_rollout(feature_name: str, percentage: float) -> bool:
    """更新灰度比例

    Args:
        feature_name: 功能名称
        percentage: 新的灰度比例 (0.0 - 100.0)

    Returns:
        是否更新成功
    """
    flag = _FEATURE_FLAGS.get(feature_name)
    if flag is None:
        logger.warning("功能开关未注册: %s", feature_name)
        return False

    old_pct = flag.rollout_percentage
    flag.rollout_percentage = max(0.0, min(100.0, percentage))
    logger.info("灰度比例更新: %s %.1f%% -> %.1f%%", feature_name, old_pct, flag.rollout_percentage)
    return True


def add_to_whitelist(feature_name: str, user_ids: list[str]) -> bool:
    """添加用户到白名单

    Args:
        feature_name: 功能名称
        user_ids: 用户ID列表

    Returns:
        是否添加成功
    """
    flag = _FEATURE_FLAGS.get(feature_name)
    if flag is None:
        return False

    for uid in user_ids:
        if uid not in flag.whitelist:
            flag.whitelist.append(uid)

    logger.info("白名单更新: %s 添加 %d 个用户", feature_name, len(user_ids))
    return True


def set_feature_enabled(feature_name: str, enabled: bool) -> bool:
    """设置功能开关全局启用/禁用

    Args:
        feature_name: 功能名称
        enabled: 是否启用

    Returns:
        是否设置成功
    """
    flag = _FEATURE_FLAGS.get(feature_name)
    if flag is None:
        return False

    flag.enabled = enabled
    logger.info("功能开关 %s: %s", feature_name, "启用" if enabled else "禁用")
    return True


def get_all_flags() -> dict[str, dict[str, Any]]:
    """获取所有功能开关状态"""
    result = {}
    for name, flag in _FEATURE_FLAGS.items():
        result[name] = {
            "name": flag.name,
            "description": flag.description,
            "enabled": flag.enabled,
            "rollout_percentage": flag.rollout_percentage,
            "whitelist_count": len(flag.whitelist),
            "blacklist_count": len(flag.blacklist),
        }
    return result


def register_feature_flag(flag: FeatureFlag) -> None:
    """动态注册新的功能开关

    Args:
        flag: 功能开关定义
    """
    _FEATURE_FLAGS[flag.name] = flag
    logger.info("注册功能开关: %s (灰度比例: %.1f%%)", flag.name, flag.rollout_percentage)
