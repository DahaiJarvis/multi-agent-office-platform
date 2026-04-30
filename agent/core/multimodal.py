"""多模态处理引擎

支持图像理解和语音交互，与 M365 Copilot 和 Gemini Enterprise 的多模态能力对齐。

能力：
  - 图像理解：截图分析、文档图片解析、图表数据提取
  - 语音交互：语音转文字（ASR）、文字转语音（TTS）
  - 多模态消息：统一的多模态消息格式，支持文本+图像+语音混合输入

实现策略：
  - 图像理解：基于多模态 LLM（如 qwen-vl、gpt-4o-vision）
  - 语音转文字：基于阿里云 ASR / OpenAI Whisper
  - 文字转语音：基于阿里云 TTS / OpenAI TTS
"""

import base64
import logging
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agent.core.config import get_settings

logger = logging.getLogger(__name__)


class ModalityType(str, Enum):
    """模态类型"""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class ImageFormat(str, Enum):
    """图像格式"""

    PNG = "png"
    JPEG = "jpeg"
    GIF = "gif"
    WEBP = "webp"


class AudioFormat(str, Enum):
    """音频格式"""

    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"


class MultimodalContent(BaseModel):
    """多模态内容单元"""

    type: ModalityType
    text: str = ""
    image_url: str = ""
    image_base64: str = ""
    image_format: ImageFormat = ImageFormat.JPEG
    audio_url: str = ""
    audio_base64: str = ""
    audio_format: AudioFormat = AudioFormat.WAV


class MultimodalMessage(BaseModel):
    """多模态消息"""

    contents: list[MultimodalContent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_text(self, text: str) -> "MultimodalMessage":
        self.contents.append(MultimodalContent(type=ModalityType.TEXT, text=text))
        return self

    def add_image_url(self, url: str) -> "MultimodalMessage":
        self.contents.append(MultimodalContent(type=ModalityType.IMAGE, image_url=url))
        return self

    def add_image_base64(self, data: str, fmt: ImageFormat = ImageFormat.JPEG) -> "MultimodalMessage":
        self.contents.append(MultimodalContent(type=ModalityType.IMAGE, image_base64=data, image_format=fmt))
        return self

    def add_audio_url(self, url: str, fmt: AudioFormat = AudioFormat.WAV) -> "MultimodalMessage":
        self.contents.append(MultimodalContent(type=ModalityType.AUDIO, audio_url=url, audio_format=fmt))
        return self

    def add_audio_base64(self, data: str, fmt: AudioFormat = AudioFormat.WAV) -> "MultimodalMessage":
        self.contents.append(MultimodalContent(type=ModalityType.AUDIO, audio_base64=data, audio_format=fmt))
        return self

    def to_openai_format(self) -> list[dict]:
        """转换为 OpenAI 多模态消息格式"""
        parts: list[dict] = []
        for content in self.contents:
            if content.type == ModalityType.TEXT:
                parts.append({"type": "text", "text": content.text})
            elif content.type == ModalityType.IMAGE:
                if content.image_url:
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": content.image_url},
                    })
                elif content.image_base64:
                    data_url = f"data:image/{content.image_format.value};base64,{content.image_base64}"
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    })
            elif content.type == ModalityType.AUDIO:
                if content.audio_url:
                    parts.append({
                        "type": "input_audio",
                        "input_audio": {"url": content.audio_url, "format": content.audio_format.value},
                    })
                elif content.audio_base64:
                    data_url = f"data:audio/{content.audio_format.value};base64,{content.audio_base64}"
                    parts.append({
                        "type": "input_audio",
                        "input_audio": {"url": data_url, "format": content.audio_format.value},
                    })
        return parts


class ImageAnalysisResult(BaseModel):
    """图像分析结果"""

    description: str = Field(description="图像内容描述")
    text_content: str = Field(default="", description="图像中的文字内容（OCR）")
    objects: list[str] = Field(default_factory=list, description="识别到的对象")
    chart_data: dict[str, Any] | None = Field(default=None, description="图表数据（如为图表）")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ASRResult(BaseModel):
    """语音识别结果"""

    text: str = Field(description="识别文本")
    language: str = Field(default="zh", description="语言")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    duration_seconds: float = Field(default=0.0)


class TTSRequest(BaseModel):
    """语音合成请求"""

    text: str = Field(description="待合成文本")
    voice: str = Field(default="zhixiaoxia", description="音色名称")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="语速")
    format: AudioFormat = Field(default=AudioFormat.MP3)


# ==================== 图像理解 ====================


async def analyze_image(
    image_source: str,
    prompt: str = "请描述这张图片的内容",
    is_base64: bool = False,
) -> ImageAnalysisResult:
    """分析图像内容

    使用多模态 LLM 进行图像理解。

    Args:
        image_source: 图像 URL 或 base64 编码
        prompt: 分析提示词
        is_base64: 是否为 base64 编码

    Returns:
        ImageAnalysisResult
    """
    settings = get_settings()

    message = MultimodalMessage()
    message.add_text(prompt)
    if is_base64:
        message.add_image_base64(image_source)
    else:
        message.add_image_url(image_source)

    openai_parts = message.to_openai_format()

    api_key = settings.dashscope_api_key
    base_url = settings.dashscope_base_url

    vision_model = getattr(settings, "model_qwen_vl", "qwen-vl-max")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": vision_model,
                    "messages": [
                        {"role": "user", "content": openai_parts},
                    ],
                    "max_tokens": 2000,
                },
            )

            if response.status_code != 200:
                logger.error("图像分析 API 错误: %d %s", response.status_code, response.text[:200])
                return ImageAnalysisResult(description="图像分析失败", confidence=0.0)

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            return ImageAnalysisResult(
                description=content,
                confidence=0.85,
            )

    except Exception as e:
        logger.error("图像分析异常: %s", e)
        return ImageAnalysisResult(description=f"图像分析异常: {e}", confidence=0.0)


