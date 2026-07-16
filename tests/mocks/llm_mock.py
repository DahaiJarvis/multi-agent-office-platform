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

录制-回放模式（spec 04 第 5.3 节）：
    # 录制模式：真实调用 LLM，同时把请求-响应存入缓存
    client = MockChatCompletionClient(replay_cache_key="trace:session-123", replay_mode=False)

    # 回放模式：从缓存读取录制的响应返回
    client = MockChatCompletionClient(replay_cache_key="trace:session-123", replay_mode=True)
"""

import hashlib
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

    三种模式：
      - 预设模式（默认）：返回构造的固定响应（基于 responses 关键词匹配）
      - 录制模式：真实调用 LLM，同时把请求-响应存入缓存（采集真实响应样本）
      - 回放模式：从缓存读取录制的响应返回（确定性复现）
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str = "这是一个模拟回复。",
        latency_ms: int = 0,
        replay_cache_key: str = "",
        replay_mode: bool = False,
    ) -> None:
        """初始化 Mock 客户端

        Args:
            responses: 关键词到响应的映射，匹配用户消息中的关键词返回对应响应
            default_response: 默认响应内容
            latency_ms: 模拟延迟（毫秒）
            replay_cache_key: 回放缓存 key（如 "trace:session-123"），为空时不启用录制-回放
            replay_mode: 是否回放模式。False 时为录制模式（真实调用+缓存），True 时为回放模式（从缓存读取）
        """
        self._responses = responses or {}
        self._default_response = default_response
        self._latency_ms = latency_ms
        self._call_count: int = 0
        self._call_history: list[dict[str, Any]] = []

        # 录制-回放相关字段
        self._replay_cache_key = replay_cache_key
        self._replay_mode = replay_mode
        self._replay_cache: dict[str, str] = {}  # 内存缓存（生产环境用 Redis）
        self._real_client: Any = None  # 录制模式下的真实 LLM 客户端

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
        """模拟 LLM 创建响应

        三种模式：
          - 回放模式：从缓存读取录制的响应返回
          - 录制模式：真实调用 LLM，同时把响应存入缓存
          - 预设模式：返回关键词匹配的预设响应
        """
        if self._latency_ms > 0:
            time.sleep(self._latency_ms / 1000.0)

        self._call_count += 1

        # 生成请求缓存 key
        cache_key = self._make_cache_key(messages, json_output)

        # 回放模式：从缓存读取
        if self._replay_mode and self._replay_cache_key:
            response_text = self._replay_cache.get(cache_key, "")
            if not response_text:
                logger.warning(
                    "回放模式未找到缓存响应 key=%s，使用默认响应",
                    cache_key,
                )
                response_text = self._match_response(messages)
        # 录制模式：真实调用 LLM 并缓存
        elif self._replay_cache_key and not self._replay_mode:
            response_text = await self._record_and_create(
                messages,
                cache_key,
                json_output,
                extra_create_args,
                **kwargs,
            )
        # 预设模式：关键词匹配
        else:
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
            "replay_mode": self._replay_mode,
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
            cached=self._replay_mode,
        )

    def _make_cache_key(
        self,
        messages: Sequence[LLMMessage],
        json_output: bool | None = None,
    ) -> str:
        """生成请求缓存 key

        基于 messages 内容和 json_output 标志生成确定性 key，
        确保相同请求在录制和回放时使用相同的 key。

        Args:
            messages: 消息序列
            json_output: 是否 JSON 输出

        Returns:
            缓存 key 字符串
        """
        parts: list[str] = []
        for msg in messages:
            content = getattr(msg, "content", "") or ""
            source = getattr(msg, "source", "") or ""
            parts.append(f"{source}:{content}")

        key_content = "|".join(parts) + f"|json={json_output}"
        return hashlib.md5(key_content.encode("utf-8")).hexdigest()

    async def _record_and_create(
        self,
        messages: Sequence[LLMMessage],
        cache_key: str,
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """录制模式：真实调用 LLM 并缓存响应

        首次调用时真实调用 LLM，将响应存入缓存。
        后续相同请求从缓存返回。
        当真实 LLM 不可用时降级为预设响应。

        Args:
            messages: 消息序列
            cache_key: 缓存 key
            json_output: 是否 JSON 输出
            extra_create_args: 额外参数
            **kwargs: 其他参数

        Returns:
            LLM 响应文本
        """
        # 检查缓存是否已有响应
        if cache_key in self._replay_cache:
            logger.debug("录制模式命中缓存: key=%s", cache_key)
            return self._replay_cache[cache_key]

        # 尝试真实调用 LLM
        try:
            real_client = self._get_real_client()
            if real_client is None:
                # 真实客户端不可用，降级为预设响应
                response_text = self._match_response(messages)
            else:
                result = await real_client.create(
                    messages,
                    json_output=json_output,
                    extra_create_args=extra_create_args,
                    **kwargs,
                )
                response_text = str(result.content)

            # 缓存响应
            self._replay_cache[cache_key] = response_text
            logger.debug("录制模式缓存响应: key=%s len=%d", cache_key, len(response_text))

            return response_text

        except Exception as e:
            logger.warning("录制模式真实调用失败，降级为预设响应: %s", e)
            response_text = self._match_response(messages)
            self._replay_cache[cache_key] = response_text
            return response_text

    def _get_real_client(self) -> Any:
        """获取真实 LLM 客户端（延迟初始化）

        录制模式下用于真实调用 LLM。
        初始化失败时返回 None，调用方降级为预设响应。

        Returns:
            真实 LLM 客户端，不可用时返回 None
        """
        if self._real_client is not None:
            return self._real_client

        try:
            from agent.core.model.model_client import get_model_client
            self._real_client = get_model_client()
            return self._real_client
        except Exception as e:
            logger.debug("真实 LLM 客户端不可用: %s", e)
            return None

    def load_replay_cache(self, cache: dict[str, str]) -> None:
        """加载回放缓存

        从外部加载录制的响应缓存，用于回放模式。

        Args:
            cache: 缓存字典 {cache_key: response_text}
        """
        self._replay_cache.update(cache)
        logger.debug("加载回放缓存: %d 条记录", len(cache))

    def get_replay_cache(self) -> dict[str, str]:
        """获取录制的响应缓存

        用于将录制的缓存导出，供回放模式使用。

        Returns:
            缓存字典 {cache_key: response_text}
        """
        return dict(self._replay_cache)

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
