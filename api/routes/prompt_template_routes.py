"""Prompt 模板库路由"""

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from agent.core.prompt_library import (
    create_template,
    get_template,
    list_templates,
    update_template,
    delete_template,
    render_template,
    recommend_templates,
    rate_template,
    PromptTemplate,
    PromptCategory,
    PromptVariable,
    PromptExecution,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompt-templates", tags=["Prompt模板库"])


class CreateTemplateRequest(BaseModel):
    """创建模板请求"""

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    category: PromptCategory = PromptCategory.CUSTOM
    template: str = Field(min_length=1, max_length=8000)
    variables: list[PromptVariable] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_public: bool = True


class RenderTemplateRequest(BaseModel):
    """渲染模板请求"""

    variables: dict[str, str] = Field(default_factory=dict)


class RateTemplateRequest(BaseModel):
    """评分请求"""

    rating: float = Field(ge=1.0, le=5.0)


@router.get("", response_model=list[PromptTemplate], summary="列出Prompt模板")
async def api_list_templates(
    category: PromptCategory | None = None,
    keyword: str = Query(default=""),
    tags: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> list[PromptTemplate]:
    """列出 Prompt 模板"""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    return list_templates(category=category, tags=tag_list, keyword=keyword, limit=limit, offset=offset)


@router.get("/recommend", response_model=list[PromptTemplate], summary="推荐Prompt模板")
async def api_recommend_templates(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[PromptTemplate]:
    """智能推荐模板"""
    return recommend_templates(query, limit)


@router.get("/categories", summary="列出Prompt分类")
async def api_list_categories() -> dict:
    """列出模板分类"""
    return {
        "categories": [
            {"id": "writing", "name": "写作", "description": "文章、文案等写作场景"},
            {"id": "analysis", "name": "分析", "description": "数据分析、洞察场景"},
            {"id": "coding", "name": "编程", "description": "代码编写、审查场景"},
            {"id": "translation", "name": "翻译", "description": "多语言翻译场景"},
            {"id": "summary", "name": "摘要", "description": "文档摘要、提炼场景"},
            {"id": "brainstorm", "name": "创意", "description": "头脑风暴、方案生成"},
            {"id": "email", "name": "邮件", "description": "邮件撰写、回复场景"},
            {"id": "meeting", "name": "会议", "description": "会议纪要、议程场景"},
            {"id": "report", "name": "报告", "description": "周报、月报等报告场景"},
            {"id": "custom", "name": "自定义", "description": "用户自定义模板"},
        ]
    }


@router.get("/{template_id}", response_model=PromptTemplate, summary="获取Prompt模板详情")
async def api_get_template(template_id: str) -> PromptTemplate:
    """获取模板详情"""
    tpl = get_template(template_id)
    if not tpl:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="模板不存在")
    return tpl


@router.post("", response_model=PromptTemplate, summary="创建Prompt模板")
async def api_create_template(request: CreateTemplateRequest) -> PromptTemplate:
    """创建 Prompt 模板"""
    template = PromptTemplate(
        name=request.name,
        description=request.description,
        category=request.category,
        template=request.template,
        variables=request.variables,
        tags=request.tags,
        is_public=request.is_public,
    )
    return create_template(template)


@router.put("/{template_id}", response_model=PromptTemplate, summary="更新Prompt模板")
async def api_update_template(template_id: str, request: CreateTemplateRequest) -> PromptTemplate:
    """更新模板"""
    result = update_template(template_id, request.model_dump(exclude_unset=True))
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="模板不存在")
    return result


@router.delete("/{template_id}", summary="删除Prompt模板")
async def api_delete_template(template_id: str) -> dict:
    """删除模板"""
    success = delete_template(template_id)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="模板不存在")
    return {"status": "ok"}


@router.post("/{template_id}/render", response_model=PromptExecution, summary="渲染Prompt模板")
async def api_render_template(template_id: str, request: RenderTemplateRequest) -> PromptExecution:
    """渲染模板"""
    result = render_template(template_id, request.variables)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="模板不存在")
    return result


@router.post("/{template_id}/rate", response_model=PromptTemplate, summary="评价Prompt模板")
async def api_rate_template(template_id: str, request: RateTemplateRequest) -> PromptTemplate:
    """为模板评分"""
    result = rate_template(template_id, request.rating)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="模板不存在")
    return result
