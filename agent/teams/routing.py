"""任务路由引擎

这是整个多 Agent 系统的入口模块，负责将用户请求路由到正确的 Agent 并执行。

================================================================================
模块职责
================================================================================

1. 意图分类：调用 LLM 识别用户意图（如 approval_query、email_send 等）
2. 置信度校验：低置信度时请求用户澄清
3. 团队创建：根据意图创建对应的 Agent 团队（DIRECT/SELECTOR/SWARM）
4. 上下文注入：将对话历史注入任务描述，使 Agent 理解上下文
5. 任务执行：调用 ExecutionController 执行任务（含超时控制、重试、上下文压缩）
6. 结果返回：返回执行结果或错误信息

================================================================================
核心流程
================================================================================

用户消息 -> 意图分类 -> 置信度校验 -> 创建团队 -> 注入上下文 -> 执行任务 -> 返回结果

详细步骤：
  1. classify_intent(user_message) 使用轻量模型分类意图
  2. 如果 confidence < 0.7，返回 clarification_needed 请求用户澄清
  3. create_team(intent) 根据意图创建 Agent 团队
  4. _build_contextual_task() 将对话历史注入任务描述
  5. ExecutionController.execute_with_control() 执行任务
  6. 返回执行结果

================================================================================
两种执行模式
================================================================================

1. route_and_execute()：同步执行，等待完整结果后返回
2. route_and_execute_stream()：流式执行，逐 Token 返回中间结果

================================================================================
与其他模块的关系
================================================================================

- agent.agents.supervisor：提供意图分类能力（classify_intent）
- agent.teams.team_factory：提供团队创建能力（create_team）
- agent.teams.execution_controller：提供执行控制能力（超时、重试、压缩）
- agent.core.session_manager：提供会话状态管理
- observability.tracing：提供追踪能力（Langfuse）
- observability.metrics：提供指标记录能力

================================================================================
使用示例
================================================================================

    # 同步执行
    result = await route_and_execute(
        user_message="帮我查一下待审批列表",
        session_id="session-123",
        user_id="user-456",
    )

    # 流式执行
    async for event in route_and_execute_stream(
        user_message="帮我发一封邮件",
        session_id="session-123",
        user_id="user-456",
    ):
        if event["type"] == "chunk":
            print(event["content"], end="")
        elif event["type"] == "complete":
            print("\\n完成")
"""

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from autogen_agentchat.messages import (
    TextMessage,
    ToolCallSummaryMessage,
    HandoffMessage,
    BaseAgentEvent,
    BaseChatMessage,
    ModelClientStreamingChunkEvent,
    ToolCallRequestEvent,
    ToolCallExecutionEvent,
    ThoughtEvent,
    StopMessage,
)

from agent.agents.supervisor import (
    classify_intent,
    IntentResult,
    CollaborationMode,
)
from agent.teams.team_factory import create_team
from agent.teams.execution_controller import get_execution_controller
from agent.core.session_manager import SessionState, get_session_manager
from agent.core.event_bus import publish_event, EventType
from observability.metrics import record_agent_call, record_business_task, record_intent_distribution, record_clarification
from observability.tracing import langfuse_tracer, span_cache

logger = logging.getLogger(__name__)

# 置信度阈值，低于此值需要用户确认
# 当 LLM 对意图分类的置信度低于 70% 时，不直接执行，而是请求用户澄清
CONFIDENCE_THRESHOLD = 0.7