# ==================== 语音转文字 ====================


async def speech_to_text(
    audio_source: str,
    language: str = "zh",
    is_base64: bool = False,
) -> ASRResult:
    """语音转文字

    优先使用阿里云 ASR，不可用时降级到 OpenAI Whisper。

    Args:
        audio_source: 音频 URL 或 base64 编码
        language: 语言代码
        is_base64: 是否为 base64 编码

    Returns:
        ASRResult
    """
    settings = get_settings()

    if settings.dashscope_api_key:
        return await _dashscope_asr(audio_source, language, is_base64, settings)

    if settings.openai_api_key:
        return await _openai_whisper(audio_source, language, is_base64, settings)

    return ASRResult(text="", language=language, confidence=0.0)


async def _dashscope_asr(
    audio_source: str,
    language: str,
    is_base64: bool,
    settings: Any,
) -> ASRResult:
    """阿里云语音识别"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload: dict[str, Any] = {
                "model": "sensevoice-v1",
                "input": audio_source if not is_base64 else f"data:audio/wav;base64,{audio_source}",
            }
            if language != "auto":
                payload["parameters"] = {"language": language}

            response = await client.post(
                f"{settings.dashscope_base_url}/audio/transcriptions",
                headers={
                    "Authorization": f"Bearer {settings.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code != 200:
                logger.error("阿里云 ASR 错误: %d", response.status_code)
                return ASRResult(text="", language=language, confidence=0.0)

            data = response.json()
            text = data.get("text", "")
            return ASRResult(
                text=text,
                language=language,
                confidence=0.9,
            )

    except Exception as e:
        logger.error("阿里云 ASR 异常: %s", e)
        return ASRResult(text="", language=language, confidence=0.0)


async def _openai_whisper(
    audio_source: str,
    language: str,
    is_base64: bool,
    settings: Any,
) -> ASRResult:
    """OpenAI Whisper 语音识别

    支持 base64 编码音频和 URL 音频两种输入模式。
    URL 模式下先下载音频数据再提交给 Whisper API。
    """
    try:
        if is_base64:
            audio_bytes = base64.b64decode(audio_source)
            files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        else:
            # URL 模式：先下载音频数据
            async with httpx.AsyncClient(timeout=30.0) as download_client:
                download_resp = await download_client.get(audio_source, follow_redirects=True)
                if download_resp.status_code != 200:
                    logger.error("Whisper ASR 下载音频失败: url=%s, status=%d", audio_source[:200], download_resp.status_code)
                    return ASRResult(text="", language=language, confidence=0.0)
                audio_bytes = download_resp.content

            content_type = download_resp.headers.get("content-type", "audio/wav")
            ext = "wav"
            if "mp3" in content_type or "mpeg" in content_type:
                ext = "mp3"
            elif "webm" in content_type:
                ext = "webm"
            elif "ogg" in content_type:
                ext = "ogg"
            elif "m4a" in content_type or "mp4" in content_type:
                ext = "m4a"

            files = {"file": (f"audio.{ext}", audio_bytes, content_type)}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.openai_base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={"model": "whisper-1", "language": language},
                files=files,
            )

            if response.status_code != 200:
                logger.error("Whisper ASR 错误: %d", response.status_code)
                return ASRResult(text="", language=language, confidence=0.0)

            data = response.json()
            return ASRResult(
                text=data.get("text", ""),
                language=language,
                confidence=0.9,
            )

    except Exception as e:
        logger.error("Whisper ASR 异常: %s", e)
        return ASRResult(text="", language=language, confidence=0.0)


# ==================== 文字转语音 ====================


async def text_to_speech(request: TTSRequest) -> bytes:
    """文字转语音

    优先使用阿里云 TTS，不可用时降级到 OpenAI TTS。

    Args:
        request: TTS 请求

    Returns:
        音频二进制数据
    """
    settings = get_settings()

    if settings.dashscope_api_key:
        return await _dashscope_tts(request, settings)

    if settings.openai_api_key:
        return await _openai_tts(request, settings)

    return b""


async def _dashscope_tts(request: TTSRequest, settings: Any) -> bytes:
    """阿里云语音合成"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.dashscope_base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {settings.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "cosyvoice-v1",
                    "input": request.text,
                    "voice": request.voice,
                    "speed": request.speed,
                    "response_format": request.format.value,
                },
            )

            if response.status_code == 200:
                return response.content

            logger.error("阿里云 TTS 错误: %d", response.status_code)
            return b""

    except Exception as e:
        logger.error("阿里云 TTS 异常: %s", e)
        return b""


async def _openai_tts(request: TTSRequest, settings: Any) -> bytes:
    """OpenAI 语音合成"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.openai_base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "tts-1",
                    "input": request.text,
                    "voice": "alloy",
                    "speed": request.speed,
                    "response_format": request.format.value,
                },
            )

            if response.status_code == 200:
                return response.content

            logger.error("OpenAI TTS 错误: %d", response.status_code)
            return b""

    except Exception as e:
        logger.error("OpenAI TTS 异常: %s", e)
        return b""
