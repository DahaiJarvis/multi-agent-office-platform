"""故障隔离与恢复策略

当单个 Agent 步骤失败时，按策略优先级尝试恢复，
确保多Agent协作任务不会因单个Agent故障而整体失败。

================================================================================
恢复策略优先级
================================================================================

1. 同Agent重试（检查熔断器状态）
   - 指数退避重试，默认最多2次
   - 熔断器打开时跳过此策略

2. Agent替换（Fallback Table）
   - 前提条件1：故障层级为LLM层（MCP层故障时替换Agent也调不通，跳过替换）
   - 前提条件2：当前意图类型的任务适合替换（操作类任务替换成功率低，跳过替换）
   - 替换时继承原Agent的专业Prompt，使替换Agent获得专业能力
   - 如果无法获取原Agent的Prompt，降级使用通用降级提示词

3. 降级执行（简化任务描述）
   - 基于已有数据简化任务
   - 标注缺失数据部分

4. 标记失败继续
   - STRICT: 暂停任务，等待人工决策
   - RELAXED: 标记失败继续下一步
   - MANUAL: 暂停任务，推送通知让用户选择

================================================================================
故障层级分类
================================================================================

通过 _classify_fault() 方法判断故障层级：
- llm_failure: LLM调用失败（如API限流、模型返回错误），Agent替换可能有效
- mcp_failure: MCP工具调用失败（如服务宕机、连接超时），Agent替换无效

================================================================================
替换可行性评估
================================================================================

通过 FALLBACK_FEASIBILITY 和 _should_attempt_fallback() 方法判断：
- 查询类任务（_query）替换成功率高，允许替换
- 操作类任务（_action/_send/_create）替换成功率低，跳过替换

================================================================================
与其他模块的关系
================================================================================

- task_checkpoint.py: 读取/更新步骤检查点状态
- circuit_breaker.py: 检查Agent熔断器状态
- execution_controller.py: 执行步骤级重试
- event_bus.py: 发布故障隔离事件
- team_factory.py: 创建替换Agent
- domain.py: 获取原Agent的专业Prompt（AGENT_PROMPTS）
- prompt_registry.py: 获取外置版本管理的Prompt
"""

import asyncio
import json
import logging
from typing import Any

from agent.core.workflow.task_checkpoint import (
    TaskExecution,
    TaskCheckpointStore,
    StepCheckpoint,
    StepType,
    StepStatus,
    FailurePolicy,
    TaskStatus,
    get_task_checkpoint_store,
)
from agent.core.infrastructure.event_bus import publish_event, EventType

logger = logging.getLogger(__name__)


# Agent替换表：当某个Agent不可用时，按顺序尝试替换
AGENT_FALLBACK_TABLE: dict[str, list[str]] = {
    "EmailAgent": ["OfficeAssistant"],
    "ApprovalAgent": ["OfficeAssistant"],
    "CalendarAgent": ["OfficeAssistant"],
    "CRMAgent": ["KnowledgeAgent"],
    "HRAgent": ["KnowledgeAgent"],
    "FinanceAgent": ["KnowledgeAgent"],
    "KnowledgeAgent": ["OfficeAssistant"],
    "OfficeAssistant": [],
}

# 降级提示词模板：Agent替换时注入
FALLBACK_PROMPT_TEMPLATE = (
    "注意：这是一个降级请求。原始 Agent {original_agent} 不可用，"
    "你作为 {fallback_agent} 正在替代执行。"
    "你的能力可能与原始 Agent 不同，请尽力完成以下任务，"
    "如果超出你的能力范围，请明确说明哪些部分无法处理。\n\n"
    "原始任务：{task}"
)

# 降级提示词模板：简化任务执行时注入
DEGRADE_PROMPT_TEMPLATE = (
    "注意：由于部分数据源不可用，任务已降级。"
    "请仅基于以下可用数据完成任务，并在结果中明确标注数据缺失部分。\n\n"
    "可用数据：{available_data}\n"
    "缺失数据：{missing_data}\n"
    "原始任务：{task}"
)

# 同Agent重试最大次数
MAX_SAME_AGENT_RETRIES = 2