async def route_and_execute(
    user_message: str,
    session_id: str,
    user_id: str,
    session: SessionState | None = None,
    knowledge_base_id: str | None = None,
) -> dict[str, Any]:
    """路由并执行用户请求（同步模式）

    这是整个系统的主入口函数，完成从用户消息到执行结果的完整流程。

    执行流程：
    -------------------------------------------------------------------------
    步骤 1：获取会话状态
      - 从 SessionManager 获取会话，包含对话历史和上下文摘要
      - 会话状态用于构建带上下文的任务描述

    步骤 2：意图分类
      - 调用 classify_intent() 使用轻量模型识别用户意图
      - 如果指定了 knowledge_base_id，跳过意图分类，直接路由到 KnowledgeAgent
      - 意图分类结果包含：intent（意图标签）、confidence（置信度）、
        target_agent（目标 Agent）、collaboration_mode（协作模式）

    步骤 3：置信度校验
      - 如果 confidence < 0.7，返回 clarification_needed，请求用户澄清
      - 避免在意图不明确时执行错误操作

    步骤 4：创建团队
      - 调用 create_team(intent) 根据意图创建 Agent 团队
      - DIRECT 模式：单个 Agent
      - SELECTOR 模式：SelectorGroupChat（可能包含 Reviewer）
      - SWARM 模式：Swarm（包含 Supervisor + 领域 Agent + Reviewer）

    步骤 5：构建带上下文的任务描述
      - 调用 _build_contextual_task() 将对话历史注入任务描述
      - 使 Agent 能够理解对话上下文，实现多轮对话

    步骤 6：执行任务
      - 调用 ExecutionController.execute_with_control() 执行
      - ExecutionController 负责：超时控制、重试逻辑、上下文压缩
      - 记录执行指标和追踪数据

    步骤 7：返回结果
      - 成功：返回 status=success 和 Agent 输出
      - 超时：返回 status=error 和超时提示
      - 失败：返回 status=error 和错误信息
    -------------------------------------------------------------------------

    Args:
        user_message: 用户原始消息文本
        session_id: 会话唯一标识，用于获取会话状态和追踪
        user_id: 用户唯一标识，用于审计日志和权限校验
        session: 会话状态对象（可选），传入时直接使用，否则从 SessionManager 获取
        knowledge_base_id: 知识库 ID（可选），指定后直接路由到 KnowledgeAgent，
                          跳过意图分类，用于知识库选择后的场景

    Returns:
        执行结果字典，包含以下字段：
        - status: 执行状态（success / error / clarification_needed）
        - message: Agent 输出内容或错误提示
        - agent_name: 执行的 Agent 名称
        - intent: 识别的意图标签
        - confidence: 意图分类置信度（仅 clarification_needed 时返回）
        - collaboration_mode: 协作模式（仅 success 时返回）

    示例返回值：
        # 成功
        {
            "status": "success",
            "message": "您有 3 条待审批...",
            "agent_name": "ApprovalAgent",
            "intent": "approval_query",
            "collaboration_mode": "direct"
        }

        # 需要澄清
        {
            "status": "clarification_needed",
            "message": "我不太确定您的意图（置信度: 65%），请更详细地描述您的需求。",
            "intent": "general",
            "confidence": 0.65
        }

        # 执行失败
        {
            "status": "error",
            "message": "任务执行超时（超过 600s），请简化请求或稍后重试。",
            "agent_name": "KnowledgeAgent",
            "intent": "knowledge_query"
        }
    """
    start_time = time.time()

    # 步骤 1：获取会话状态（L2 短期记忆）
    # 会话状态包含对话历史和上下文摘要，用于构建带上下文的任务描述
    if session is None:
        session_mgr = await get_session_manager()
        session = await session_mgr.get_session(session_id)

    # 步骤 2：意图分类
    # 使用轻量模型（qwen-turbo）识别用户意图
    # 如果指定了知识库 ID，跳过意图分类，直接路由到 KnowledgeAgent
    if knowledge_base_id:
        # 知识库直路由：用户已选择知识库，直接路由到 KnowledgeAgent
        intent = IntentResult(
            intent="knowledge_query",
            confidence=1.0,
            target_agent="KnowledgeAgent",
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )
        logger.info(
            "知识库直路由: kb_id=%s agent=%s",
            knowledge_base_id,
            intent.target_agent,
        )
    else:
        # 正常意图分类：调用 LLM 识别意图
        intent = await classify_intent(user_message)
        logger.info(
            "意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
            intent.intent,
            intent.confidence,
            intent.target_agent,
            intent.collaboration_mode.value,
        )

    # 记录意图分布业务指标
    try:
        record_intent_distribution(intent.intent, intent.confidence)
    except Exception:
        pass

    # 记录意图分类 Span（用于 Langfuse 追踪）
    try:
        intent_duration = (time.time() - start_time) * 1000
        langfuse_tracer.trace_intent_classification(
            trace_id=session_id,
            user_message=user_message,
            intent=intent.intent,
            confidence=intent.confidence,
            target_agent=intent.target_agent,
            duration_ms=intent_duration,
        )
        await span_cache.store_span(
            session_id=session_id,
            span_type="intent_classification",
            input_data={"user_message": user_message},
            output_data={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "mode": intent.collaboration_mode.value,
            },
            duration_ms=intent_duration,
        )
    except Exception:
        pass

    # 发布意图分类事件（用于事件驱动架构）
    try:
        await publish_event(
            EventType.INTENT_CLASSIFIED,
            session_id,
            {
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "mode": intent.collaboration_mode.value,
            },
        )
    except Exception:
        pass

    # 步骤 3：置信度校验
    # 当置信度低于阈值时，不直接执行，而是请求用户澄清
    # 避免在意图不明确时执行错误操作（如误发邮件）
    if intent.confidence < CONFIDENCE_THRESHOLD:
        # 记录需要用户澄清的业务指标
        try:
            record_clarification(intent.intent)
        except Exception:
            pass
        return {
            "status": "clarification_needed",
            "message": f"我不太确定您的意图（置信度: {intent.confidence:.0%}），请更详细地描述您的需求。",
            "intent": intent.intent,
            "confidence": intent.confidence,
        }

    # 步骤 3.5：SWARM 模式委托给 TaskExecutionEngine
    # SWARM 模式涉及多Agent协作，使用引擎编排步骤并保存检查点
    if intent.collaboration_mode == CollaborationMode.SWARM:
        try:
            from agent.teams.task_execution_engine import get_task_execution_engine
            from agent.core.task_checkpoint import FailurePolicy

            engine = get_task_execution_engine()
            result = await engine.execute(
                user_message=user_message,
                session_id=session_id,
                user_id=user_id,
                intent=intent,
                session=session,
                knowledge_base_id=knowledge_base_id,
                failure_policy=FailurePolicy.RELAXED,
            )
            return result
        except Exception as e:
            logger.error("TaskExecutionEngine 执行失败，降级为原有流程: %s", e)

    # 步骤 4：创建团队
    # 根据意图的 collaboration_mode 创建对应的 Agent 团队
    try:
        team = await create_team(intent)
    except Exception as e:
        logger.error("创建团队失败: %s", e)
        return {
            "status": "error",
            "message": "系统暂时无法处理您的请求，请稍后重试。",
            "intent": intent.intent,
        }

    # 步骤 5：构建带上下文的任务描述
    # 将对话历史注入任务描述，使 Agent 能够理解多轮对话上下文
    task = await _build_contextual_task(user_message, intent, session, knowledge_base_id)

    # 发布 Agent 启动事件
    try:
        await publish_event(
            EventType.AGENT_START,
            session_id,
            {
                "agent_name": intent.target_agent,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
            },
        )
    except Exception:
        pass

    # 步骤 6：执行任务
    # 调用 ExecutionController 执行，包含超时控制、重试逻辑、上下文压缩
    try:
        controller = get_execution_controller()
        result, exec_meta = await controller.execute_with_control(
            team, task, session_id, user_id,
        )

        # 处理超时
        if exec_meta.status == "timeout":
            duration = time.time() - start_time
            record_agent_call(intent.target_agent, "error", duration)
            try:
                record_business_task(intent.intent, intent.target_agent, "timeout", duration)
            except Exception:
                pass
            try:
                await publish_event(EventType.ERROR, session_id, {
                    "agent_name": intent.target_agent,
                    "error": "timeout",
                    "duration_ms": round(duration * 1000),
                })
            except Exception:
                pass
            return {
                "status": "error",
                "message": f"任务执行超时（超过 {controller._config.max_runtime}s），请简化请求或稍后重试。",
                "agent_name": intent.target_agent,
                "intent": intent.intent,
            }

        # 处理执行错误
        if exec_meta.status == "error" and result is None:
            duration = time.time() - start_time
            record_agent_call(intent.target_agent, "error", duration)
            try:
                record_business_task(intent.intent, intent.target_agent, "error", duration)
            except Exception:
                pass
            logger.error("任务执行失败: agent=%s error=%s", intent.target_agent, exec_meta.message)
            try:
                await publish_event(EventType.ERROR, session_id, {
                    "agent_name": intent.target_agent,
                    "error": exec_meta.message,
                    "duration_ms": round(duration * 1000),
                })
            except Exception:
                pass
            return {
                "status": "error",
                "message": f"任务执行失败: {exec_meta.message}",
                "agent_name": intent.target_agent,
                "intent": intent.intent,
            }

        # 提取 Agent 输出（使用 _extract_agent_response 过滤工具调用和原始 JSON）
        from agent.teams.advanced_orchestration import _extract_agent_response
        output = _extract_agent_response(result) if result else "处理完成"

        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "success", duration)
        try:
            record_business_task(intent.intent, intent.target_agent, "success", duration)
        except Exception:
            pass

        # 记录审计日志
        try:
            from agent.core.audit import audit_log, AuditEventType
            await audit_log(
                event_type=AuditEventType.AGENT,
                action="task_execute",
                user_id=user_id,
                session_id=session_id,
                agent_name=intent.target_agent,
                detail={
                    "intent": intent.intent,
                    "mode": intent.collaboration_mode.value,
                    "duration_ms": round(duration * 1000),
                    "status": "success",
                    "retries": exec_meta.retries,
                    "compacted": exec_meta.compacted,
                },
            )
        except Exception:
            pass

        # 记录 Langfuse 追踪
        langfuse_tracer.trace_agent_call(
            trace_id=session_id,
            agent_name=intent.target_agent,
            input_text=user_message,
            output_text=output,
            metadata={
                "user_id": user_id,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
                "review_required": intent.review_required,
                "retries": exec_meta.retries,
                "compacted": exec_meta.compacted,
            },
        )

        # 记录 Span 和统计
        try:
            await span_cache.store_span(
                session_id=session_id,
                span_type="agent_call",
                input_data={"user_message": user_message, "intent": intent.intent},
                output_data={"message": output[:500], "agent": intent.target_agent},
                duration_ms=duration * 1000,
                metadata={"retries": exec_meta.retries, "compacted": exec_meta.compacted},
            )
            await span_cache.increment_agent_stats(
                intent.target_agent, duration * 1000, success=True,
            )
        except Exception:
            pass

        # 发布 Agent 完成事件
        try:
            await publish_event(EventType.AGENT_END, session_id, {
                "agent_name": intent.target_agent,
                "status": "success",
                "duration_ms": round(duration * 1000),
                "retries": exec_meta.retries,
                "compacted": exec_meta.compacted,
            })
        except Exception:
            pass

        return {
            "status": "success",
            "message": output,
            "agent_name": intent.target_agent,
            "intent": intent.intent,
            "collaboration_mode": intent.collaboration_mode.value,
        }

    except Exception as e:
        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "error", duration)
        try:
            record_business_task(intent.intent, intent.target_agent, "error", duration)
        except Exception:
            pass
        logger.error("任务执行失败: agent=%s error=%s", intent.target_agent, e)

        # 记录失败统计
        try:
            await span_cache.increment_agent_stats(
                intent.target_agent, duration * 1000, success=False,
            )
        except Exception:
            pass

        # 记录失败审计日志
        try:
            from agent.core.audit import audit_log, AuditEventType
            await audit_log(
                event_type=AuditEventType.AGENT,
                action="task_execute_failed",
                user_id=user_id,
                session_id=session_id,
                agent_name=intent.target_agent,
                detail={
                    "intent": intent.intent,
                    "mode": intent.collaboration_mode.value,
                    "duration_ms": round(duration * 1000),
                    "status": "error",
                    "error": str(e)[:200],
                },
            )
        except Exception:
            pass

        return {
            "status": "error",
            "message": f"任务执行失败: {str(e)}",
            "agent_name": intent.target_agent,
            "intent": intent.intent,
        }
    finally:
        # After-turn 知识提取（异步，不阻塞主流程）
        # 从对话中提取有价值的知识存储到长期记忆
        try:
            from agent.core.context_manager import extract_and_store_knowledge
            tenant_id = ""
            try:
                from security.tenant import get_current_tenant_id
                tenant_id = get_current_tenant_id() or ""
            except Exception:
                pass
            if session and session.message_history:
                await extract_and_store_knowledge(
                    user_id=user_id,
                    session_id=session_id,
                    messages=session.message_history,
                    tenant_id=tenant_id,
                )
        except Exception:
            pass


