"""请求模型定义"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """用户对话请求"""

    message: str = Field(..., min_length=1, max_length=5000, description="用户消息内容")
    session_id: str | None = Field(default=None, description="会话ID，为空则创建新会话")
    user_id: str = Field(..., description="用户ID")
    channel: str = Field(default="web", description="接入渠道: web/wechat/dingtalk")


class SessionCreateRequest(BaseModel):
    """创建会话请求"""

    user_id: str = Field(..., description="用户ID")
    channel: str = Field(default="web", description="接入渠道")


class SessionHistoryRequest(BaseModel):
    """查询会话历史请求"""

    session_id: str = Field(..., description="会话ID")
    limit: int = Field(default=50, ge=1, le=200, description="返回消息数量上限")
