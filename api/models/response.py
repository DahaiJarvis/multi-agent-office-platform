"""响应模型定义"""

from datetime import datetime
from pydantic import BaseModel, Field


class ChatResponse(BaseModel):
    """对话响应"""

    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., description="Agent 回复内容")
    agent_name: str = Field(default="Supervisor", description="响应的 Agent 名称")
    intent: str | None = Field(default=None, description="识别的意图")
    collaboration_mode: str | None = Field(default=None, description="协作模式")
    execution_id: str | None = Field(default=None, description="任务执行ID")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")


class SessionResponse(BaseModel):
    """会话信息响应"""

    session_id: str
    user_id: str
    channel: str
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(default=0, description="消息数量")
    active_agents: list[str] = Field(default_factory=list, description="活跃的 Agent 列表")


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(default="healthy", description="服务状态")
    version: str = Field(default="0.1.0", description="服务版本")
    timestamp: datetime = Field(default_factory=datetime.now, description="检查时间")
    components: dict[str, str] = Field(
        default_factory=dict, description="各组件状态"
    )


class ErrorResponse(BaseModel):
    """错误响应

    已弃用: 请使用 api.errors.ErrorResponse 替代，该版本包含完整的错误码体系。
    保留此模型仅为向后兼容。
    """

    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误详情")
    error_code: str | None = Field(default=None, description="错误码")
    request_id: str | None = Field(default=None, description="请求ID")


# ==================== 任务管理响应模型 ====================


class TaskExecutionResponse(BaseModel):
    """任务执行状态响应"""

    execution_id: str = Field(..., description="执行记录ID")
    session_id: str = Field(default="", description="会话ID")
    status: str = Field(default="", description="任务状态")
    current_step: int = Field(default=0, description="当前步骤索引")
    total_steps: int = Field(default=0, description="总步骤数")
    failure_policy: str = Field(default="relaxed", description="故障策略")
    error: str = Field(default="", description="错误信息")
    steps: list[dict] = Field(default_factory=list, description="步骤详情列表")
    created_at: float = Field(default=0, description="创建时间戳")
    updated_at: float = Field(default=0, description="更新时间戳")