async def _build_contextual_task(
    user_message: str,
    intent: IntentResult,
    session: SessionState | None,
    knowledge_base_id: str | None = None,
) -> str:
    """构建带上下文的任务描述

    将会话历史注入到任务描述中，使 Agent 能够理解对话上下文，实现多轮对话。

    注入内容：
    -------------------------------------------------------------------------
    1. 知识库上下文（如果指定了 knowledge_base_id）
       - 注入知识库 ID
       - 引导 Agent 使用知识库检索工具

    2. 前情摘要（如果会话有 context_summary）
       - 之前对话的压缩摘要
       - 帮助 Agent 快速了解历史背景

    3. 对话历史（最近 6 轮）
       - 用户和助手的对话记录
       - 使 Agent 能够理解上下文引用（如"它"、"那个"等）
    -------------------------------------------------------------------------

    Args:
        user_message: 用户原始消息
        intent: 意图分类结果
        session: 会话状态，包含对话历史和上下文摘要
        knowledge_base_id: 知识库 ID（可选），指定后注入到任务上下文

    Returns:
        带上下文的任务描述，格式如下：

        [知识库ID] kb-123
        请使用知识库检索工具在指定知识库中搜索相关信息来回答用户问题。

        [前情摘要] 用户之前询问了项目进度...

        [对话历史]
        用户: 帮我查一下项目进度
        助手: 项目 A 进度 80%...

        [当前请求] 那项目 B 呢？
    """
    parts = []

    # 注入知识库上下文
    if knowledge_base_id:
        parts.append(f"[知识库ID] {knowledge_base_id}")
        parts.append("请使用知识库检索工具在指定知识库中搜索相关信息来回答用户问题。")

    if session is not None and session.message_history:
        # 提取最近的对话历史（取最近 6 轮，避免过长）
        recent_history = session.message_history[-6:]
        history_parts = []
        for msg in recent_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                role_label = "用户" if role == "user" else "助手"
                history_parts.append(f"{role_label}: {content}")

        if history_parts:
            history_text = "\n".join(history_parts)

            # 注入前情摘要（如果有）
            if session.context_summary:
                parts.append(f"[前情摘要] {session.context_summary}")

            parts.append(f"[对话历史]\n{history_text}")

    parts.append(f"[当前请求] {user_message}")

    return "\n\n".join(parts)


