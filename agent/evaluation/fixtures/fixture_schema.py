"""Fixture 数据模型

每个 Fixture 描述一个测试场景，包含输入、上下文、期望输出和期望工具调用。
对应 spec 文档 4.1 节。

数据流：
  Fixture（测试数据）
    -> HarnessRunner 驱动 Agent 执行
      -> SingleEvalResult（含 JudgeResult + TrajectoryEvalResult）
        -> PassKResult（k 次重复）
          -> EvalReport（套件级报告）
"""

from typing import Any

from pydantic import BaseModel, Field, ConfigDict


class Fixture(BaseModel):
    """Agent 评估 Fixture

    每个 Fixture 描述一个测试场景，包含输入、上下文、期望输出和期望工具调用。
    Fixture 是评估体系的最小单元，由 DatasetLoader 从 YAML/JSON 加载。

    Attributes:
        fixture_id: 唯一标识，如 email_query_001
        category: 分类（email/approval/crm/hr/finance/knowledge/adversarial）
        severity: 严重度：normal（常规）/ edge（边界）/ adversarial（对抗）
        input: 用户输入文本
        context: 上下文（user_id/permissions/prior_conversation 等）
        expected_tools: 期望调用的工具列表
        forbidden_tools: 禁止调用的工具列表
        expected_output_contains: 输出应包含的关键信息片段
        expected_output_shape: 输出结构（JSON Schema），用于结构化输出校验
        success_criteria: 自然语言成功标准，供 LLM-as-judge 使用
        safety_constraints: 安全约束，如"不得发送邮件"/"不得返回 PII"
        tags: 标签，如 ['canary', 'core', 'readonly']
        source: 来源：manual / trace_replay / production
        source_trace_id: 若来自 trace，记录原 trace_id，便于追溯
    """

    model_config = ConfigDict(frozen=False)

    fixture_id: str = Field(..., description="唯一标识，如 email_query_001")
    category: str = Field(
        default="general",
        description="分类（email/approval/crm/hr/finance/knowledge/adversarial）",
    )
    severity: str = Field(
        default="normal",
        description="严重度：normal（常规）/ edge（边界）/ adversarial（对抗）",
    )

    # 输入与上下文
    input: str = Field(..., description="用户输入文本")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="上下文（user_id/permissions/prior_conversation 等）",
    )

    # 期望工具调用
    expected_tools: list[str] = Field(
        default_factory=list,
        description="期望调用的工具列表，如 ['email_search', 'email_send']",
    )
    forbidden_tools: list[str] = Field(
        default_factory=list,
        description="禁止调用的工具列表，如 ['email_send']（只读场景）",
    )

    # 期望输出
    expected_output_contains: list[str] = Field(
        default_factory=list,
        description="输出应包含的关键信息片段",
    )
    expected_output_shape: dict[str, Any] | None = Field(
        default=None,
        description="输出结构（JSON Schema），用于结构化输出校验",
    )

    # 评判标准
    success_criteria: str = Field(
        default="",
        description="自然语言成功标准，供 LLM-as-judge 使用",
    )
    safety_constraints: list[str] = Field(
        default_factory=list,
        description="安全约束，如 '不得发送邮件' / '不得返回 PII'",
    )

    # 元数据
    tags: list[str] = Field(
        default_factory=list,
        description="标签，如 ['canary', 'core', 'readonly']",
    )
    source: str = Field(
        default="manual",
        description="来源：manual（人工编写）/ trace_replay（trace 转化）/ production（生产案例）",
    )
    source_trace_id: str = Field(
        default="",
        description="若来自 trace，记录原 trace_id，便于追溯",
    )

    def has_tag(self, tag: str) -> bool:
        """判断是否包含指定标签"""
        return tag in self.tags

    def is_canary(self) -> bool:
        """判断是否为金丝雀场景"""
        return self.has_tag("canary")

    def is_adversarial(self) -> bool:
        """判断是否为对抗场景"""
        return self.severity == "adversarial" or self.category == "adversarial"

    def is_readonly(self) -> bool:
        """判断是否为只读场景（标签含 readonly）"""
        return self.has_tag("readonly")

    def to_summary(self) -> dict[str, Any]:
        """生成摘要（用于日志输出）"""
        return {
            "fixture_id": self.fixture_id,
            "category": self.category,
            "severity": self.severity,
            "tags": self.tags,
            "source": self.source,
        }
