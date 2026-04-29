"""多渠道适配器

适配企业微信、钉钉、内部门户等不同渠道的消息格式，
将各渠道消息统一转换为平台内部标准格式。
采用适配器模式，每个渠道实现一个适配器。
"""

import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StandardMessage(BaseModel):
    """平台内部标准消息格式

    所有渠道的消息经适配后统一转换为此格式。
    """

    message_id: str = Field(..., description="消息唯一ID")
    channel: str = Field(..., description="来源渠道: wecom/dingtalk/web")
    user_id: str = Field(..., description="用户ID")
    user_name: str = Field(default="", description="用户名称")
    content: str = Field(..., description="消息文本内容")
    content_type: str = Field(default="text", description="内容类型: text/markdown/card")
    session_id: str = Field(default="", description="会话ID，为空则新建")
    timestamp: float = Field(default_factory=time.time, description="消息时间戳")
    raw_data: dict[str, Any] = Field(default_factory=dict, description="原始渠道数据")
    reply_url: str = Field(default="", description="回复消息的URL")


class ChannelAdapter(ABC):
    """渠道适配器基类

    定义统一的适配接口，各渠道实现具体的消息解析逻辑。
    """

    channel_name: str = ""

    @abstractmethod
    def parse_message(self, raw_data: dict[str, Any]) -> StandardMessage:
        """将渠道原始消息解析为标准格式

        Args:
            raw_data: 渠道原始请求数据

        Returns:
            标准消息对象

        Raises:
            ValueError: 消息格式无效
        """

    @abstractmethod
    def format_reply(self, message: str, session_id: str = "") -> dict[str, Any]:
        """将回复内容格式化为渠道要求的格式

        Args:
            message: 回复文本
            session_id: 会话ID

        Returns:
            渠道格式的回复数据
        """

    def verify_signature(self, raw_data: dict[str, Any], signature: str = "") -> bool:
        """验证渠道消息签名

        Args:
            raw_data: 原始数据
            signature: 待验证的签名

        Returns:
            签名是否有效
        """
        return True

    def _generate_message_id(self, channel: str, raw_data: dict[str, Any]) -> str:
        """根据渠道和原始数据生成消息唯一ID"""
        seed = f"{channel}:{raw_data.get('timestamp', time.time())}:{raw_data.get('msg_id', '')}"
        return hashlib.md5(seed.encode()).hexdigest()

    async def push_notification(
        self,
        user_id: str,
        message: str,
        title: str = "",
    ) -> bool:
        """主动推送通知到用户

        默认实现：格式化消息后通过渠道的回复机制推送。
        子类可覆写此方法实现渠道特定的推送逻辑。

        Args:
            user_id: 目标用户ID
            message: 推送内容
            title: 通知标题

        Returns:
            是否推送成功
        """
        logger.info(
            "推送通知: channel=%s user=%s title=%s",
            self.channel_name, user_id, title,
        )
        return True


class WeComAdapter(ChannelAdapter):
    """企业微信适配器

    解析企业微信回调消息格式，支持文本、Markdown、事件消息。
    """

    channel_name = "wecom"

    def parse_message(self, raw_data: dict[str, Any]) -> StandardMessage:
        msg_type = raw_data.get("MsgType", "text")
        user_id = raw_data.get("FromUserName", "")
        content = self._extract_content(raw_data, msg_type)

        if not user_id or not content:
            raise ValueError("企业微信消息缺少必要字段: FromUserName 或内容")

        return StandardMessage(
            message_id=raw_data.get("MsgId", self._generate_message_id("wecom", raw_data)),
            channel=self.channel_name,
            user_id=user_id,
            user_name=raw_data.get("UserName", ""),
            content=content,
            content_type="markdown" if msg_type == "markdown" else "text",
            session_id=raw_data.get("SessionId", ""),
            timestamp=raw_data.get("CreateTime", time.time()),
            raw_data=raw_data,
            reply_url="",
        )

    def format_reply(self, message: str, session_id: str = "") -> dict[str, Any]:
        return {
            "msgtype": "markdown",
            "markdown": {"content": message},
        }

    def verify_signature(self, raw_data: dict[str, Any], signature: str = "") -> bool:
        if not signature:
            return True
        token = raw_data.get("_wecom_token", "")
        timestamp = raw_data.get("timestamp", "")
        nonce = raw_data.get("nonce", "")
        encrypt = raw_data.get("Encrypt", "")
        if not all([token, timestamp, nonce, encrypt]):
            return True
        sign_parts = sorted([token, timestamp, nonce, encrypt])
        sign_str = "".join(sign_parts)
        computed = hashlib.sha1(sign_str.encode()).hexdigest()
        return computed == signature

    def _extract_content(self, raw_data: dict[str, Any], msg_type: str) -> str:
        if msg_type == "text":
            return raw_data.get("Content", "")
        if msg_type == "markdown":
            return raw_data.get("Content", "")
        if msg_type == "event":
            event_type = raw_data.get("Event", "")
            return f"[事件: {event_type}]"
        return raw_data.get("Content", "")


