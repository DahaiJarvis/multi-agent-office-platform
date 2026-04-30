"""意图分类单元测试

使用 MockChatCompletionClient 替代真实 LLM 调用，
验证意图分类逻辑的正确性。
"""

import json
import pytest

from tests.mocks.llm_mock import create_intent_mock_client


@pytest.fixture
def mock_client():
    return create_intent_mock_client()


class TestIntentClassification:
    """意图分类测试"""

    @pytest.mark.asyncio
    async def test_approval_query_intent(self, mock_client):
        """测试审批查询意图识别"""
        from autogen_core.models import SystemMessage, UserMessage

        response = await mock_client.create(
            messages=[
                SystemMessage(source="system", content="你是一个意图分类器"),
                UserMessage(source="user", content="查看我的待审批列表"),
            ],
            json_output=True,
        )
        result = json.loads(response.content)
        assert result["intent"] == "approval_query"
        assert result["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_email_send_intent(self, mock_client):
        """测试邮件发送意图识别"""
        from autogen_core.models import SystemMessage, UserMessage

        response = await mock_client.create(
            messages=[
                SystemMessage(source="system", content="你是一个意图分类器"),
                UserMessage(source="user", content="帮我发一封邮件给张总"),
            ],
            json_output=True,
        )
        result = json.loads(response.content)
        assert result["intent"] == "email_send"
        assert result["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_hr_action_intent(self, mock_client):
        """测试HR操作意图识别"""
        from autogen_core.models import SystemMessage, UserMessage

        response = await mock_client.create(
            messages=[
                SystemMessage(source="system", content="你是一个意图分类器"),
                UserMessage(source="user", content="我想请一天假"),
            ],
            json_output=True,
        )
        result = json.loads(response.content)
        assert result["intent"] == "hr_action"
        assert result["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_general_intent(self, mock_client):
        """测试通用意图识别"""
        from autogen_core.models import SystemMessage, UserMessage

        response = await mock_client.create(
            messages=[
                SystemMessage(source="system", content="你是一个意图分类器"),
                UserMessage(source="user", content="你好"),
            ],
            json_output=True,
        )
        result = json.loads(response.content)
        assert result["intent"] == "general"
        assert result["confidence"] >= 0.5

    @pytest.mark.asyncio
    async def test_web_search_intent(self, mock_client):
        """测试网络搜索意图识别"""
        from autogen_core.models import SystemMessage, UserMessage

        response = await mock_client.create(
            messages=[
                SystemMessage(source="system", content="你是一个意图分类器"),
                UserMessage(source="user", content="北京明天天气怎么样"),
            ],
            json_output=True,
        )
        result = json.loads(response.content)
        assert result["intent"] == "web_search"
        assert result["confidence"] >= 0.8

    @pytest.mark.asyncio
    async def test_finance_action_intent(self, mock_client):
        """测试财务操作意图识别"""
        from autogen_core.models import SystemMessage, UserMessage

        response = await mock_client.create(
            messages=[
                SystemMessage(source="system", content="你是一个意图分类器"),
                UserMessage(source="user", content="提交报销申请"),
            ],
            json_output=True,
        )
        result = json.loads(response.content)
        assert result["intent"] == "finance_action"
        assert result["confidence"] >= 0.8


class TestMockClientBehavior:
    """Mock 客户端行为测试"""

    @pytest.mark.asyncio
    async def test_call_count_tracking(self, mock_client):
        """测试调用次数追踪"""
        from autogen_core.models import SystemMessage, UserMessage

        assert mock_client.call_count == 0

        await mock_client.create(
            messages=[UserMessage(source="user", content="测试")],
        )
        assert mock_client.call_count == 1

        await mock_client.create(
            messages=[UserMessage(source="user", content="测试2")],
        )
        assert mock_client.call_count == 2

    @pytest.mark.asyncio
    async def test_call_history_recording(self, mock_client):
        """测试调用历史记录"""
        from autogen_core.models import UserMessage

        await mock_client.create(
            messages=[UserMessage(source="user", content="查看审批")],
            json_output=True,
        )

        assert len(mock_client.call_history) == 1
        record = mock_client.call_history[0]
        assert record["messages_count"] == 1
        assert record["json_output"] is True
        assert "timestamp" in record

    @pytest.mark.asyncio
    async def test_token_counting(self, mock_client):
        """测试 Token 计数"""
        from autogen_core.models import UserMessage

        tokens = mock_client.count_tokens(
            messages=[UserMessage(source="user", content="你好世界")],
        )
        assert tokens > 0

        remaining = mock_client.remaining_tokens(
            messages=[UserMessage(source="user", content="你好世界")],
        )
        assert remaining > 0
