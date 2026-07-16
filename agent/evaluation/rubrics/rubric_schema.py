"""Rubric 数据模型

LLM-as-judge 的评分标准，定义评分维度、权重与通过阈值。
对应 spec 文档 4.2/4.3 节。

评分维度（四维度）：
  - 正确性（correctness）：输出是否正确满足 fixture.success_criteria
  - 完整性（completeness）：是否覆盖 expected_output_contains 全部要点
  - 安全性（safety）：是否违反 safety_constraints / forbidden_tools
  - 轨迹合理性（trajectory）：工具调用链是否合理、有无冗余调用
"""

from pydantic import BaseModel, Field, ConfigDict


class RubricDimension(BaseModel):
    """评分维度

    定义单个评分维度的名称、说明、权重与通过阈值。

    Attributes:
        name: 维度名称（如 correctness/completeness/safety/trajectory）
        description: 维度说明，供 judge 参考
        weight: 权重，总分加权计算（默认 1.0）
        pass_threshold: 该维度通过阈值（0-1），低于则标记不通过（默认 0.7）
    """

    model_config = ConfigDict(frozen=False)

    name: str = Field(..., description="维度名称")
    description: str = Field(..., description="维度说明，供 judge 参考")
    weight: float = Field(default=1.0, description="权重，总分加权计算")
    pass_threshold: float = Field(
        default=0.7,
        description="该维度通过阈值（0-1），低于则标记不通过",
    )


class Rubric(BaseModel):
    """评分标准

    定义一组评分维度及其权重，用于 LLM-as-judge 评分。

    Attributes:
        rubric_id: 唯一标识
        name: 标准名称
        category: 适用分类，与 Fixture.category 对应
        dimensions: 评分维度列表
        overall_pass_threshold: 总分通过阈值（0-1）
        judge_prompt_template: judge 提示词模板，含占位符
    """

    model_config = ConfigDict(frozen=False)

    rubric_id: str = Field(..., description="唯一标识")
    name: str = Field(..., description="标准名称")
    category: str = Field(
        default="general",
        description="适用分类，与 Fixture.category 对应",
    )
    dimensions: list[RubricDimension] = Field(
        default_factory=list,
        description="评分维度列表",
    )
    overall_pass_threshold: float = Field(
        default=0.8,
        description="总分通过阈值（0-1）",
    )
    judge_prompt_template: str = Field(
        default="",
        description="judge 提示词模板，含 {fixture}/{response}/{trajectory} 占位符",
    )

    def get_dimension(self, name: str) -> RubricDimension | None:
        """按名称获取评分维度"""
        for dim in self.dimensions:
            if dim.name == name:
                return dim
        return None

    def total_weight(self) -> float:
        """计算总权重"""
        return sum(dim.weight for dim in self.dimensions)


class DimensionScore(BaseModel):
    """单维度评分

    Attributes:
        name: 维度名称
        score: 得分（0-1）
        passed: 是否超过该维度通过阈值
        reason: 得分理由（LLM 生成）
    """

    model_config = ConfigDict(frozen=False)

    name: str = Field(..., description="维度名称")
    score: float = Field(..., description="得分（0-1）")
    passed: bool = Field(..., description="是否超过该维度通过阈值")
    reason: str = Field(default="", description="得分理由（LLM 生成）")


class JudgeResult(BaseModel):
    """LLM-as-judge 评分结果

    Attributes:
        fixture_id: 被评 fixture ID
        overall_score: 总分（0-1，加权平均）
        passed: 是否通过（总分 >= overall_pass_threshold）
        dimension_scores: 分项得分
        reason: 总体评分理由
        safety_violations: 安全违规列表（触犯 safety_constraints）
        judge_model: 评分模型名称
        judge_usage: 评分模型 token 用量
    """

    model_config = ConfigDict(frozen=False)

    fixture_id: str = Field(..., description="被评 fixture ID")
    overall_score: float = Field(..., description="总分（0-1，加权平均）")
    passed: bool = Field(..., description="是否通过（总分 >= overall_pass_threshold）")
    dimension_scores: list[DimensionScore] = Field(
        default_factory=list,
        description="分项得分",
    )
    reason: str = Field(default="", description="总体评分理由")
    safety_violations: list[str] = Field(
        default_factory=list,
        description="安全违规列表（触犯 safety_constraints）",
    )
    judge_model: str = Field(default="", description="评分模型名称")
    judge_usage: dict = Field(
        default_factory=dict,
        description="评分模型 token 用量",
    )
