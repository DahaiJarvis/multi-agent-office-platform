"""原生工具基础模型

定义原生工具层的核心数据模型，包括：
  - 延迟分层枚举（LatencyTier）
  - 权限级别枚举（PermissionLevel）
  - 工具元数据模型（NativeToolMeta）

这些模型为工具注册中心、加载器和协议适配层提供统一的数据基础。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LatencyTier(str, Enum):
    """工具延迟分层

    根据工具执行的预期延迟进行分级，用于：
      - 工具调度优化：优先调用低延迟工具
      - 超时策略配置：不同延迟分层设置不同超时时间
      - 用户体验提示：告知用户工具响应速度预期

    分级说明：
    -------------------------------------------------------------------------
    instant: 瞬时工具，无外部依赖，毫秒级响应
      - 典型工具：时间查询、文本格式转换、会话历史查询
      - 超时建议：5秒

    fast: 快速工具，依赖本地服务或缓存，秒级响应
      - 典型工具：数据查询、搜索引擎、OCR
      - 超时建议：15秒

    slow: 慢速工具，依赖 LLM 或复杂计算，十秒级响应
      - 典型工具：文档摘要、报告生成、RAG 检索
      - 超时建议：60秒

    general: 通用工具，延迟不确定
      - 典型工具：复合工具、动态延迟工具
      - 超时建议：30秒
    -------------------------------------------------------------------------
    """

    INSTANT = "instant"
    FAST = "fast"
    SLOW = "slow"
    GENERAL = "general"


class PermissionLevel(str, Enum):
    """工具权限级别

    定义工具调用的权限要求，用于安全护栏判断：
      - 是否需要用户确认
      - 操作审计级别
      - 数据访问范围

    权限层级（从低到高）：
    -------------------------------------------------------------------------
    read_only: 只读操作，无需确认
      - 典型操作：查询数据、搜索文档、获取时间
      - 审计级别：INFO

    read_write: 读写操作，需确认
      - 典型操作：导出数据、生成报告、修改配置
      - 审计级别：WARN

    sensitive: 敏感操作，需二次确认
      - 典型操作：解析文档（访问文件系统）、发送通知
      - 审计级别：ERROR

    admin: 管理操作，需管理员确认
      - 典型操作：系统配置、用户管理
      - 审计级别：CRITICAL
    -------------------------------------------------------------------------
    """

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    SENSITIVE = "sensitive"
    ADMIN = "admin"


class NativeToolMeta(BaseModel):
    """原生工具元数据

    描述一个原生工具的完整信息，用于工具注册、发现、调度和安全控制。
    所有字段均有默认值，确保向后兼容。

    Attributes:
        name: 工具名称，必须以 native_ 前缀开头
        display_name: 工具显示名称，用于 UI 展示
        description: 工具功能描述，供 LLM 理解工具用途
        category: 工具分类（session/data/document/search/text/report/multimodal/rag/skill/system）
        parameters: 工具参数的 JSON Schema，与 AutoGen FunctionTool 格式一致
        latency_tier: 延迟分层，用于调度优化
        permission_level: 权限级别，用于安全护栏
        timeout_seconds: 执行超时时间（秒）
        requires_llm: 是否依赖 LLM 客户端
        version: 工具版本号
        enabled: 是否启用
        tags: 工具标签，用于分类和搜索
        agent_bindings: 绑定该工具的 Agent 列表
        examples: 工具调用示例
    """

    name: str = Field(description="工具名称，必须以 native_ 前缀开头")
    display_name: str = Field(default="", description="工具显示名称")
    description: str = Field(default="", description="工具功能描述")
    category: str = Field(default="system", description="工具分类")
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        },
        description="工具参数的 JSON Schema",
    )
    latency_tier: LatencyTier = Field(default=LatencyTier.GENERAL, description="延迟分层")
    permission_level: PermissionLevel = Field(
        default=PermissionLevel.READ_ONLY, description="权限级别"
    )
    timeout_seconds: int = Field(default=30, description="执行超时时间（秒）")
    requires_llm: bool = Field(default=False, description="是否依赖 LLM 客户端")
    version: str = Field(default="1.0.0", description="工具版本号")
    enabled: bool = Field(default=True, description="是否启用")
    tags: list[str] = Field(default_factory=list, description="工具标签")
    agent_bindings: list[str] = Field(default_factory=list, description="绑定该工具的 Agent 列表")
    examples: list[dict[str, Any]] = Field(default_factory=list, description="工具调用示例")
