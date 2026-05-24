"""Agent 任务管理路由

提供任务执行管理相关接口:
  - GET /agent/task/{execution_id}: 查询任务执行状态
  - GET /agent/task/session/{session_id}: 通过会话ID查询任务状态
  - POST /agent/task/resume: 恢复中断的任务
  - POST /agent/task/retry: 重试指定步骤
  - GET /agent/task/events/{execution_id}: SSE任务事件流
  - GET /agent/task/confirms/{user_id}: 查询用户待确认列表
  - POST /agent/task/confirm/{confirm_id}: 处理人工确认
  - POST /agent/task/cancel: 取消/暂停任务
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.errors import AppException, ErrorCode
from api.models.request import TaskResumeRequest, TaskStepRetryRequest, TaskCancelRequest, TaskConfirmRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent-Task"])


@router.get("/task/{execution_id}", summary="查询任务执行状态")
async def get_task_status(execution_id: str) -> dict:
    """查询任务执行状态

    返回任务的当前状态、步骤进度、检查点等信息，
    用于前端展示任务进度和断点恢复。
    """
    from agent.teams.task_execution_engine import get_task_execution_engine

    engine = get_task_execution_engine()
    status = await engine.get_execution_status(execution_id)
    if status is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND, message=f"任务执行记录不存在: {execution_id}")
    return status


@router.get("/task/session/{session_id}", summary="通过会话ID查询任务状态")
async def get_task_status_by_session(session_id: str) -> dict:
    """通过会话ID查询任务执行状态

    根据会话ID查找关联的任务执行记录，返回状态信息。
    """
    from agent.teams.task_execution_engine import get_task_execution_engine

    engine = get_task_execution_engine()
    status = await engine.get_execution_by_session(session_id)
    if status is None:
        return {"status": "not_found", "message": "该会话无关联的任务执行记录"}
    return status


@router.post("/task/resume", summary="恢复中断的任务")
async def resume_task(request: TaskResumeRequest) -> dict:
    """恢复中断的任务

    从断点恢复任务执行，跳过已完成的步骤。
    仅支持状态为 interrupted 或 paused 的任务。
    支持传入 supplementary_message 补充需求。
    """
    from agent.teams.task_execution_engine import get_task_execution_engine

    engine = get_task_execution_engine()
    result = await engine.resume(
        execution_id=request.execution_id,
        session_id=request.session_id,
        user_id=request.user_id,
        supplementary_message=request.supplementary_message,
    )
    return result


@router.post("/task/retry", summary="重试指定步骤")
async def retry_step(request: TaskStepRetryRequest) -> dict:
    """重试指定步骤

    使用同Agent或指定Agent重试失败的步骤。
    重试成功后，如果任务之前是暂停状态，会自动恢复执行后续步骤。
    """
    from agent.teams.task_execution_engine import get_task_execution_engine

    engine = get_task_execution_engine()
    result = await engine.retry_step(
        execution_id=request.execution_id,
        step_index=request.step_index,
        agent_name_override=request.agent_name,
    )
    return result


@router.get("/task/events/{execution_id}", summary="SSE任务事件流")
async def task_event_stream(execution_id: str) -> StreamingResponse:
    """SSE推送任务执行事件

    支持断线重连，从断点续推。
    通过事件总线订阅与任务关联的事件，实时推送给前端。
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        from agent.core.infrastructure.event_bus import subscribe_events, EventType
        from agent.core.workflow.task_checkpoint import get_task_checkpoint_store

        store = get_task_checkpoint_store()
        execution = await store.get_execution(execution_id)
        if execution is None:
            yield f"event: error\ndata: {json.dumps({'message': '执行记录不存在'})}\n\n"
            return

        session_id = execution.session_id

        task_event_types = [
            EventType.TASK_STARTED,
            EventType.TASK_COMPLETED,
            EventType.TASK_PAUSED,
            EventType.TASK_RESUMED,
            EventType.TASK_INTERRUPTED,
            EventType.STEP_COMPLETED,
            EventType.STEP_FAILED,
            EventType.AGENT_RETRY,
            EventType.AGENT_FALLBACK,
            EventType.EXECUTION_DEGRADED,
            EventType.HUMAN_CONFIRM_REQUIRED,
            EventType.TASK_STEP_START,
            EventType.TASK_STEP_COMPLETE,
        ]

        async for event in subscribe_events(session_id, task_event_types):
            event_exec_id = event.data.get("execution_id", "")
            if event_exec_id and event_exec_id != execution_id:
                continue

            event_data = {
                "event_type": event.event_type.value,
                "data": event.data,
                "timestamp": event.timestamp,
            }
            yield f"event: {event.event_type.value}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/task/confirms/{user_id}", summary="查询用户待确认列表")
async def get_pending_confirms(user_id: str) -> dict:
    """查询用户的待确认列表

    返回用户所有待处理的人工确认请求。
    """
    from agent.core.workflow.human_confirm import get_human_confirm_manager

    manager = get_human_confirm_manager()
    confirms = await manager.get_pending_confirms(user_id)

    return {
        "user_id": user_id,
        "pending_count": len(confirms),
        "confirms": [c.to_dict() for c in confirms],
    }