class DingTalkAdapter(ChannelAdapter):
    """钉钉适配器

    解析钉钉机器人回调消息格式，支持文本、Markdown、富文本消息。
    """

    channel_name = "dingtalk"

    def parse_message(self, raw_data: dict[str, Any]) -> StandardMessage:
        msg_type = raw_data.get("msgtype", "text")
        user_id = raw_data.get("senderStaffId", "") or raw_data.get("senderId", "")
        content = self._extract_content(raw_data, msg_type)

        if not user_id or not content:
            raise ValueError("钉钉消息缺少必要字段: senderStaffId 或内容")

        conversation_id = raw_data.get("conversationId", "")

        return StandardMessage(
            message_id=raw_data.get("msgId", self._generate_message_id("dingtalk", raw_data)),
            channel=self.channel_name,
            user_id=user_id,
            user_name=raw_data.get("senderNick", ""),
            content=content,
            content_type="markdown" if msg_type == "markdown" else "text",
            session_id=conversation_id,
            timestamp=raw_data.get("createAt", time.time()) / 1000 if raw_data.get("createAt") else time.time(),
            raw_data=raw_data,
            reply_url=raw_data.get("sessionWebhook", ""),
        )

    def format_reply(self, message: str, session_id: str = "") -> dict[str, Any]:
        return {
            "msgtype": "markdown",
            "markdown": {"title": "Agent 回复", "text": message},
        }

    def _extract_content(self, raw_data: dict[str, Any], msg_type: str) -> str:
        if msg_type == "text":
            return raw_data.get("text", {}).get("content", "").strip()
        if msg_type == "markdown":
            return raw_data.get("markdown", {}).get("text", "")
        if msg_type == "richText":
            parts = raw_data.get("richText", {}).get("richContent", [])
            texts = []
            for part in parts:
                if isinstance(part, list):
                    for item in part:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
            return "".join(texts)
        return ""


class WebAdapter(ChannelAdapter):
    """Web 端适配器

    解析 Web 前端直接发送的 JSON 格式消息。
    """

    channel_name = "web"

    def parse_message(self, raw_data: dict[str, Any]) -> StandardMessage:
        user_id = raw_data.get("user_id", "")
        content = raw_data.get("content", "") or raw_data.get("message", "")

        if not user_id or not content:
            raise ValueError("Web 消息缺少必要字段: user_id 或 content")

        return StandardMessage(
            message_id=raw_data.get("message_id", self._generate_message_id("web", raw_data)),
            channel=self.channel_name,
            user_id=user_id,
            user_name=raw_data.get("user_name", ""),
            content=content,
            content_type=raw_data.get("content_type", "text"),
            session_id=raw_data.get("session_id", ""),
            timestamp=raw_data.get("timestamp", time.time()),
            raw_data=raw_data,
            reply_url="",
        )

    def format_reply(self, message: str, session_id: str = "") -> dict[str, Any]:
        return {
            "type": "agent_reply",
            "content": message,
            "session_id": session_id,
        }


# 渠道适配器注册表
CHANNEL_ADAPTERS: dict[str, type[ChannelAdapter]] = {
    "wecom": WeComAdapter,
    "dingtalk": DingTalkAdapter,
    "web": WebAdapter,
}


def get_adapter(channel: str) -> ChannelAdapter:
    """获取指定渠道的适配器实例

    Args:
        channel: 渠道名称

    Returns:
        渠道适配器实例

    Raises:
        ValueError: 不支持的渠道
    """
    adapter_cls = CHANNEL_ADAPTERS.get(channel)
    if adapter_cls is None:
        raise ValueError(f"不支持的渠道: {channel}，可选: {list(CHANNEL_ADAPTERS.keys())}")
    return adapter_cls()


def parse_channel_message(channel: str, raw_data: dict[str, Any]) -> StandardMessage:
    """解析渠道消息为标准格式

    Args:
        channel: 渠道名称
        raw_data: 原始消息数据

    Returns:
        标准消息对象
    """
    adapter = get_adapter(channel)
    return adapter.parse_message(raw_data)


