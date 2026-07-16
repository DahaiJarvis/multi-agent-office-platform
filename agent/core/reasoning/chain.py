"""推理链数据模型

定义 Chain of Thought 推理过程中的数据结构，用于：
  1. 记录 Agent 在关键决策点的推理过程
  2. 在步骤间传递推理依据，使后续步骤理解前序决策
  3. 持久化到检查点，支持断点恢复时重建推理上下文

数据流：
  ReasoningStep（单步推理）
    -> ReasoningChain（推理链，包含多步推理）
      -> 写入 StepCheckpoint.reasoning_chain
        -> 传递给后续步骤作为上下文
"""

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReasoningType(str, Enum):
    """推理类型枚举

    标识推理发生的场景，不同场景的推理链在前端展示时
    使用不同的图标和折叠样式。
    """

    INTENT = "intent"                  # 意图分类推理
    PLANNING = "planning"              # 任务规划推理
    REVIEW = "review"                  # 审核决策推理
    TOOL_SELECTION = "tool_selection"  # 工具选择推理
    EXECUTION = "execution"            # 执行过程推理


class ReasoningStep(BaseModel):
    """单步推理记录

    记录一次推理的完整信息，包括：
      - 推理类型（意图/规划/审核/工具选择/执行）
      - 推理输入（触发推理的问题或上下文）
      - 推理过程（LLM 输出的思考步骤）
      - 推理结论（推理得出的决策）
      - 置信度（推理结论的可信程度）

    Attributes:
        step_id: 推理步骤唯一标识
        reasoning_type: 推理类型
        agent_name: 执行推理的 Agent 名称
        input_context: 触发推理的输入上下文
        thought_process: 推理思考过程（LLM 输出的中间步骤）
        conclusion: 推理结论
        confidence: 推理置信度 [0, 1]
        created_at: 推理时间戳
    """

    step_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    reasoning_type: ReasoningType
    agent_name: str = ""
    input_context: str = ""
    thought_process: str = ""
    conclusion: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: float = Field(default_factory=time.time)


class ReasoningChain(BaseModel):
    """推理链

    由多个 ReasoningStep 组成的推理链，记录一次任务执行中
    所有关键决策点的推理过程。

    推理链在步骤间传递：后续步骤可通过 reasoning_chain 了解
    前序步骤的决策依据，从而做出更合理的判断。

    Attributes:
        chain_id: 推理链唯一标识
        task_message: 触发推理链的原始用户消息
        steps: 推理步骤列表
        created_at: 创建时间戳
    """

    chain_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_message: str = ""
    steps: list[ReasoningStep] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

    def add_step(self, step: ReasoningStep) -> None:
        """追加推理步骤"""
        self.steps.append(step)

    def get_steps_by_type(self, reasoning_type: ReasoningType) -> list[ReasoningStep]:
        """按类型获取推理步骤"""
        return [s for s in self.steps if s.reasoning_type == reasoning_type]

    def get_last_conclusion(self) -> str:
        """获取最近一步推理的结论"""
        if self.steps:
            return self.steps[-1].conclusion
        return ""

    def to_context_text(self, max_steps: int = 5) -> str:
        """将推理链转换为可注入 Prompt 的上下文文本

        取最近 max_steps 步推理的结论，供后续 Agent 参考。
        不输出完整 thought_process 以控制 Token 消耗。

        Args:
            max_steps: 最多包含的推理步骤数

        Returns:
            格式化的推理上下文文本
        """
        if not self.steps:
            return ""

        recent_steps = self.steps[-max_steps:]
        parts = ["[前序推理依据]"]
        for i, step in enumerate(recent_steps, 1):
            type_label = {
                ReasoningType.INTENT: "意图分析",
                ReasoningType.PLANNING: "任务规划",
                ReasoningType.REVIEW: "审核决策",
                ReasoningType.TOOL_SELECTION: "工具选择",
                ReasoningType.EXECUTION: "执行推理",
            }.get(step.reasoning_type, "推理")

            parts.append(f"  {i}. [{type_label}] {step.conclusion}")
        return "\n".join(parts)

    def to_summary(self) -> dict[str, Any]:
        """生成推理链摘要（用于事件输出和前端展示）

        Returns:
            包含推理链概要信息的字典
        """
        return {
            "chain_id": self.chain_id,
            "total_steps": len(self.steps),
            "types": list({s.reasoning_type.value for s in self.steps}),
            "last_conclusion": self.get_last_conclusion(),
        }
