"""定时任务路由

提供定时任务的创建、查询、更新、删除和启用/禁用接口。
"""

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.core.message_queue import get_scheduled_task_manager, ScheduledTask

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scheduler", tags=["Scheduler"])


class TaskCreateRequest(BaseModel):
    """创建定时任务请求"""

    name: str = Field(min_length=1, description="任务名称")
    trigger_type: str = Field(description="触发类型: cron / interval")
    trigger_value: str = Field(description="Cron 表达式 或 间隔秒数")
    agent_name: str = Field(default="", description="执行的 Agent")
    task_prompt: str = Field(default="", description="任务描述")
    channel: str = Field(default="web", description="推送渠道")
    target_user: str = Field(default="", description="推送目标用户")
    tenant_id: str = Field(default="", description="租户ID")


class TaskUpdateRequest(BaseModel):
    """更新定时任务请求"""

    name: str | None = None
    trigger_type: str | None = None
    trigger_value: str | None = None
    agent_name: str | None = None
    task_prompt: str | None = None
    channel: str | None = None
    target_user: str | None = None
    enabled: bool | None = None


class TaskResponse(BaseModel):
    """定时任务响应"""

    task_id: str
    name: str
    trigger_type: str
    trigger_value: str
    agent_name: str = ""
    task_prompt: str = ""
    channel: str = "web"
    target_user: str = ""
    tenant_id: str = ""
    enabled: bool = True
    last_run_at: float = 0
    next_run_at: float = 0
    created_at: float = 0


class TaskListResponse(BaseModel):
    """定时任务列表响应"""

    items: list[TaskResponse]
    total: int


def _to_task_response(task: ScheduledTask) -> TaskResponse:
    """将 ScheduledTask 转换为 API 响应"""
    return TaskResponse(
        task_id=task.task_id,
        name=task.name,
        trigger_type=task.trigger_type,
        trigger_value=task.trigger_value,
        agent_name=task.agent_name,
        task_prompt=task.task_prompt,
        channel=task.channel,
        target_user=task.target_user,
        tenant_id=task.tenant_id,
        enabled=task.enabled,
        last_run_at=task.last_run_at,
        next_run_at=task.next_run_at,
        created_at=task.created_at,
    )


@router.post("/tasks", response_model=TaskResponse)
async def create_task(request: TaskCreateRequest) -> TaskResponse:
    """创建定时任务

    支持 Cron 和 Interval 两种触发类型。
    """
    mgr = get_scheduled_task_manager()
    task = ScheduledTask(
        name=request.name,
        trigger_type=request.trigger_type,
        trigger_value=request.trigger_value,
        agent_name=request.agent_name,
        task_prompt=request.task_prompt,
        channel=request.channel,
        target_user=request.target_user,
        tenant_id=request.tenant_id,
    )
    task_id = await mgr.schedule_task(task)
    task.task_id = task_id
    return _to_task_response(task)


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(tenant_id: str = "") -> TaskListResponse:
    """查询定时任务列表"""
    mgr = get_scheduled_task_manager()
    tasks = await mgr.list_scheduled_tasks(tenant_id=tenant_id or "")
    return TaskListResponse(
        items=[_to_task_response(t) for t in tasks],
        total=len(tasks),
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    """获取定时任务详情"""
    mgr = get_scheduled_task_manager()
    task = await mgr.get_scheduled_task(task_id)
    if task is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)
    return _to_task_response(task)


@router.put("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, request: TaskUpdateRequest) -> TaskResponse:
    """更新定时任务"""
    updates: dict[str, Any] = {}
    for field_name in [
        "name", "trigger_type", "trigger_value",
        "agent_name", "task_prompt", "channel",
        "target_user", "enabled",
    ]:
        value = getattr(request, field_name, None)
        if value is not None:
            updates[field_name] = value

    mgr = get_scheduled_task_manager()
    success = await mgr.update_scheduled_task(task_id, **updates)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)

    task = await mgr.get_scheduled_task(task_id)
    return _to_task_response(task)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str) -> dict[str, Any]:
    """删除定时任务"""
    mgr = get_scheduled_task_manager()
    success = await mgr.cancel_scheduled_task(task_id)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)
    return {"status": "deleted", "task_id": task_id}


@router.post("/tasks/{task_id}/toggle", response_model=TaskResponse)
async def toggle_task(task_id: str) -> TaskResponse:
    """启用/禁用定时任务"""
    mgr = get_scheduled_task_manager()
    task = await mgr.get_scheduled_task(task_id)
    if task is None:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.NOT_FOUND)

    new_enabled = not task.enabled
    await mgr.update_scheduled_task(task_id, enabled=new_enabled)

    task.enabled = new_enabled
    return _to_task_response(task)