@router.post("/task/confirm/{confirm_id}", summary="处理人工确认")
async def resolve_confirm(confirm_id: str, body: TaskConfirmRequest) -> dict:
    """处理人工确认请求

    确认后任务恢复执行采用后台异步方式，API 立即返回，
    前端通过 SSE 事件流或轮询获取后续任务进度。

    Args:
        confirm_id: 确认单ID
        body: 确认请求体，包含 decision/comment/user_id 等字段
    """
    from agent.core.workflow.human_confirm import get_human_confirm_manager

    decision = body.decision
    comment = body.comment
    user_id = body.user_id

    manager = get_human_confirm_manager()
    result = await manager.handle_confirm(confirm_id, decision, comment)

    if result is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND, message=f"确认请求不存在或已处理: {confirm_id}")

    if user_id and result.user_id and result.user_id != user_id:
        raise AppException(ErrorCode.INTERNAL_ERROR, message="无权处理此确认请求")

    if decision in ("continue", "skip", "retry"):
        from agent.teams.task_execution_engine import get_task_execution_engine
        from agent.core.workflow.task_checkpoint import get_task_checkpoint_store, TaskStatus

        store = get_task_checkpoint_store()
        execution = await store.get_execution(result.execution_id)

        if execution and execution.status == TaskStatus.PAUSED:
            engine = get_task_execution_engine()

            if decision == "retry":
                async def _retry_background():
                    try:
                        await engine.retry_step(
                            execution_id=result.execution_id,
                            step_index=result.step_index,
                            agent_name_override=body.agent_name,
                        )
                    except Exception as e:
                        logger.error("后台重试步骤失败: execution_id=%s error=%s", result.execution_id, e)

                asyncio.create_task(_retry_background())
            elif decision == "skip":
                from agent.core.workflow.task_checkpoint import StepStatus

                for cp in execution.checkpoints:
                    if cp.step_index == result.step_index and cp.status in (StepStatus.WAITING_CONFIRM, StepStatus.FAILED):
                        cp.status = StepStatus.SKIPPED
                        cp.error = f"用户跳过: {comment}"
                        await store.save_checkpoint(execution.execution_id, cp)
                        break
                execution = await store.get_execution(result.execution_id) or execution
                execution.error = ""
                await store.update_execution(execution)

                async def _resume_background():
                    try:
                        await engine.resume(
                            execution_id=result.execution_id,
                            session_id=execution.session_id,
                            user_id=execution.user_id,
                        )
                    except Exception as e:
                        logger.error("后台恢复执行失败: execution_id=%s error=%s", result.execution_id, e)

                asyncio.create_task(_resume_background())
            else:
                from agent.core.workflow.task_checkpoint import StepStatus

                for cp in execution.checkpoints:
                    if cp.step_index == result.step_index and cp.status == StepStatus.WAITING_CONFIRM:
                        cp.status = StepStatus.COMPLETED
                        cp.output_data = cp.output_data or {}
                        cp.output_data["confirmed"] = True
                        cp.output_data["decision"] = decision
                        await store.save_checkpoint(execution.execution_id, cp)
                        break
                execution = await store.get_execution(result.execution_id) or execution
                execution.error = ""
                await store.update_execution(execution)

                async def _resume_background():
                    try:
                        await engine.resume(
                            execution_id=result.execution_id,
                            session_id=execution.session_id,
                            user_id=execution.user_id,
                        )
                    except Exception as e:
                        logger.error("后台恢复执行失败: execution_id=%s error=%s", result.execution_id, e)

                asyncio.create_task(_resume_background())

    elif decision == "cancel":
        from agent.core.workflow.task_checkpoint import get_task_checkpoint_store, TaskStatus

        store = get_task_checkpoint_store()
        execution = await store.get_execution(result.execution_id)
        if execution:
            execution.status = TaskStatus.CANCELLED
            execution.error = f"用户取消任务: {comment}"
            from agent.core.workflow.task_checkpoint import StepStatus
            for cp in execution.checkpoints:
                if cp.step_index == result.step_index and cp.status == StepStatus.WAITING_CONFIRM:
                    cp.status = StepStatus.FAILED
                    cp.error = f"用户取消: {comment}"
                    await store.save_checkpoint(execution.execution_id, cp)
                    break
            execution = await store.get_execution(result.execution_id) or execution
            await store.update_execution(execution)
            await store.remove_from_running(result.execution_id)

    return {
        "status": "resolved",
        "confirm_id": confirm_id,
        "decision": decision,
        "execution_id": result.execution_id,
    }


@router.post("/task/cancel", summary="取消/暂停任务")
async def cancel_task(request: TaskCancelRequest) -> dict:
    """取消或暂停正在执行的任务

    force=False: 暂停任务(INTERRUPTED)，可通过resume恢复
    force=True: 放弃任务(CANCELLED)，不可恢复
    """
    from agent.core.workflow.task_checkpoint import get_task_checkpoint_store, TaskStatus

    store = get_task_checkpoint_store()
    execution = await store.get_execution(request.execution_id)
    if execution is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND, message=f"执行记录不存在: {request.execution_id}")

    if execution.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        return {"status": "error", "message": f"任务状态不允许取消: {execution.status.value}"}

    if request.force:
        execution.status = TaskStatus.CANCELLED
        execution.error = "用户放弃任务"
        await store.update_execution(execution)
        await store.remove_from_running(request.execution_id)
        return {"status": "cancelled", "message": "任务已放弃"}
    else:
        execution.status = TaskStatus.INTERRUPTED
        execution.error = "用户暂停任务"
        await store.update_execution(execution)
        await store.remove_from_running(request.execution_id)
        return {"status": "interrupted", "message": "任务已暂停，可通过resume恢复"}
