"""多模态交互路由

提供图像理解、语音转文字、文字转语音 API。
"""

import base64
import logging

from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import Response

from agent.core.common.multimodal import (
    analyze_image,
    speech_to_text,
    text_to_speech,
    ImageAnalysisResult,
    ASRResult,
    TTSRequest,
    AudioFormat,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/multimodal", tags=["多模态"])


@router.post("/image/analyze", response_model=ImageAnalysisResult, summary="分析图片")
async def api_analyze_image(
    prompt: str = Form(default="请描述这张图片的内容"),
    image_url: str = Form(default=""),
    image_file: UploadFile | None = File(default=None),
) -> ImageAnalysisResult:
    """分析图像内容

    支持通过 URL 或上传文件两种方式提供图像。
    """
    if image_file:
        image_bytes = await image_file.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        return await analyze_image(image_b64, prompt, is_base64=True)
    elif image_url:
        return await analyze_image(image_url, prompt, is_base64=False)
    else:
        return ImageAnalysisResult(description="请提供图像 URL 或上传图像文件", confidence=0.0)


@router.post("/audio/transcribe", response_model=ASRResult, summary="语音转文字")
async def api_transcribe_audio(
    language: str = Form(default="zh"),
    audio_url: str = Form(default=""),
    audio_file: UploadFile | None = File(default=None),
) -> ASRResult:
    """语音转文字"""
    if audio_file:
        audio_bytes = await audio_file.read()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return await speech_to_text(audio_b64, language, is_base64=True)
    elif audio_url:
        return await speech_to_text(audio_url, language, is_base64=False)
    else:
        return ASRResult(text="", language=language, confidence=0.0)


@router.post("/audio/synthesize", summary="文字转语音")
async def api_synthesize_speech(
    text: str = Form(...),
    voice: str = Form(default="zhixiaoxia"),
    speed: float = Form(default=1.0),
    format: AudioFormat = Form(default=AudioFormat.MP3),
) -> Response:
    """文字转语音"""
    request = TTSRequest(text=text, voice=voice, speed=speed, format=format)
    audio_data = await text_to_speech(request)

    content_type = "audio/mpeg" if format == AudioFormat.MP3 else "audio/wav"
    return Response(content=audio_data, media_type=content_type)