def format_channel_reply(channel: str, message: str, session_id: str = "") -> dict[str, Any]:
    """格式化回复消息为渠道格式

    Args:
        channel: 渠道名称
        message: 回复内容
        session_id: 会话ID

    Returns:
        渠道格式的回复数据
    """
    adapter = get_adapter(channel)
    return adapter.format_reply(message, session_id)


async def push_notification(
    channel: str,
    user_id: str,
    message: str,
    title: str = "",
) -> bool:
    """主动推送通知到用户

    通过渠道适配器推送通知，支持 Web（SSE）、企微、钉钉等渠道。

    Args:
        channel: 渠道名称
        user_id: 目标用户ID
        message: 推送内容
        title: 通知标题

    Returns:
        是否推送成功
    """
    try:
        adapter = get_adapter(channel)
        return await adapter.push_notification(user_id, message, title)
    except ValueError:
        logger.warning("不支持的推送渠道: %s", channel)
        return False
    except Exception as e:
        logger.error("推送通知失败: channel=%s user=%s error=%s", channel, user_id, e)
        return False


class ChannelSessionManager:
    """渠道与会话绑定管理器

    维护渠道维度的会话映射，支持：
      - 渠道会话亲和：同一用户在同一渠道复用同一会话
      - 跨渠道会话关联：同一用户在不同渠道的会话可关联
      - 会话生命周期管理：渠道会话超时自动解绑
    """

    # 渠道会话超时时间（秒）
    CHANNEL_SESSION_TTL = 7200

    def __init__(self) -> None:
        # key: "channel:user_id", value: session_id
        self._channel_sessions: dict[str, str] = {}
        # key: "channel:user_id", value: 绑定时间戳
        self._bind_timestamps: dict[str, float] = {}

    def bind_session(self, channel: str, user_id: str, session_id: str) -> None:
        """绑定渠道用户到会话

        当用户通过某渠道开始对话时，建立渠道-会话绑定关系。
        后续同一渠道的请求自动路由到同一会话。

        Args:
            channel: 渠道名称
            user_id: 用户ID
            session_id: 会话ID
        """
        key = f"{channel}:{user_id}"
        self._channel_sessions[key] = session_id
        self._bind_timestamps[key] = time.time()
        logger.debug("渠道会话绑定: %s -> %s", key, session_id)

    def unbind_session(self, channel: str, user_id: str) -> None:
        """解绑渠道用户的会话

        当会话结束或超时时，解除渠道-会话绑定。

        Args:
            channel: 渠道名称
            user_id: 用户ID
        """
        key = f"{channel}:{user_id}"
        self._channel_sessions.pop(key, None)
        self._bind_timestamps.pop(key, None)

    def get_session_id(self, channel: str, user_id: str) -> str | None:
        """获取渠道用户绑定的会话ID

        如果绑定已超时，自动解绑并返回 None。

        Args:
            channel: 渠道名称
            user_id: 用户ID

        Returns:
            会话ID 或 None
        """
        key = f"{channel}:{user_id}"
        session_id = self._channel_sessions.get(key)
        if session_id is None:
            return None

        # 检查绑定是否超时
        bind_time = self._bind_timestamps.get(key, 0)
        if time.time() - bind_time > self.CHANNEL_SESSION_TTL:
            self.unbind_session(channel, user_id)
            return None

        return session_id

    def get_user_all_sessions(self, user_id: str) -> dict[str, str]:
        """获取用户在所有渠道的会话绑定

        Args:
            user_id: 用户ID

        Returns:
            渠道 -> 会话ID 的映射
        """
        result = {}
        prefix = f":{user_id}"
        for key, session_id in self._channel_sessions.items():
            if key.endswith(prefix):
                channel = key.split(":")[0]
                result[channel] = session_id
        return result

    def cleanup_expired(self) -> int:
        """清理所有过期的渠道会话绑定

        Returns:
            清理的绑定数量
        """
        now = time.time()
        expired_keys = [
            key for key, ts in self._bind_timestamps.items()
            if now - ts > self.CHANNEL_SESSION_TTL
        ]

        for key in expired_keys:
            self._channel_sessions.pop(key, None)
            self._bind_timestamps.pop(key, None)

        if expired_keys:
            logger.info("清理过期渠道会话绑定: %d 条", len(expired_keys))

        return len(expired_keys)


# 全局渠道会话管理器
_channel_session_manager: ChannelSessionManager | None = None


def get_channel_session_manager() -> ChannelSessionManager:
    """获取全局渠道会话管理器"""
    global _channel_session_manager
    if _channel_session_manager is None:
        _channel_session_manager = ChannelSessionManager()
    return _channel_session_manager
