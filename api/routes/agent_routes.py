"""Agent 交互路由

提供同步和流式两种对话接口:
  - POST /agent/chat: 同步对话，等待完整响应后返回
  - POST /agent/chat/stream: 流式对话，SSE 逐 Token 推送响应
  - GET /agent/events/{session_id}: SSE 事件流，实时推送事件总线事件
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.errors import AppException, ErrorCode
from api.models.request import ChatRequest, TaskResumeRequest, TaskStepRetryRequest, TaskCancelRequest, TaskConfirmRequest
from api.models.response import ChatResponse
from agent.core.feedback import FeedbackType
from agent.core.session_manager import get_session_manager
from agent.teams.routing import route_and_execute, route_and_execute_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse, summary="同步对话")
async def chat(request: ChatRequest) -> ChatResponse:
    """处理用户对话请求（同步模式）

    流程：获取/创建会话 -> 追加用户消息 -> Supervisor 路由 -> Agent 执行 -> 返回结果
    """
    session_mgr = await get_session_manager()

    if request.session_id:
        session = await session_mgr.get_session(request.session_id)
        if session is None:
            raise AppException(ErrorCode.SESSION_NOT_FOUND)
    else:
        session = await session_mgr.create_session(
            user_id=request.user_id, channel=request.channel
        )

    await session_mgr.append_message(
        session_id=session.session_id,
        role="user",
        content=request.message,
    )

    result = await route_and_execute(
        user_message=request.message,
        session_id=session.session_id,
        user_id=request.user_id,
        session=session,
        knowledge_base_id=request.knowledge_base_id,
    )

    reply = result.get("message", "处理完成")
    agent_name = result.get("agent_name", "Supervisor")
    intent = result.get("intent")
    collaboration_mode = result.get("collaboration_mode")

    await session_mgr.append_message(
        session_id=session.session_id,
        role="assistant",
        content=reply,
        metadata={
            "agent": agent_name,
            "intent": intent,
            "collaboration_mode": collaboration_mode,
        },
    )

    # 自动归档到 L3，确保历史列表可查询
    try:
        await session_mgr.archive_session(session.session_id)
    except Exception:
        logger.debug("自动归档会话失败: %s", session.session_id)

    return ChatResponse(
        session_id=session.session_id,
        message=reply,
        agent_name=agent_name,
        intent=intent,
        collaboration_mode=collaboration_mode,
    )


@router.post("/chat/stream", summary="流式对话")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """处理用户对话请求（流式模式）

    通过 SSE (Server-Sent Events) 逐 Token 推送 Agent 响应。
    使用 AutoGen 的 run_stream() 实现真正的 Token 级流式输出，
    LLM 每生成一个 Token 即推送到客户端，显著降低首 Token 延迟。
    """
    session_mgr = await get_session_manager()

    if request.session_id:
        session = await session_mgr.get_session(request.session_id)
        if session is None:
            raise AppException(ErrorCode.SESSION_NOT_FOUND)
    else:
        session = await session_mgr.create_session(
            user_id=request.user_id, channel=request.channel
        )

    await session_mgr.append_message(
        session_id=session.session_id,
        role="user",
        content=request.message,
    )

    async def event_generator():
        try:
            yield _format_sse("session_id", session.session_id)

            full_message = ""
            final_agent = "Supervisor"
            final_intent = ""
            final_mode = ""

            # 创建事件总线订阅，并行监听事件总线事件
            event_bus_gen: AsyncGenerator[str, None] | None = None
            try:
                from agent.core.event_bus import subscribe_events, EventType

                _event_types = [
                    EventType.TOOL_CALL,
                    EventType.TOOL_RESULT,
                    EventType.GUARDRAIL_BLOCK,
                    EventType.APPROVAL_PENDING,
                    EventType.CONTEXT_COMPACTION,
                    EventType.DEGRADATION,
                ]

                async def _listen_event_bus() -> AsyncGenerator[str, None]:
                    async for event in subscribe_events(session.session_id, _event_types):
                        if event.data.get("heartbeat"):
                            continue
                        yield _format_sse("bus_event", json.dumps({
                            "event_type": event.event_type.value,
                            "data": event.data,
                            "timestamp": event.timestamp,
                        }, ensure_ascii=False))

                event_bus_gen = _listen_event_bus()
            except Exception:
                event_bus_gen = None

            # 使用 asyncio 合并流式对话和事件总线事件
            stream_gen = route_and_execute_stream(
                user_message=request.message,
                session_id=session.session_id,
                user_id=request.user_id,
                session=session,
                knowledge_base_id=request.knowledge_base_id,
            )

            async def _consume_stream() -> AsyncGenerator[str, None]:
                nonlocal full_message, final_agent, final_intent, final_mode
                async for event in stream_gen:
                    event_type = event.get("type")

                    if event_type == "intent":
                        yield _format_sse("intent", json.dumps({
                            "intent": event["intent"],
                            "confidence": event["confidence"],
                            "agent": event["agent"],
                            "mode": event["mode"],
                        }, ensure_ascii=False))

                    elif event_type == "clarification":
                        yield _format_sse("chunk", event["message"])
                        yield _format_sse("status", "clarification_needed")
                        full_message = event["message"]

                    elif event_type == "chunk":
                        yield _format_sse("chunk", event["content"])
                        final_agent = event.get("agent_name", final_agent)

                    elif event_type == "tool_call":
                        yield _format_sse("tool_call", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "tools": event.get("tools", []),
                        }, ensure_ascii=False))

                    elif event_type == "tool_result":
                        yield _format_sse("tool_result", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "tool_name": event.get("tool_name", ""),
                            "is_error": event.get("is_error", False),
                            "content": event.get("content", ""),
                        }, ensure_ascii=False))

                    elif event_type == "thought":
                        yield _format_sse("thought", json.dumps({
                            "agent_name": event.get("agent_name", ""),
                            "content": event.get("content", ""),
                        }, ensure_ascii=False))

                    elif event_type == "handoff":
                        yield _format_sse("handoff", json.dumps({
                            "from_agent": event.get("from_agent", ""),
                            "to_agent": event.get("to_agent", ""),
                        }, ensure_ascii=False))

                    elif event_type == "execution_id":
                        yield _format_sse("execution_id", event.get("execution_id", ""))

                    elif event_type == "step_start":
                        # 步骤开始事件：通知前端当前执行进度
                        yield _format_sse("step_start", json.dumps({
                            "step_name": event.get("step_name", ""),
                            "agent_name": event.get("agent_name", ""),
                            "step_index": event.get("step_index", 0),
                            "total_steps": event.get("total_steps", 0),
                        }, ensure_ascii=False))

                    elif event_type == "step_done":
                        # 步骤完成事件：通知前端步骤执行结果
                        step_done_data = {
                            "step_name": event.get("step_name", ""),
                            "agent_name": event.get("agent_name", ""),
                            "step_index": event.get("step_index", 0),
                            "total_steps": event.get("total_steps", 0),
                            "status": event.get("status", ""),
                        }
                        # 携带步骤输出结果，供前端预览
                        if event.get("message"):
                            step_done_data["message"] = event["message"]
                        if event.get("error"):
                            step_done_data["error"] = event["error"]
                        yield _format_sse("step_done", json.dumps(step_done_data, ensure_ascii=False))

                    elif event_type == "complete":
                        final_agent = event.get("agent_name", final_agent)
                        final_intent = event.get("intent", "")
                        final_mode = event.get("mode", "")
                        full_message = event.get("full_message", full_message)
                        yield _format_sse("agent_name", final_agent)
                        yield _format_sse("intent", final_intent)
                        yield _format_sse("collaboration_mode", final_mode)
                        yield _format_sse("status", "completed")

                    elif event_type == "error":
                        yield _format_sse("error", event["message"])
                        yield _format_sse("status", "error")

            # 优先处理流式对话，同时尝试获取事件总线事件
            try:
                async for sse_data in _consume_stream():
                    yield sse_data

                    # 在每个 chunk 之后非阻塞地检查事件总线是否有事件
                    # 使用 asyncio.wait_for 设置短超时，避免阻塞主流程
                    if event_bus_gen is not None:
                        try:
                            while True:
                                bus_sse = await asyncio.wait_for(
                                    event_bus_gen.__anext__(),
                                    timeout=0.01,
                                )
                                yield bus_sse
                        except (asyncio.TimeoutError, StopAsyncIteration):
                            pass
            finally:
                # 确保关闭事件总线订阅，防止内存泄漏
                if event_bus_gen is not None:
                    try:
                        await event_bus_gen.aclose()
                    except Exception:
                        pass

            if full_message:
                await session_mgr.append_message(
                    session_id=session.session_id,
                    role="assistant",
                    content=full_message,
                    metadata={
                        "agent": final_agent,
                        "intent": final_intent,
                        "collaboration_mode": final_mode,
                    },
                )

                # 自动归档到 L3，确保历史列表可查询
                try:
                    await session_mgr.archive_session(session.session_id)
                except Exception:
                    logger.debug("自动归档会话失败: %s", session.session_id)

        except Exception as e:
            logger.error("流式响应异常: %s", e)
            yield _format_sse("error", str(e))
            yield _format_sse("status", "error")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event: str, data: str) -> str:
    """格式化 SSE 事件

    Args:
        event: 事件类型
        data: 事件数据

    Returns:
        SSE 格式字符串
    """
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


# ==================== 事件流 ====================


@router.get("/events/{session_id}", summary="SSE事件流")
async def event_stream(session_id: str) -> StreamingResponse:
    """SSE 事件流端点

    实时推送事件总线中该会话的所有事件，
    包括工具调用、护栏拦截、审批挂起、上下文压缩等。

    前端可通过 EventSource API 订阅此端点，
    实时展示 Agent 执行过程中的关键事件。
    """

    async def _generate():
        try:
            from agent.core.event_bus import subscribe_events, EventType

            event_types = [
                EventType.AGENT_START,
                EventType.AGENT_END,
                EventType.TOOL_CALL,
                EventType.TOOL_RESULT,
                EventType.GUARDRAIL_BLOCK,
                EventType.APPROVAL_PENDING,
                EventType.CONTEXT_COMPACTION,
                EventType.DEGRADATION,
                EventType.INTENT_CLASSIFIED,
                EventType.RETRY,
                EventType.ERROR,
            ]

            async for event in subscribe_events(session_id, event_types):
                if event.data.get("heartbeat"):
                    yield _format_sse("heartbeat", "")
                    continue

                yield _format_sse(event.event_type.value, json.dumps({
                    "session_id": event.session_id,
                    "data": event.data,
                    "timestamp": event.timestamp,
                }, ensure_ascii=False))

        except Exception as e:
            logger.error("事件流异常: %s", e)
            yield _format_sse("error", str(e))

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 对话反馈 ====================


class FeedbackRequestBody(BaseModel):
    """反馈请求体"""

    session_id: str = Field(..., description="会话ID")
    message_index: int = Field(..., description="消息在会话中的索引位置")
    feedback_type: FeedbackType = Field(..., description="反馈类型: thumbs_up / thumbs_down")
    comment: str | None = Field(default=None, description="用户补充说明")
    agent_name: str | None = Field(default=None, description="Agent名称")
    intent: str | None = Field(default=None, description="意图标签")


@router.post("/feedback", summary="提交对话反馈")
async def submit_feedback(
    body: FeedbackRequestBody,
    user_id: str = "anonymous",
) -> dict:
    """提交对话反馈（点赞/点踩）"""
    from agent.core.feedback import get_feedback_service, FeedbackRequest

    service = get_feedback_service()
    request = FeedbackRequest(
        session_id=body.session_id,
        message_index=body.message_index,
        feedback_type=body.feedback_type,
        comment=body.comment,
        user_id=user_id,
        agent_name=body.agent_name,
        intent=body.intent,
    )

    success = await service.submit_feedback(request)
    if not success:
        raise AppException(ErrorCode.INTERNAL_ERROR, message="反馈提交失败")

    return {"status": "ok", "message": "反馈已提交"}


@router.get("/feedback/stats", summary="查询反馈统计")
async def get_feedback_stats(date: str | None = None) -> dict:
    """查询反馈统计"""
    from agent.core.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_daily_stats(date)
    return stats.model_dump()


@router.get("/feedback/stats/{agent_name}", summary="查询指定Agent反馈统计")
async def get_agent_feedback_stats(agent_name: str, date: str | None = None) -> dict:
    """查询指定 Agent 的反馈统计"""
    from agent.core.feedback import get_feedback_service

    service = get_feedback_service()
    stats = await service.get_agent_stats(agent_name, date)
    return stats.model_dump()


# ==================== 任务管理 ====================


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
    """
    from agent.teams.task_execution_engine import get_task_execution_engine

    engine = get_task_execution_engine()
    result = await engine.resume(
        execution_id=request.execution_id,
        session_id=request.session_id,
        user_id=request.user_id,
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
        from agent.core.event_bus import subscribe_events, EventType
        from agent.core.task_checkpoint import get_task_checkpoint_store

        store = get_task_checkpoint_store()
        execution = await store.get_execution(execution_id)
        if execution is None:
            yield f"event: error\ndata: {json.dumps({'message': '执行记录不存在'})}\n\n"
            return

        session_id = execution.session_id

        # 订阅事件总线的任务相关事件
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
            # 仅推送与当前执行ID相关的事件
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
    from agent.core.human_confirm import get_human_confirm_manager

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
    import asyncio
    from agent.core.human_confirm import get_human_confirm_manager

    decision = body.decision
    comment = body.comment
    user_id = body.user_id

    manager = get_human_confirm_manager()
    result = await manager.handle_confirm(confirm_id, decision, comment)

    if result is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND, message=f"确认请求不存在或已处理: {confirm_id}")

    # 验证确认请求归属用户
    if user_id and result.user_id and result.user_id != user_id:
        raise AppException(ErrorCode.INTERNAL_ERROR, message="无权处理此确认请求")

    # 根据决策恢复任务执行
    if decision in ("continue", "skip", "retry"):
        from agent.teams.task_execution_engine import get_task_execution_engine
        from agent.core.task_checkpoint import get_task_checkpoint_store, TaskStatus

        store = get_task_checkpoint_store()
        execution = await store.get_execution(result.execution_id)

        if execution and execution.status == TaskStatus.PAUSED:
            engine = get_task_execution_engine()

            if decision == "retry":
                # 重试步骤也改为后台执行
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
                # skip：将当前步骤标记为SKIPPED，然后后台恢复执行后续步骤
                # 注意：不在此处修改 execution.status 为 RUNNING，由 resume() 内部处理状态转换
                from agent.core.task_checkpoint import StepStatus
                for cp in execution.checkpoints:
                    if cp.step_index == result.step_index and cp.status in (StepStatus.WAITING_CONFIRM, StepStatus.FAILED):
                        cp.status = StepStatus.SKIPPED
                        cp.error = f"用户跳过: {comment}"
                        await store.save_checkpoint(execution.execution_id, cp)
                        break
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
                # continue：后台恢复执行
                # 注意：不在此处修改 execution.status 为 RUNNING，由 resume() 内部处理状态转换
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
        from agent.core.task_checkpoint import get_task_checkpoint_store, TaskStatus

        store = get_task_checkpoint_store()
        execution = await store.get_execution(result.execution_id)
        if execution:
            execution.status = TaskStatus.CANCELLED
            execution.error = f"用户取消任务: {comment}"
            await store.update_execution(execution)
            await store.remove_from_running(result.execution_id)

    return {
        "status": "resolved",
        "confirm_id": confirm_id,
        "decision": decision,
        "execution_id": result.execution_id,
    }


@router.post("/task/cancel", summary="取消任务")
async def cancel_task(request: TaskCancelRequest) -> dict:
    """取消正在执行的任务

    将任务状态标记为CANCELLED，停止执行。
    """
    from agent.core.task_checkpoint import get_task_checkpoint_store, TaskStatus

    store = get_task_checkpoint_store()
    execution = await store.get_execution(request.execution_id)
    if execution is None:
        raise AppException(ErrorCode.SESSION_NOT_FOUND, message=f"执行记录不存在: {request.execution_id}")

    if execution.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        return {"status": "error", "message": f"任务状态不允许取消: {execution.status.value}"}

    execution.status = TaskStatus.CANCELLED
    execution.error = "用户取消任务"
    await store.update_execution(execution)
    await store.remove_from_running(request.execution_id)

    return {"status": "cancelled", "message": "任务已取消"}
