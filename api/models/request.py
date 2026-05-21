"""请求模型定义"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """用户对话请求"""

    message: str = Field(..., min_length=1, max_length=5000, description="用户消息内容")
    session_id: str | None = Field(default=None, description="会话ID，为空则创建新会话")
    user_id: str = Field(..., description="用户ID")
    channel: str = Field(default="web", description="接入渠道: web/wechat/dingtalk")
    image_urls: list[str] = Field(default_factory=list, description="图像URL列表（多模态输入）")
    audio_url: str = Field(default="", description="语音URL（多模态输入）")
    knowledge_base_id: str | None = Field(default=None, description="知识库ID，选择知识库后直接路由到知识库问答")


class SessionCreateRequest(BaseModel):
    """创建会话请求"""

    user_id: str = Field(..., description="用户ID")
    channel: str = Field(default="web", description="接入渠道")


class SessionHistoryRequest(BaseModel):
    """查询会话历史请求"""

    session_id: str = Field(..., description="会话ID")
    limit: int = Field(default=50, ge=1, le=200, description="返回消息数量上限")


# ==================== 知识库代理请求模型 ====================


class CreateKnowledgeBaseRequest(BaseModel):
    """创建知识库请求

    对应 IDA 的 POST /api/knowledge-bases 接口，
    校验前端提交的知识库创建参数。
    """

    name: str = Field(..., min_length=1, max_length=100, description="知识库名称")
    description: str = Field(default="", max_length=500, description="知识库描述")
    access_level: str = Field(
        default="private",
        pattern="^(private|team|public)$",
        description="访问级别: private/team/public",
    )


class UpdateKnowledgeBaseRequest(BaseModel):
    """更新知识库请求

    对应 IDA 的 PUT /api/knowledge-bases/{kb_id} 接口，
    所有字段可选，仅更新提交的字段。
    """

    name: str | None = Field(default=None, min_length=1, max_length=100, description="知识库名称")
    description: str | None = Field(default=None, max_length=500, description="知识库描述")
    access_level: str | None = Field(
        default=None,
        pattern="^(private|team|public)$",
        description="访问级别: private/team/public",
    )


class QAAskRequest(BaseModel):
    """智能问答请求

    对应 IDA 的 POST /api/qa/ask 和 /api/qa/ask/stream 接口，
    校验问答请求参数。
    """

    query: str = Field(..., min_length=1, max_length=5000, description="用户提问内容")
    knowledge_base_id: str | None = Field(default=None, description="关联的知识库ID")
    session_id: str | None = Field(default=None, description="会话ID，用于多轮对话")
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="相似度阈值")


# ==================== 任务管理请求模型 ====================


class TaskResumeRequest(BaseModel):
    """任务恢复请求"""

    execution_id: str = Field(..., description="执行记录ID")
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    supplementary_message: str | None = Field(default=None, description="补充需求，恢复时追加到原始请求上下文中")


class TaskStepRetryRequest(BaseModel):
    """步骤重试请求"""

    execution_id: str = Field(..., description="执行记录ID")
    step_index: int = Field(..., ge=1, description="步骤索引（从1开始，0为意图分类）")
    user_id: str = Field(..., description="用户ID")
    agent_name: str | None = Field(default=None, description="指定重试使用的Agent名称，为空则使用原Agent")


class TaskConfirmRequest(BaseModel):
    """人工确认请求"""

    execution_id: str | None = Field(default=None, description="执行记录ID（可选，后端通过confirm_id自动获取）")
    step_index: int | None = Field(default=None, ge=0, description="步骤索引（可选，后端通过confirm_id自动获取）")
    decision: str = Field(..., description="决策: continue/skip/cancel/retry")
    comment: str = Field(default="", description="备注")
    user_id: str = Field(..., description="用户ID")
    agent_name: str | None = Field(default=None, description="重试时指定Agent名称，仅decision=retry时有效")


class TaskCancelRequest(BaseModel):
    """任务取消请求"""

    execution_id: str = Field(..., description="执行记录ID")
    user_id: str = Field(..., description="用户ID")
    force: bool = Field(default=False, description="True=放弃任务(CANCELLED)，False=暂停任务(INTERRUPTED)")