# 重试退避基数（秒）
RETRY_BACKOFF_BASE = 1.0

# MCP层故障关键词：用于判断故障是否来自MCP工具调用层
MCP_FAULT_KEYWORDS = [
    "mcp", "sse", "tool_call", "connection refused",
    "timeout", "tool execution", "tool server",
]

# Agent替换可行性评估：按意图类型判断替换是否可行
# 查询类任务（_query）替换成功率高，操作类任务（_action/_send/_create）替换成功率低
FALLBACK_FEASIBILITY: dict[str, bool] = {
    "approval_query": True,
    "approval_action": False,
    "email_query": True,
    "email_send": False,
    "calendar_query": True,
    "calendar_create": False,
    "crm_query": True,
    "hr_query": True,
    "hr_action": False,
    "finance_query": True,
    "finance_action": False,
    "knowledge_query": True,
    "document_parse": True,
    "document_summary": True,
    "document_compare": True,
    "report_generate": False,
    "web_search": True,
    "image_analyze": True,
    "kb_manage": False,
    "general": True,
}


class FaultIsolationPolicy:
    """故障隔离策略执行器

    当单个Agent步骤失败时，按优先级依次尝试恢复策略。
    每种策略尝试后，如果成功则返回检查点；如果失败则尝试下一种策略。

    使用方式：
        policy = FaultIsolationPolicy()
        checkpoint = await policy.handle_step_failure(
            execution=execution,
            step_index=1,
            error=ValueError("Agent调用超时"),
        )
    """

    def __init__(self, checkpoint_store: TaskCheckpointStore | None = None) -> None:
        self._store = checkpoint_store or get_task_checkpoint_store()

    async def handle_step_failure(
        self,
        execution: TaskExecution,
        step_index: int,
        error: Exception,
    ) -> StepCheckpoint:
        """处理步骤失败

        按优先级依次尝试四种恢复策略：
        1. 同Agent重试（检查熔断器状态）
        2. Agent替换（Fallback Table）
        3. 降级执行（简化任务描述）
        4. 标记失败继续（根据 failure_policy 决定是否暂停）

        Args:
            execution: 任务执行记录
            step_index: 失败步骤的索引
            error: 步骤失败的异常

        Returns:
            最终的步骤检查点（可能是成功恢复的，也可能是标记失败的）
        """
        step = self._get_step(execution, step_index)
        if step is None:
            return self._create_failed_checkpoint(execution, step_index, error, "步骤定义不存在")

        agent_name = step.get("agent_name", "")
        logger.warning(
            "步骤失败，启动故障隔离: execution=%s step=%d agent=%s error=%s",
            execution.execution_id, step_index, agent_name, str(error)[:200],
        )

        # 发布步骤失败事件
        await self._publish_fault_event(
            execution, step_index, agent_name, "step_failed", str(error),
        )

        # 判断故障层级：LLM层故障 vs MCP层故障
        fault_level = self._classify_fault(error)

        # 策略1: 同Agent重试
        retry_result = await self._retry_same_agent(execution, step_index)
        if retry_result is not None:
            return retry_result

        # 策略2: Agent替换（需满足两个前提条件）
        # 条件1: 故障层级为LLM层（MCP层故障时替换Agent也调不通，跳过替换）
        # 条件2: 当前意图类型的任务适合替换（操作类任务替换成功率低，跳过替换）
        should_fallback = (
            fault_level == "llm_failure"
            and self._should_attempt_fallback(execution)
        )
        if should_fallback:
            fallback_result = await self._fallback_agent(execution, step_index)
            if fallback_result is not None:
                return fallback_result
        else:
            skip_reason = "MCP层故障" if fault_level == "mcp_failure" else "任务类型不适合替换"
            logger.info(
                "策略2跳过-%s: execution=%s step=%d agent=%s",
                skip_reason, execution.execution_id, step_index, agent_name,
            )
            await self._publish_fault_event(
                execution, step_index, agent_name,
                "fallback_skipped", f"跳过Agent替换: {skip_reason}",
            )

        # 策略3: 降级执行
        degrade_result = await self._degrade_execution(execution, step_index)
        if degrade_result is not None:
            return degrade_result

        # 策略4: 标记失败继续
        return await self._mark_failed_continue(execution, step_index, error)

    async def _retry_same_agent(
        self,
        execution: TaskExecution,
        step_index: int,
        max_retries: int = MAX_SAME_AGENT_RETRIES,
    ) -> StepCheckpoint | None:
        """策略1: 同Agent重试，指数退避

        检查熔断器状态，如果Agent的熔断器已打开则跳过此策略。
        重试使用指数退避，避免对故障Agent造成过大压力。

        Args:
            execution: 任务执行记录
            step_index: 步骤索引
            max_retries: 最大重试次数

        Returns:
            成功时返回检查点，失败时返回None
        """
        step = self._get_step(execution, step_index)
        if step is None:
            return None

        agent_name = step.get("agent_name", "")
        if not agent_name:
            return None

        # 检查熔断器状态
        if self._is_circuit_open(agent_name):
            logger.info(
                "策略1跳过-熔断器已打开: execution=%s agent=%s",
                execution.execution_id, agent_name,
            )
            await self._publish_fault_event(
                execution, step_index, agent_name,
                "retry_skipped", "熔断器已打开，跳过同Agent重试",
            )
            return None

        logger.info(
            "策略1-同Agent重试: execution=%s step=%d agent=%s max_retries=%d",
            execution.execution_id, step_index, agent_name, max_retries,
        )

        for attempt in range(1, max_retries + 1):
            # 指数退避
            backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.info("重试等待: %.1fs (第%d次)", backoff, attempt)
            await asyncio.sleep(backoff)

            # 再次检查熔断器（重试间隔内可能已打开）
            if self._is_circuit_open(agent_name):
                logger.info(
                    "重试中止-熔断器已打开: attempt=%d agent=%s",
                    attempt, agent_name,
                )
                break

            # 发布重试事件
            await self._publish_fault_event(
                execution, step_index, agent_name,
                "agent_retry", f"同Agent重试第{attempt}次",
            )

            # 执行重试
            result = await self._execute_step_with_agent(
                execution, step, agent_name, step_index,
            )

            if result.status == StepStatus.COMPLETED:
                result.retry_count = attempt
                result.fallback_used = "same_agent_retry"
                logger.info(
                    "同Agent重试成功: execution=%s step=%d attempt=%d",
                    execution.execution_id, step_index, attempt,
                )
                await self._publish_fault_event(
                    execution, step_index, agent_name,
                    "agent_retry_success", f"同Agent重试第{attempt}次成功",
                )
                return result

            logger.warning(
                "同Agent重试失败: execution=%s step=%d attempt=%d error=%s",
                execution.execution_id, step_index, attempt, result.error[:200],
            )

        logger.info(
            "策略1失败-同Agent重试耗尽: execution=%s agent=%s",
            execution.execution_id, agent_name,
        )
        return None

    async def _fallback_agent(
        self,
        execution: TaskExecution,
        step_index: int,
    ) -> StepCheckpoint | None:
        """策略2: 替换为Fallback Agent，继承原Agent的专业Prompt

        从AGENT_FALLBACK_TABLE中查找可替换的Agent，
        将原Agent的专业Prompt注入给替换Agent，使其获得专业能力，
        同时告知替换Agent正在替代执行，工具集可能不同。

        Args:
            execution: 任务执行记录
            step_index: 步骤索引

        Returns:
            成功时返回检查点，失败时返回None
        """
        step = self._get_step(execution, step_index)
        if step is None:
            return None

        original_agent = step.get("agent_name", "")
        fallback_agents = AGENT_FALLBACK_TABLE.get(original_agent, [])

        if not fallback_agents:
            logger.info(
                "策略2跳过-无替换Agent: execution=%s original=%s",
                execution.execution_id, original_agent,
            )
            return None

        # 获取原Agent的专业Prompt
        original_prompt = self._get_original_agent_prompt(original_agent)

        # 依次尝试替换Agent
        for fallback_name in fallback_agents:
            # 检查替换Agent的熔断器
            if self._is_circuit_open(fallback_name):
                logger.info(
                    "替换Agent熔断器已打开，跳过: fallback=%s", fallback_name,
                )
                continue

            logger.info(
                "策略2-Agent替换: execution=%s step=%d %s -> %s",
                execution.execution_id, step_index, original_agent, fallback_name,
            )

            # 发布替换事件
            await self._publish_fault_event(
                execution, step_index, original_agent,
                "agent_fallback", f"替换为{fallback_name}",
            )

            # 构建降级任务描述：继承原Agent的专业Prompt
            original_task = self._build_step_task(execution, step)
            if original_prompt:
                degraded_task = (
                    f"你正在临时替代 {original_agent} 执行任务。\n\n"
                    f"{original_prompt}\n\n"
                    f"注意：你使用的是 {fallback_name} 的工具集，"
                    f"如果工具不足以完成任务，请明确说明哪些部分无法处理。\n\n"
                    f"原始任务：{original_task}"
                )
            else:
                degraded_task = FALLBACK_PROMPT_TEMPLATE.format(
                    original_agent=original_agent,
                    fallback_agent=fallback_name,
                    task=original_task,
                )

            # 使用替换Agent执行
            result = await self._execute_step_with_agent(
                execution, step, fallback_name, step_index,
                task_override=degraded_task,
            )

            if result.status == StepStatus.COMPLETED:
                result.fallback_used = f"fallback:{original_agent}->{fallback_name}"
                result.agent_name = fallback_name
                logger.info(
                    "Agent替换成功: execution=%s step=%d fallback=%s",
                    execution.execution_id, step_index, fallback_name,
                )
                await self._publish_fault_event(
                    execution, step_index, fallback_name,
                    "agent_fallback_success", f"替换Agent {fallback_name} 执行成功",
                )
                return result

            logger.warning(
                "Agent替换失败: execution=%s fallback=%s error=%s",
                execution.execution_id, fallback_name, result.error[:200],
            )

        logger.info(
            "策略2失败-所有替换Agent均不可用: execution=%s original=%s",
            execution.execution_id, original_agent,
        )
        return None

    async def _degrade_execution(
        self,
        execution: TaskExecution,
        step_index: int,
    ) -> StepCheckpoint | None:
        """策略3: 降级执行，简化任务描述

        基于已完成步骤的输出数据，构建简化版任务描述，
        让Agent仅基于可用数据完成任务，并标注缺失部分。

        仅在有已完成步骤的输出数据时才尝试此策略。

        Args:
            execution: 任务执行记录
            step_index: 步骤索引

        Returns:
            成功时返回检查点（状态为DEGRADED），失败时返回None
        """
        step = self._get_step(execution, step_index)
        if step is None:
            return None

        # 收集已完成步骤的输出数据
        available_data: dict[str, Any] = {}
        missing_data: list[str] = []

        for cp in execution.checkpoints:
            if cp.status == StepStatus.COMPLETED and cp.output_data:
                available_data[cp.step_name] = cp.output_data
            elif cp.status == StepStatus.FAILED:
                missing_data.append(f"{cp.step_name}({cp.agent_name})")

        # 如果没有可用数据，降级执行意义不大
        if not available_data:
            logger.info(
                "策略3跳过-无可用数据: execution=%s step=%d",
                execution.execution_id, step_index,
            )
            return None

        agent_name = step.get("agent_name", "")

        # 检查Agent熔断器
        if self._is_circuit_open(agent_name):
            # 尝试使用OfficeAssistant
            if not self._is_circuit_open("OfficeAssistant"):
                agent_name = "OfficeAssistant"
            else:
                logger.info(
                    "策略3跳过-Agent熔断器已打开: execution=%s agent=%s",
                    execution.execution_id, agent_name,
                )
                return None

        logger.info(
            "策略3-降级执行: execution=%s step=%d agent=%s",
            execution.execution_id, step_index, agent_name,
        )

        # 发布降级事件
        await self._publish_fault_event(
            execution, step_index, agent_name,
            "execution_degraded", "降级执行，基于部分数据",
        )

        # 构建降级任务描述
        original_task = self._build_step_task(execution, step)
        degraded_task = DEGRADE_PROMPT_TEMPLATE.format(
            available_data=json.dumps(available_data, ensure_ascii=False, default=str)[:2000],
            missing_data=", ".join(missing_data) if missing_data else "无",
            task=original_task,
        )

        # 执行降级任务
        result = await self._execute_step_with_agent(
            execution, step, agent_name, step_index,
            task_override=degraded_task,
        )

        if result.status == StepStatus.COMPLETED:
            result.status = StepStatus.DEGRADED
            result.fallback_used = "degraded_execution"
            logger.info(
                "降级执行成功: execution=%s step=%d",
                execution.execution_id, step_index,
            )
            await self._publish_fault_event(
                execution, step_index, agent_name,
                "execution_degraded_success", "降级执行成功",
            )
            return result

        logger.warning(
            "降级执行失败: execution=%s step=%d error=%s",
            execution.execution_id, step_index, result.error[:200],
        )
        return None

    async def _mark_failed_continue(
        self,
        execution: TaskExecution,
        step_index: int,
        error: Exception,
        failure_reason: str = "",
    ) -> StepCheckpoint:
        """策略4: 标记失败，根据 failure_policy 决定后续行为

        所有恢复策略都失败后，根据故障策略决定后续行为：
        - STRICT: 暂停任务，等待人工决策
        - RELAXED: 标记失败继续下一步
        - MANUAL: 暂停任务，推送通知让用户选择

        Args:
            execution: 任务执行记录
            step_index: 步骤索引
            error: 原始错误

        Returns:
            标记失败的步骤检查点
        """
        step = self._get_step(execution, step_index)
        step_name = step.get("name", "未知步骤") if step else "未知步骤"
        agent_name = step.get("agent_name", "") if step else ""

        checkpoint = StepCheckpoint(
            step_index=step_index,
            step_type=StepType(step.get("type", "agent_call")) if step else StepType.AGENT_CALL,
            step_name=step_name,
            agent_name=agent_name,
            status=StepStatus.FAILED,
            input_data=step or {},
            error=str(error),
            fallback_used="all_strategies_exhausted",
            failure_reason=failure_reason or "agent_fault",
        )

        policy = execution.failure_policy

        if policy == FailurePolicy.STRICT:
            # 严格模式：暂停任务，等待人工决策
            logger.warning(
                "严格模式-任务暂停: execution=%s step=%d error=%s",
                execution.execution_id, step_index, str(error)[:200],
            )
            execution.status = TaskStatus.PAUSED
            execution.error = f"步骤 {step_name} 执行失败（所有恢复策略已耗尽）: {str(error)[:200]}"
            await self._store.update_execution(execution)

            await self._publish_fault_event(
                execution, step_index, agent_name,
                "task_paused_strict", "严格模式：任务暂停，等待人工决策",
            )

        elif policy == FailurePolicy.MANUAL:
            # 手动模式：暂停任务，推送通知让用户选择
            logger.warning(
                "手动模式-任务暂停等待用户: execution=%s step=%d error=%s",
                execution.execution_id, step_index, str(error)[:200],
            )
            execution.status = TaskStatus.PAUSED
            execution.error = f"步骤 {step_name} 执行失败，需要用户决策: {str(error)[:200]}"
            await self._store.update_execution(execution)

            await self._publish_fault_event(
                execution, step_index, agent_name,
                "task_paused_manual", "手动模式：任务暂停，等待用户决策",
            )

        else:
            # 宽松模式：标记失败继续下一步
            logger.warning(
                "宽松模式-标记失败继续: execution=%s step=%d error=%s",
                execution.execution_id, step_index, str(error)[:200],
            )

            await self._publish_fault_event(
                execution, step_index, agent_name,
                "step_failed_continue", "宽松模式：标记失败继续执行",
            )

        return checkpoint

    async def retry_step(
        self,
        execution: TaskExecution,
        step_index: int,
        agent_name_override: str | None = None,
    ) -> StepCheckpoint:
        """重试指定步骤（外部调用，如人工确认后重试）

        支持指定不同的Agent来重试步骤。

        Args:
            execution: 任务执行记录
            step_index: 步骤索引
            agent_name_override: 指定重试使用的Agent名称，为空则使用原Agent

        Returns:
            步骤检查点
        """
        step = self._get_step(execution, step_index)
        if step is None:
            return StepCheckpoint(
                step_index=step_index,
                step_type=StepType.AGENT_CALL,
                step_name="未知步骤",
                status=StepStatus.FAILED,
                error="步骤定义不存在",
            )

        agent_name = agent_name_override or step.get("agent_name", "")

        # 发布重试事件
        await self._publish_fault_event(
            execution, step_index, agent_name,
            "manual_retry", f"手动重试步骤，使用Agent: {agent_name}",
        )

        result = await self._execute_step_with_agent(
            execution, step, agent_name, step_index,
        )

        if result.status == StepStatus.COMPLETED:
            result.fallback_used = "manual_retry"
            if agent_name_override:
                result.fallback_used = f"manual_retry:{step.get('agent_name', '')}->{agent_name_override}"
                result.agent_name = agent_name_override

        return result

    def _get_step(self, execution: TaskExecution, step_index: int) -> dict[str, Any] | None:
        """获取步骤定义

        步骤索引从1开始（0是意图分类），对应execution.steps列表的索引step_index-1。

        Args:
            execution: 任务执行记录
            step_index: 步骤检查点索引

        Returns:
            步骤定义字典，不存在时返回None
        """
        steps_list_index = step_index - 1
        if 0 <= steps_list_index < len(execution.steps):
            return execution.steps[steps_list_index]
        return None

    def _is_circuit_open(self, agent_name: str) -> bool:
        """检查Agent的熔断器是否已打开

        Args:
            agent_name: Agent名称

        Returns:
            True表示熔断器已打开（Agent不可用）
        """
        try:
            from agent.core.infrastructure.circuit_breaker import get_circuit_breaker
            cb = get_circuit_breaker(f"agent_{agent_name}")
            return cb.state.value == "open"
        except Exception:
            return False

    def _classify_fault(self, error: Exception) -> str:
        """根据错误信息判断故障层级

        区分LLM层故障和MCP工具层故障：
        - llm_failure: LLM调用失败（如API限流、模型返回错误），Agent替换可能有效
        - mcp_failure: MCP工具调用失败（如服务宕机、连接超时），Agent替换无效

        Args:
            error: 步骤失败的异常

        Returns:
            故障层级: "llm_failure" 或 "mcp_failure"
        """
        error_msg = str(error).lower()
        if any(kw in error_msg for kw in MCP_FAULT_KEYWORDS):
            return "mcp_failure"
        return "llm_failure"

    def _should_attempt_fallback(self, execution: TaskExecution) -> bool:
        """判断当前意图类型的任务是否适合Agent替换

        查询类任务替换成功率高（通用助手也能完成简单查询），
        操作类任务替换成功率低（需要专业Prompt中的操作规范和安全规则）。

        Args:
            execution: 任务执行记录

        Returns:
            True表示适合尝试Agent替换
        """
        intent = execution.intent_result.get("intent", "")
        return FALLBACK_FEASIBILITY.get(intent, False)

    def _get_original_agent_prompt(self, agent_name: str) -> str | None:
        """获取原Agent的专业Prompt

        优先从Prompt Registry获取外置版本管理的Prompt，
        降级到代码内嵌的AGENT_PROMPTS默认值。

        Args:
            agent_name: Agent名称

        Returns:
            Agent的专业Prompt，不存在则返回None
        """
        try:
            from agent.core.prompt.prompt_registry import get_prompt_registry
            registry = get_prompt_registry()
            prompt = registry.get_prompt(agent_name)
            if prompt:
                return prompt
        except Exception:
            pass

        try:
            from agent.agents.domain import AGENT_PROMPTS
            return AGENT_PROMPTS.get(agent_name)
        except Exception:
            return None

    def _build_step_task(self, execution: TaskExecution, step: dict[str, Any]) -> str:
        """构建步骤任务描述

        将用户原始消息与步骤定义结合，生成完整的任务描述。

        Args:
            execution: 任务执行记录
            step: 步骤定义

        Returns:
            任务描述字符串
        """
        step_name = step.get("name", "")
        original_message = execution.original_message

        # 如果步骤有自定义的任务模板，使用模板
        input_template = step.get("input_template", "")
        if input_template:
            try:
                return input_template.format(original_message=original_message)
            except (KeyError, IndexError):
                pass

        # 默认任务描述
        if step_name:
            return f"{step_name}。用户原始请求：{original_message}"
        return original_message

    async def _execute_step_with_agent(
        self,
        execution: TaskExecution,
        step: dict[str, Any],
        agent_name: str,
        step_index: int,
        task_override: str | None = None,
    ) -> StepCheckpoint:
        """使用指定Agent执行步骤

        复用现有的团队创建和执行控制逻辑，
        在步骤级别进行执行和错误处理。

        Args:
            execution: 任务执行记录
            step: 步骤定义
            agent_name: 执行Agent名称
            step_index: 步骤索引
            task_override: 覆盖的任务描述（降级/替换时使用）

        Returns:
            步骤检查点
        """
        step_type = StepType(step.get("type", "agent_call"))
        step_name = step.get("name", "未知步骤")

        checkpoint = StepCheckpoint(
            step_index=step_index,
            step_type=step_type,
            step_name=step_name,
            agent_name=agent_name,
            status=StepStatus.RUNNING,
            input_data=step,
        )

        try:
            # 构建意图结果用于创建团队
            from agent.agents.supervisor import IntentResult, CollaborationMode
            intent_data = execution.intent_result
            intent = IntentResult(
                intent=intent_data.get("intent", ""),
                confidence=intent_data.get("confidence", 0),
                target_agent=agent_name,
                collaboration_mode=CollaborationMode.DIRECT,
                review_required=False,
            )

            # 创建团队（使用DIRECT模式，只创建单个Agent）
            from agent.teams.team_factory import create_team
            team = await create_team(intent)

            # 构建任务描述
            task = task_override or self._build_step_task(execution, step)

            # 使用步骤级执行控制
            from agent.teams.execution_controller import get_execution_controller
            controller = get_execution_controller()
            result, exec_meta = await controller.execute_step_with_control(
                agent=team,
                task=task,
                session_id=execution.session_id,
                user_id=execution.user_id,
                step_index=step_index,
            )

            if exec_meta.status == "timeout":
                checkpoint.status = StepStatus.FAILED
                checkpoint.error = f"步骤执行超时（超过 {controller._config.max_runtime}s）"
                return checkpoint

            if exec_meta.status == "error" and result is None:
                checkpoint.status = StepStatus.FAILED
                checkpoint.error = exec_meta.message
                return checkpoint

            # 提取输出
            output = result.messages[-1].content if result and result.messages else "处理完成"
            checkpoint.status = StepStatus.COMPLETED
            checkpoint.output_data = {
                "status": "success",
                "message": output,
                "agent_name": agent_name,
                "retries": exec_meta.retries,
            }

        except Exception as e:
            checkpoint.status = StepStatus.FAILED
            checkpoint.error = str(e)
            logger.error(
                "步骤执行异常: execution=%s step=%d agent=%s error=%s",
                execution.execution_id, step_index, agent_name, str(e)[:200],
            )

        return checkpoint

    async def _publish_fault_event(
        self,
        execution: TaskExecution,
        step_index: int,
        agent_name: str,
        event_subtype: str,
        message: str,
    ) -> None:
        """发布故障隔离事件

        Args:
            execution: 任务执行记录
            step_index: 步骤索引
            agent_name: Agent名称
            event_subtype: 事件子类型
            message: 事件消息
        """
        try:
            await publish_event(
                EventType.STEP_FAILED,
                execution.session_id,
                {
                    "execution_id": execution.execution_id,
                    "step_index": step_index,
                    "agent_name": agent_name,
                    "fault_event": event_subtype,
                    "message": message,
                },
            )
        except Exception:
            pass


# ==================== 全局实例 ====================

_fault_isolation_policy: FaultIsolationPolicy | None = None


def get_fault_isolation_policy() -> FaultIsolationPolicy:
    """获取全局故障隔离策略执行器"""
    global _fault_isolation_policy
    if _fault_isolation_policy is None:
        _fault_isolation_policy = FaultIsolationPolicy()
    return _fault_isolation_policy