async def route_and_execute_stream(
    user_message: str,
    session_id: str,
    user_id: str,
    session: SessionState | None = None,
    knowledge_base_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """路由并流式执行用户请求

    与 route_and_execute 流程一致，但使用 AutoGen 的 run_stream()
    逐 Token 产出中间结果，实现真正的流式输出。

    流式输出适用于：
    - 长文本生成（如报告、邮件起草）
    - 实时反馈用户体验
    - 前端打字机效果展示

    流程与 route_and_execute 相同，此处不再赘述。

    Yields:
        流式事件字典，按时间顺序输出以下类型：

        1. 意图分类结果：
           {"type": "intent", "intent": "approval_query", "confidence": 0.95,
            "agent": "ApprovalAgent", "mode": "direct"}

        2. 需要澄清：
           {"type": "clarification", "message": "我不太确定您的意图..."}

        3. 错误：
           {"type": "error", "message": "任务执行失败: ..."}

        4. 流式内容块（核心输出）：
           {"type": "chunk", "agent_name": "ApprovalAgent", "content": "您有"}

        5. 执行完成：
           {"type": "complete", "agent_name": "ApprovalAgent",
            "intent": "approval_query", "mode": "direct"}

        6. 控制事件（内部使用，通常不展示给用户）：
           {"type": "retry", "attempt": 1, "max_retries": 2}
           {"type": "compacted", "original_tokens": 100000, "compacted_tokens": 50000}

    使用示例：
        async for event in route_and_execute_stream(
            user_message="帮我写一封邮件",
            session_id="session-123",
            user_id="user-456",
        ):
            if event["type"] == "chunk":
                print(event["content"], end="", flush=True)
            elif event["type"] == "complete":
                print("\\n[完成]")
            elif event["type"] == "error":
                print(f"\\n[错误] {event['message']}")
    """
    start_time = time.time()

    if session is None:
        session_mgr = await get_session_manager()
        session = await session_mgr.get_session(session_id)

    # 步骤 1：意图分类
    if knowledge_base_id:
        intent = IntentResult(
            intent="knowledge_query",
            confidence=1.0,
            target_agent="KnowledgeAgent",
            collaboration_mode=CollaborationMode.DIRECT,
            review_required=False,
        )
        logger.info(
            "流式-知识库直路由: kb_id=%s agent=%s",
            knowledge_base_id,
            intent.target_agent,
        )
    else:
        intent = await classify_intent(user_message)
        logger.info(
            "流式-意图分类: intent=%s confidence=%.2f agent=%s mode=%s",
            intent.intent,
            intent.confidence,
            intent.target_agent,
            intent.collaboration_mode.value,
        )

    # 记录意图分类 Span
    try:
        intent_duration = (time.time() - start_time) * 1000
        langfuse_tracer.trace_intent_classification(
            trace_id=session_id,
            user_message=user_message,
            intent=intent.intent,
            confidence=intent.confidence,
            target_agent=intent.target_agent,
            duration_ms=intent_duration,
        )
        await span_cache.store_span(
            session_id=session_id,
            span_type="intent_classification",
            input_data={"user_message": user_message},
            output_data={
                "intent": intent.intent,
                "confidence": intent.confidence,
                "target_agent": intent.target_agent,
                "mode": intent.collaboration_mode.value,
            },
            duration_ms=intent_duration,
        )
    except Exception:
        pass

    # 输出意图分类结果
    yield {
        "type": "intent",
        "intent": intent.intent,
        "confidence": intent.confidence,
        "agent": intent.target_agent,
        "mode": intent.collaboration_mode.value,
    }

    # 发布意图分类事件
    try:
        await publish_event(EventType.INTENT_CLASSIFIED, session_id, {
            "intent": intent.intent,
            "confidence": intent.confidence,
            "target_agent": intent.target_agent,
            "mode": intent.collaboration_mode.value,
        })
    except Exception:
        pass

    # 步骤 2：置信度校验
    if intent.confidence < CONFIDENCE_THRESHOLD:
        yield {
            "type": "clarification",
            "message": f"我不太确定您的意图（置信度: {intent.confidence:.0%}），请更详细地描述您的需求。",
        }
        return

    # 步骤 2.5：SWARM 模式委托给 TaskExecutionEngine（异步执行，流式输出进度和结果）
    if intent.collaboration_mode == CollaborationMode.SWARM:
        try:
            from agent.teams.task_execution_engine import get_task_execution_engine
            from agent.core.task_checkpoint import FailurePolicy

            engine = get_task_execution_engine()

            # 先创建执行记录，获取 execution_id 以便前端订阅任务进度
            execution = await engine.create_execution_record(
                user_message=user_message,
                session_id=session_id,
                user_id=user_id,
                intent=intent,
                session=session,
                knowledge_base_id=knowledge_base_id,
                failure_policy=FailurePolicy.RELAXED,
            )

            # 输出 execution_id，让前端可以立即订阅任务事件
            if execution.execution_id:
                yield {
                    "type": "execution_id",
                    "execution_id": execution.execution_id,
                }

            # 逐步执行并输出步骤进度事件
            result = None
            async for event in engine.execute_with_progress(
                execution=execution,
                user_message=user_message,
                session_id=session_id,
                user_id=user_id,
                intent=intent,
                session=session,
                knowledge_base_id=knowledge_base_id,
            ):
                if event.get("type") == "step_start":
                    # 步骤开始事件，直接传递
                    yield event
                elif event.get("type") == "step_done":
                    # 步骤完成事件，直接传递
                    yield event
                elif event.get("type") == "result":
                    # 执行结果，保存并后续处理
                    result = event

            # 将最终结果作为 chunk 输出，确保前端消息气泡有内容
            final_message = result.get("message", "") if result else ""
            result_status = result.get("status", "") if result else ""

            if result_status == "paused":
                if final_message:
                    yield {
                        "type": "chunk",
                        "agent_name": result.get("agent_name", intent.target_agent) if result else intent.target_agent,
                        "content": final_message,
                    }
                yield {
                    "type": "paused",
                    "agent_name": result.get("agent_name", intent.target_agent) if result else intent.target_agent,
                    "intent": result.get("intent", intent.intent) if result else intent.intent,
                    "mode": intent.collaboration_mode.value,
                    "execution_id": result.get("execution_id", execution.execution_id),
                }
                return

            if final_message:
                yield {
                    "type": "chunk",
                    "agent_name": result.get("agent_name", intent.target_agent) if result else intent.target_agent,
                    "content": final_message,
                }

            yield {
                "type": "complete",
                "agent_name": result.get("agent_name", intent.target_agent) if result else intent.target_agent,
                "intent": result.get("intent", intent.intent) if result else intent.intent,
                "mode": intent.collaboration_mode.value,
                "full_message": final_message,
            }
            return
        except Exception as e:
            logger.error("流式-TaskExecutionEngine 执行失败，降级为原有流程: %s", e)

    # 步骤 3：创建团队
    try:
        team = await create_team(intent)
    except Exception as e:
        logger.error("流式-创建团队失败: %s", e)
        yield {
            "type": "error",
            "message": "系统暂时无法处理您的请求，请稍后重试。",
        }
        return

    # 步骤 4：构建带上下文的任务描述
    task = await _build_contextual_task(user_message, intent, session, knowledge_base_id)

    # 发布 Agent 启动事件
    try:
        await publish_event(EventType.AGENT_START, session_id, {
            "agent_name": intent.target_agent,
            "intent": intent.intent,
            "mode": intent.collaboration_mode.value,
        })
    except Exception:
        pass

    # 步骤 5：流式执行任务
    full_response = ""
    current_agent = intent.target_agent

    try:
        controller = get_execution_controller()
        async for message in controller.execute_stream_with_control(
            team, task, session_id, user_id,
        ):
            # 处理 ExecutionController 的控制事件
            if isinstance(message, dict):
                msg_type = message.get("type", "")
                if msg_type == "timeout":
                    duration = time.time() - start_time
                    record_agent_call(intent.target_agent, "error", duration)
                    yield {
                        "type": "error",
                        "message": message.get("message", "任务执行超时"),
                    }
                    return
                elif msg_type == "retry":
                    logger.info(
                        "流式执行重试: attempt=%d/%d",
                        message.get("attempt", 0),
                        message.get("max_retries", 0),
                    )
                    continue
                elif msg_type == "compacted":
                    logger.info(
                        "流式执行上下文压缩: %d -> %d tokens",
                        message.get("original_tokens", 0),
                        message.get("compacted_tokens", 0),
                    )
                    continue
                elif msg_type == "error":
                    duration = time.time() - start_time
                    record_agent_call(intent.target_agent, "error", duration)
                    yield {
                        "type": "error",
                        "message": message.get("message", "任务执行失败"),
                    }
                    return
                continue

            # 处理 AutoGen BaseAgentEvent（流式 Token、工具调用等）
            if isinstance(message, BaseAgentEvent):
                if isinstance(message, ModelClientStreamingChunkEvent):
                    # 逐 Token 流式输出事件：LLM 每生成一个 Token 即推送
                    chunk_content = message.content or ""
                    if chunk_content:
                        full_response += chunk_content
                        yield {
                            "type": "chunk",
                            "agent_name": message.source or current_agent,
                            "content": chunk_content,
                        }
                elif isinstance(message, ToolCallRequestEvent):
                    # 工具调用请求事件：通知前端 Agent 正在调用工具
                    tool_names = [tc.name for tc in message.content] if message.content else []
                    logger.debug("流式-工具调用请求: agent=%s tools=%s", message.source, tool_names)
                    yield {
                        "type": "tool_call",
                        "agent_name": message.source or current_agent,
                        "tools": tool_names,
                    }
                elif isinstance(message, ToolCallExecutionEvent):
                    # 工具调用结果事件：通知前端工具执行状态
                    for result in message.content:
                        result_content = result.content if hasattr(result, "content") else ""
                        is_error = result.is_error if hasattr(result, "is_error") else False
                        tool_name = result.name if hasattr(result, "name") else ""
                        if result_content:
                            full_response += str(result_content)
                        yield {
                            "type": "tool_result",
                            "agent_name": message.source or current_agent,
                            "tool_name": tool_name,
                            "is_error": is_error,
                            "content": str(result_content)[:500] if result_content else "",
                        }
                elif isinstance(message, ThoughtEvent):
                    # Agent 思考过程事件：可用于前端展示"正在思考"
                    logger.debug("流式-Agent思考: agent=%s", message.source)
                    yield {
                        "type": "thought",
                        "agent_name": message.source or current_agent,
                        "content": message.content or "",
                    }
                # 其他 BaseAgentEvent 子类（SelectSpeakerEvent 等）忽略
                continue

            # 处理 AutoGen BaseChatMessage（最终消息）
            if isinstance(message, BaseChatMessage):
                if isinstance(message, TextMessage):
                    content = message.content or ""
                    if content:
                        full_response += content
                        yield {
                            "type": "chunk",
                            "agent_name": current_agent,
                            "content": content,
                        }
                elif isinstance(message, ToolCallSummaryMessage):
                    # ToolCallSummaryMessage 是工具调用的摘要，不直接展示给用户
                    full_response += message.content or ""
                elif isinstance(message, HandoffMessage):
                    # Agent 切换：记录新 Agent 并通知前端
                    current_agent = message.target
                    logger.info("流式-Agent 切换: %s -> %s", intent.target_agent, current_agent)
                    yield {
                        "type": "handoff",
                        "from_agent": intent.target_agent,
                        "to_agent": current_agent,
                    }
                    # HandoffMessage.context 是 List[ChatMessage]，不作为输出内容
                elif isinstance(message, StopMessage):
                    # 终止消息
                    content = message.content or ""
                    if content:
                        full_response += content
                        yield {
                            "type": "chunk",
                            "agent_name": current_agent,
                            "content": content,
                        }
                continue

    except Exception as e:
        duration = time.time() - start_time
        record_agent_call(intent.target_agent, "error", duration)
        logger.error("流式-任务执行失败: agent=%s error=%s", intent.target_agent, e)

        # 发布错误事件
        try:
            await publish_event(EventType.ERROR, session_id, {
                "agent_name": intent.target_agent,
                "error": str(e),
                "duration_ms": round(duration * 1000),
            })
        except Exception:
            pass

        yield {
            "type": "error",
            "message": f"任务执行失败: {str(e)}",
        }
        return

    # 步骤 6：输出完成事件
    duration = time.time() - start_time
    record_agent_call(intent.target_agent, "success", duration)

    # 发布 Agent 完成事件
    try:
        await publish_event(EventType.AGENT_END, session_id, {
            "agent_name": intent.target_agent,
            "status": "success",
            "duration_ms": round(duration * 1000),
        })
    except Exception:
        pass

    # 记录 Langfuse 追踪
    try:
        langfuse_tracer.trace_agent_call(
            trace_id=session_id,
            agent_name=intent.target_agent,
            input_text=user_message,
            output_text=full_response,
            metadata={
                "user_id": user_id,
                "intent": intent.intent,
                "mode": intent.collaboration_mode.value,
            },
        )
    except Exception:
        pass

    yield {
        "type": "complete",
        "agent_name": intent.target_agent,
        "intent": intent.intent,
        "mode": intent.collaboration_mode.value,
        "full_message": full_response,
    }