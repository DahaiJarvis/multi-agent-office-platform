"""LLM Mock 客户端

在测试环境中替代真实 LLM 调用，支持可预测的响应生成。
无需真实 API Key 即可运行单元测试。

使用方式：
    from tests.mocks.llm_mock import MockChatCompletionClient

    client = MockChatCompletionClient()
    # 或自定义响应
    client = MockChatCompletionClient(responses={"intent": '{"intent": "email_query", "confidence": 0.9}'})

在测试中替换 model_client：
    from unittest.mock import patch
    with patch("agent.core.model_client.get_model_client", return_value=MockChatCompletionClient()):
        # 测试代码
"""

import json
import logging
import time
import uuid
from typing import Any, Sequence

from autogen_core.models import (
    CreateResult,
    LLMMessage,
    ModelInfo,
    RequestUsage,
)
from autogen_core import CancellationToken

logger = logging.getLogger(__name__)


class MockChatCompletionClient:
    """Mock LLM 客户端

    实现 autogen ChatCompletionClient 接口，返回预设的响应。
    支持按关键词匹配不同响应，用于测试不同场景。
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str = "这是一个模拟回复。",
        latency_ms: int = 0,
    ) -> None:
        """初始化 Mock 客户端

        Args:
            responses: 关键词到响应的映射，匹配用户消息中的关键词返回对应响应
            default_response: 默认响应内容
            latency_ms: 模拟延迟（毫秒）
        """
        self._responses = responses or {}
        self._default_response = default_response
        self._latency_ms = latency_ms
        self._call_count: int = 0
        self._call_history: list[dict[str, Any]] = []
        self._model_info = ModelInfo(
            vision=False,
            function_calling=True,
            json_output=True,
            structured_output=True,
            family="mock",
        )

    @property
    def call_count(self) -> int:
        """获取调用次数"""
        return self._call_count

    @property
    def call_history(self) -> list[dict[str, Any]]:
        """获取调用历史"""
        return self._call_history

    def _match_response(self, messages: Sequence[LLMMessage]) -> str:
        """根据消息内容匹配响应

        遍历 responses 中的关键词，匹配用户消息返回对应响应。
        无匹配时返回默认响应。
        """
        user_content = ""
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    part.text for part in content if hasattr(part, "text")
                )
            if content and getattr(msg, "source", "") == "user":
                user_content += content + " "

        user_content = user_content.lower()

        for keyword, response in self._responses.items():
            if keyword.lower() in user_content:
                return response

        return self._default_response

    async def create(
        self,
        messages: Sequence[LLMMessage],
        *,
        cancellation_token: CancellationToken | None = None,
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> CreateResult:
        """模拟 LLM 创建响应"""
        if self._latency_ms > 0:
            time.sleep(self._latency_ms / 1000.0)

        self._call_count += 1

        response_text = self._match_response(messages)

        # 如果要求 JSON 输出且响应不是有效 JSON，包装为 JSON
        if json_output and not response_text.strip().startswith("{"):
            response_text = json.dumps(
                {"response": response_text}, ensure_ascii=False
            )

        # 记录调用历史
        self._call_history.append({
            "messages_count": len(messages),
            "json_output": json_output,
            "response_preview": response_text[:100],
            "timestamp": time.time(),
        })

        prompt_tokens = sum(
            len(getattr(msg, "content", "") or "") for msg in messages
        )
        completion_tokens = len(response_text)

        return CreateResult(
            content=response_text,
            finish_reason="stop",
            usage=RequestUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            cached=False,
        )

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        cancellation_token: CancellationToken | None = None,
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """模拟流式响应"""
        result = await self.create(
            messages,
            cancellation_token=cancellation_token,
            json_output=json_output,
            extra_create_args=extra_create_args,
            **kwargs,
        )
        yield result

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    @property
    def model_capabilities(self) -> ModelInfo:
        return self._model_info

    def count_tokens(self, messages: Sequence[LLMMessage], *, json_output: bool | None = None) -> int:
        """模拟 Token 计数"""
        total = 0
        for msg in messages:
            content = getattr(msg, "content", "") or ""
            total += len(content)
        return total

    def remaining_tokens(self, messages: Sequence[LLMMessage], *, json_output: bool | None = None) -> int:
        """模拟剩余 Token 数"""
        return 8000 - self.count_tokens(messages, json_output=json_output)

    def __repr__(self) -> str:
        return f"MockChatCompletionClient(calls={self._call_count})"


# 预置的意图分类 Mock 响应
INTENT_MOCK_RESPONSES: dict[str, str] = {
    "审批": '{"intent": "approval_query", "confidence": 0.95, "sub_tasks": []}',
    "待审批": '{"intent": "approval_query", "confidence": 0.95, "sub_tasks": []}',
    "同意": '{"intent": "approval_action", "confidence": 0.9, "sub_tasks": []}',
    "拒绝": '{"intent": "approval_action", "confidence": 0.9, "sub_tasks": []}',
    "邮件": '{"intent": "email_query", "confidence": 0.9, "sub_tasks": []}',
    "发邮件": '{"intent": "email_send", "confidence": 0.95, "sub_tasks": []}',
    "日程": '{"intent": "calendar_query", "confidence": 0.9, "sub_tasks": []}',
    "会议": '{"intent": "calendar_create", "confidence": 0.9, "sub_tasks": []}',
    "客户": '{"intent": "crm_query", "confidence": 0.9, "sub_tasks": []}',
    "请假": '{"intent": "hr_action", "confidence": 0.95, "sub_tasks": []}',
    "考勤": '{"intent": "hr_query", "confidence": 0.9, "sub_tasks": []}',
    "报销": '{"intent": "finance_action", "confidence": 0.95, "sub_tasks": []}',
    "知识": '{"intent": "knowledge_query", "confidence": 0.9, "sub_tasks": []}',
    "天气": '{"intent": "web_search", "confidence": 0.95, "sub_tasks": []}',
    "你好": '{"intent": "general", "confidence": 0.95, "sub_tasks": []}',
}


def create_intent_mock_client() -> MockChatCompletionClient:
    """创建意图分类专用的 Mock 客户端"""
    return MockChatCompletionClient(
        responses=INTENT_MOCK_RESPONSES,
        default_response='{"intent": "general", "confidence": 0.5, "sub_tasks": []}',
    )
