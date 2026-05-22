"""工作流可视化编辑引擎

让非技术用户也能定制 Agent 执行流程，与 Coze 拖拽式工作流引擎对齐。

能力：
  - 节点定义：定义工作流中的各类节点（Agent调用、条件判断、并行执行等）
  - 连接管理：节点之间的数据流和控制流
  - 工作流执行：按拓扑排序执行工作流
  - 可视化数据：生成前端可渲染的工作流图数据
  - 版本管理：工作流的保存、加载、版本控制
"""

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 工作流执行最大步数限制，防止死循环
MAX_EXECUTION_STEPS = 1000

_human_input_waiters: dict[str, asyncio.Event] = {}
_human_input_results: dict[str, dict[str, Any]] = {}


class NodeType(str, Enum):
    """节点类型"""

    START = "start"
    END = "end"
    AGENT = "agent"
    CONDITION = "condition"
    PARALLEL = "parallel"
    TOOL = "tool"
    TRANSFORM = "transform"
    HUMAN_INPUT = "human_input"
    DELAY = "delay"


class NodePosition(BaseModel):
    """节点位置（前端可视化用）"""

    x: float = 0
    y: float = 0


class WorkflowNode(BaseModel):
    """工作流节点"""

    node_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: NodeType
    name: str = Field(default="", description="节点名称")
    description: str = Field(default="", description="节点描述")

    config: dict[str, Any] = Field(default_factory=dict, description="节点配置")
    position: NodePosition = Field(default_factory=NodePosition)

    agent_name: str = Field(default="", description="Agent 节点绑定的 Agent 名称")
    tool_name: str = Field(default="", description="Tool 节点绑定的工具名称")
    condition_expr: str = Field(default="", description="条件表达式")
    transform_expr: str = Field(default="", description="数据转换表达式")
    delay_seconds: int = Field(default=0, description="延迟秒数")


class WorkflowEdge(BaseModel):
    """工作流连接边"""

    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_node_id: str
    target_node_id: str
    source_port: str = Field(default="output", description="源端口")
    target_port: str = Field(default="input", description="目标端口")
    condition: str = Field(default="", description="条件（条件分支用）")
    label: str = Field(default="", description="边标签")


class WorkflowStatus(str, Enum):
    """工作流状态"""

    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"


class Workflow(BaseModel):
    """工作流定义"""

    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=100, description="工作流名称")
    description: str = Field(default="", max_length=500, description="工作流描述")
    version: int = Field(default=1)

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)

    status: WorkflowStatus = WorkflowStatus.DRAFT
    created_by: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    tags: list[str] = Field(default_factory=list)


class WorkflowExecution(BaseModel):
    """工作流执行记录"""

    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str
    status: str = "pending"
    current_node_id: str = ""
    results: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None


# ==================== 存储 ====================

_WORKFLOW_KEY_PREFIX = "workflow:"
_EXECUTION_KEY_PREFIX = "workflow_exec:"


async def _get_workflow_redis() -> Any:
    """获取工作流存储用的 Redis 客户端

    直接使用全局统一连接管理器，不本地缓存，
    由 redis_manager 内部管理连接生命周期。
    """
    try:
        from agent.core.redis_manager import get_redis_client
        return await get_redis_client()
    except Exception as e:
        logger.warning("工作流引擎 Redis 连接失败: %s", e)
        return None


async def _store_workflow(workflow: Workflow) -> None:
    """持久化工作流到 Redis"""
    redis = await _get_workflow_redis()
    if redis is None:
        return
    key = f"{_WORKFLOW_KEY_PREFIX}{workflow.workflow_id}"
    await redis.set(key, workflow.model_dump_json(), ex=86400 * 30)


async def _load_workflow(workflow_id: str) -> Workflow | None:
    """从 Redis 加载工作流"""
    redis = await _get_workflow_redis()
    if redis is None:
        return None
    key = f"{_WORKFLOW_KEY_PREFIX}{workflow_id}"
    data = await redis.get(key)
    if data is None:
        return None
    return Workflow.model_validate_json(data)


async def _remove_workflow(workflow_id: str) -> bool:
    """从 Redis 删除工作流"""
    redis = await _get_workflow_redis()
    if redis is None:
        return False
    key = f"{_WORKFLOW_KEY_PREFIX}{workflow_id}"
    return await redis.delete(key) > 0


async def _store_execution(execution: WorkflowExecution) -> None:
    """持久化执行记录到 Redis"""
    redis = await _get_workflow_redis()
    if redis is None:
        return
    key = f"{_EXECUTION_KEY_PREFIX}{execution.execution_id}"
    await redis.set(key, execution.model_dump_json(), ex=86400 * 7)


async def _list_all_workflow_ids() -> list[str]:
    """列出 Redis 中所有工作流 ID"""
    redis = await _get_workflow_redis()
    if redis is None:
        return []
    keys = []
    cursor = 0
    while True:
        cursor, batch = await redis.scan(cursor, match=f"{_WORKFLOW_KEY_PREFIX}*", count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    return [k.replace(_WORKFLOW_KEY_PREFIX, "") for k in keys]


# ==================== CRUD ====================


async def create_workflow(workflow: Workflow, created_by: str = "") -> Workflow:
    """创建工作流"""
    workflow.created_by = created_by
    workflow.status = WorkflowStatus.DRAFT
    workflow.created_at = time.time()
    workflow.updated_at = workflow.created_at

    _validate_workflow(workflow)

    await _store_workflow(workflow)
    logger.info("工作流已创建: id=%s name=%s", workflow.workflow_id, workflow.name)
    return workflow


async def get_workflow(workflow_id: str) -> Workflow | None:
    """获取工作流"""
    return await _load_workflow(workflow_id)


async def list_workflows(
    created_by: str = "",
    status: WorkflowStatus | None = None,
) -> list[Workflow]:
    """列出工作流"""
    all_ids = await _list_all_workflow_ids()
    workflows: list[Workflow] = []
    for wid in all_ids:
        wf = await _load_workflow(wid)
        if wf:
            workflows.append(wf)

    if created_by:
        workflows = [w for w in workflows if w.created_by == created_by]
    if status:
        workflows = [w for w in workflows if w.status == status]
    workflows.sort(key=lambda w: w.updated_at, reverse=True)
    return workflows


async def update_workflow(workflow_id: str, updates: dict[str, Any]) -> Workflow | None:
    """更新工作流"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    if workflow.status == WorkflowStatus.DISABLED:
        raise ValueError("已禁用的工作流不可修改")

    for key, value in updates.items():
        if hasattr(workflow, key) and key not in ("workflow_id", "created_by", "created_at"):
            setattr(workflow, key, value)

    workflow.updated_at = time.time()

    if "nodes" in updates or "edges" in updates:
        _validate_workflow(workflow)

    await _store_workflow(workflow)
    return workflow


async def delete_workflow(workflow_id: str) -> bool:
    """删除工作流"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return False

    if workflow.status == WorkflowStatus.PUBLISHED:
        raise ValueError("已发布的工作流不可删除，请先禁用")

    return await _remove_workflow(workflow_id)


# ==================== 节点操作 ====================


async def add_node(workflow_id: str, node: WorkflowNode) -> Workflow | None:
    """添加节点"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    workflow.nodes.append(node)
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def update_node(workflow_id: str, node_id: str, updates: dict[str, Any]) -> Workflow | None:
    """更新节点"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    for node in workflow.nodes:
        if node.node_id == node_id:
            for key, value in updates.items():
                if hasattr(node, key) and key != "node_id":
                    setattr(node, key, value)
            workflow.updated_at = time.time()
            await _store_workflow(workflow)
            return workflow

    return None


async def remove_node(workflow_id: str, node_id: str) -> Workflow | None:
    """移除节点"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    workflow.nodes = [n for n in workflow.nodes if n.node_id != node_id]
    workflow.edges = [
        e for e in workflow.edges
        if e.source_node_id != node_id and e.target_node_id != node_id
    ]
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def add_edge(workflow_id: str, edge: WorkflowEdge) -> Workflow | None:
    """添加连接"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    node_ids = {n.node_id for n in workflow.nodes}
    if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
        raise ValueError("连接的源节点或目标节点不存在")

    workflow.edges.append(edge)
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def remove_edge(workflow_id: str, edge_id: str) -> Workflow | None:
    """移除连接"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    workflow.edges = [e for e in workflow.edges if e.edge_id != edge_id]
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


# ==================== 工作流执行 ====================


async def execute_workflow(workflow_id: str, input_data: dict[str, Any] | None = None) -> WorkflowExecution:
    """执行工作流

    按拓扑排序执行节点，支持条件分支和并行执行。
    设置最大执行步数限制，防止条件分支环路导致死循环。

    Args:
        workflow_id: 工作流ID
        input_data: 输入数据

    Returns:
        WorkflowExecution
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        raise ValueError(f"工作流不存在: {workflow_id}")

    if workflow.status != WorkflowStatus.PUBLISHED:
        raise ValueError("只有已发布的工作流可以执行")

    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="running",
    )
    await _store_execution(execution)

    try:
        start_nodes = [n for n in workflow.nodes if n.type == NodeType.START]
        if not start_nodes:
            raise ValueError("工作流缺少起始节点")

        context: dict[str, Any] = {"input": input_data or {}, "results": {}}
        current_node = start_nodes[0]
        visited: set[str] = set()
        step_count = 0

        while current_node:
            step_count += 1
            if step_count > MAX_EXECUTION_STEPS:
                raise ValueError(f"工作流执行步数超过限制({MAX_EXECUTION_STEPS})，可能存在环路")

            if current_node.node_id in visited:
                break
            visited.add(current_node.node_id)

            execution.current_node_id = current_node.node_id
            node_result = await _execute_node(current_node, context, execution)
            context["results"][current_node.node_id] = node_result

            next_edges = [e for e in workflow.edges if e.source_node_id == current_node.node_id]

            if current_node.type == NodeType.END:
                break

            if current_node.type == NodeType.CONDITION and next_edges:
                condition_result = _evaluate_condition(current_node.condition_expr, context)
                matched_edge = None
                for edge in next_edges:
                    if edge.condition and edge.condition.lower() == str(condition_result).lower():
                        matched_edge = edge
                        break
                if not matched_edge and next_edges:
                    matched_edge = next_edges[0]

                next_node = _find_node(workflow, matched_edge.target_node_id) if matched_edge else None
            else:
                next_node = _find_node(workflow, next_edges[0].target_node_id) if next_edges else None

            current_node = next_node

        execution.status = "completed"
        execution.results = context["results"]

    except Exception as e:
        execution.status = "failed"
        execution.error = str(e)
        logger.error("工作流执行失败: %s", e)

    execution.completed_at = time.time()
    await _store_execution(execution)
    return execution


async def _execute_node(node: WorkflowNode, context: dict[str, Any], execution: WorkflowExecution | None = None) -> dict[str, Any]:
    """执行单个节点

    根据节点类型调用对应的执行器，将结果写入上下文。
    """
    if node.type == NodeType.START:
        return {"status": "started", "input": context.get("input", {})}

    elif node.type == NodeType.END:
        return {"status": "completed", "output": context.get("results", {})}

    elif node.type == NodeType.AGENT:
        return await _execute_agent_node(node, context)

    elif node.type == NodeType.TOOL:
        return await _execute_tool_node(node, context)

    elif node.type == NodeType.TRANSFORM:
        return _execute_transform_node(node, context)

    elif node.type == NodeType.DELAY:
        return await _execute_delay_node(node)

    elif node.type == NodeType.HUMAN_INPUT:
        return await _execute_human_input_node(node, context, execution)

    elif node.type == NodeType.PARALLEL:
        return await _execute_parallel_node(node, context)

    return {"status": "unknown", "type": node.type.value}


async def _execute_agent_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行 Agent 节点：调用指定 Agent 处理输入"""
    try:
        from agent.core.agent_router import route_to_agent

        agent_name = node.agent_name or node.config.get("agent_name", "")
        input_text = node.config.get("input_template", "")
        if input_text:
            input_text = _render_template(input_text, context)

        result = await route_to_agent(
            agent_name=agent_name,
            message=input_text,
            context=context,
        )

        return {
            "status": "agent_completed",
            "agent": agent_name,
            "result": result if isinstance(result, dict) else {"response": str(result)},
        }
    except Exception as e:
        logger.error("Agent 节点执行失败: agent=%s, error=%s", node.agent_name, e)
        return {"status": "agent_failed", "agent": node.agent_name, "error": str(e)}


async def _execute_tool_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行工具节点：调用指定工具"""
    try:
        from agent.core.tool_registry import execute_tool

        tool_name = node.tool_name or node.config.get("tool_name", "")
        tool_params = node.config.get("params", {})
        tool_params = _render_template_dict(tool_params, context)

        result = await execute_tool(tool_name=tool_name, params=tool_params)

        return {
            "status": "tool_completed",
            "tool": tool_name,
            "result": result if isinstance(result, dict) else {"output": str(result)},
        }
    except Exception as e:
        logger.error("工具节点执行失败: tool=%s, error=%s", node.tool_name, e)
        return {"status": "tool_failed", "tool": node.tool_name, "error": str(e)}


def _execute_transform_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行转换节点：对上下文数据进行映射和转换"""
    try:
        mapping = node.config.get("mapping", {})
        output: dict[str, Any] = {}

        for target_key, source_expr in mapping.items():
            output[target_key] = _resolve_value(source_expr, context)

        return {"status": "transformed", "output": output}
    except Exception as e:
        logger.error("转换节点执行失败: error=%s", e)
        return {"status": "transform_failed", "error": str(e)}


async def _execute_delay_node(node: WorkflowNode) -> dict[str, Any]:
    """执行延迟节点：等待指定秒数"""
    import asyncio

    seconds = node.delay_seconds or node.config.get("seconds", 0)
    if seconds > 0:
        await asyncio.sleep(seconds)
    return {"status": "delayed", "seconds": seconds}


async def _execute_human_input_node(
    node: WorkflowNode,
    context: dict[str, Any],
    execution: WorkflowExecution,
) -> dict[str, Any]:
    """执行人工输入节点：暂停工作流等待人工输入

    通过事件总线发布 HUMAN_CONFIRM_REQUIRED 事件通知前端，
    同时创建 asyncio.Event 阻塞当前协程，直到外部调用
    resume_workflow_with_input() 提交输入后唤醒继续执行。

    Args:
        node: 人工输入节点
        context: 工作流上下文
        execution: 当前执行记录

    Returns:
        包含用户输入结果的字典
    """
    prompt = node.config.get("prompt", "请输入:")
    timeout_seconds = node.config.get("timeout_seconds", 3600)
    user_id = context.get("input", {}).get("user_id", "")
    session_id = context.get("input", {}).get("session_id", "")

    confirm_key = f"{execution.execution_id}:{node.node_id}"

    waiter = asyncio.Event()
    _human_input_waiters[confirm_key] = waiter

    try:
        from agent.core.event_bus import publish_event, EventType

        await publish_event(
            event_type=EventType.HUMAN_CONFIRM_REQUIRED,
            session_id=session_id,
            data={
                "execution_id": execution.execution_id,
                "node_id": node.node_id,
                "node_name": node.name,
                "prompt": prompt,
                "confirm_key": confirm_key,
                "user_id": user_id,
            },
        )
    except Exception as e:
        logger.warning("人工输入事件发布失败: %s", e)

    execution.status = "waiting_for_input"
    await _store_execution(execution)

    try:
        await asyncio.wait_for(waiter.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        _human_input_waiters.pop(confirm_key, None)
        _human_input_results.pop(confirm_key, None)
        return {"status": "input_timeout", "prompt": prompt}

    user_input = _human_input_results.pop(confirm_key, {})
    _human_input_waiters.pop(confirm_key, None)

    execution.status = "running"
    await _store_execution(execution)

    return {"status": "input_received", "prompt": prompt, "user_input": user_input}


async def _execute_parallel_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行并行节点：同时执行多个分支"""
    import asyncio

    branches = node.config.get("branches", [])
    if not branches:
        return {"status": "parallel_completed", "results": {}}

    tasks = []
    branch_names = []
    for branch in branches:
        branch_node = WorkflowNode(
            node_id=branch.get("node_id", str(uuid.uuid4())),
            name=branch.get("name", ""),
            type=NodeType(branch.get("type", "agent")),
            config=branch.get("config", {}),
            agent_name=branch.get("agent_name"),
            tool_name=branch.get("tool_name"),
        )
        tasks.append(_execute_node(branch_node, context))
        branch_names.append(branch.get("name", branch_node.node_id))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    branch_results: dict[str, Any] = {}
    for name, result in zip(branch_names, results):
        if isinstance(result, Exception):
            branch_results[name] = {"status": "failed", "error": str(result)}
        else:
            branch_results[name] = result

    return {"status": "parallel_completed", "results": branch_results}


def _render_template(template: str, context: dict[str, Any]) -> str:
    """渲染简单模板，替换 {{ variable }} 占位符

    Args:
        template: 模板字符串
        context: 上下文变量字典

    Returns:
        渲染后的字符串
    """
    import re

    def replacer(match: re.Match) -> str:
        var_path = match.group(1).strip()
        val = _resolve_value(var_path, context)
        return str(val) if val is not None else match.group(0)

    return re.sub(r"\{\{\s*(.+?)\s*\}\}", replacer, template)


def _render_template_dict(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """渲染字典中的模板值"""
    rendered = {}
    for key, value in params.items():
        if isinstance(value, str):
            rendered[key] = _render_template(value, context)
        elif isinstance(value, dict):
            rendered[key] = _render_template_dict(value, context)
        else:
            rendered[key] = value
    return rendered


_SAFE_OPERATORS: dict[str, Any] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "and": lambda a, b: a and b,
    "or": lambda a, b: a or b,
}


def _evaluate_condition(expr: str, context: dict[str, Any]) -> Any:
    """安全评估条件表达式

    使用白名单操作符进行简单的条件判断，避免 eval() 带来的代码注入风险。
    支持格式：
      - 变量比较: "result.status == 'completed'"
      - 数值比较: "result.count > 10"
      - 布尔变量: "result.success"
      - 逻辑组合: "result.count > 10 and result.status == 'completed'"

    Args:
        expr: 条件表达式字符串
        context: 上下文变量字典

    Returns:
        条件评估结果，解析失败时返回 False
    """
    if not expr:
        return True

    expr = expr.strip()

    try:
        if " or " in expr:
            parts = expr.split(" or ", 1)
            return _evaluate_condition(parts[0].strip(), context) or _evaluate_condition(parts[1].strip(), context)

        if " and " in expr:
            parts = expr.split(" and ", 1)
            return _evaluate_condition(parts[0].strip(), context) and _evaluate_condition(parts[1].strip(), context)

        for op_str, op_func in _SAFE_OPERATORS.items():
            if f" {op_str} " in expr:
                left_str, right_str = expr.split(f" {op_str} ", 1)
                left_val = _resolve_value(left_str.strip(), context)
                right_val = _resolve_value(right_str.strip(), context)
                return op_func(left_val, right_val)

        val = _resolve_value(expr, context)
        return bool(val)

    except Exception:
        return False


def _resolve_value(token: str, context: dict[str, Any]) -> Any:
    """解析表达式中的值

    支持从上下文中读取变量（点号路径）和字面量（字符串、数字、布尔值）。

    Args:
        token: 值标记（变量路径或字面量）
        context: 上下文变量字典

    Returns:
        解析后的值
    """
    token = token.strip()

    if (token.startswith("'") and token.endswith("'")) or (token.startswith('"') and token.endswith('"')):
        return token[1:-1]

    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False

    try:
        if "." in token and not token.replace(".", "", 1).replace("-", "", 1).isdigit():
            parts = token.split(".")
            val: Any = context
            for part in parts:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    return token
                if val is None:
                    return token
            return val
    except Exception:
        pass

    try:
        return int(token)
    except ValueError:
        pass

    try:
        return float(token)
    except ValueError:
        pass

    if token in context:
        return context[token]

    return token


def _find_node(workflow: Workflow, node_id: str) -> WorkflowNode | None:
    """查找节点"""
    for node in workflow.nodes:
        if node.node_id == node_id:
            return node
    return None


# ==================== 校验 ====================


def _validate_workflow(workflow: Workflow) -> None:
    """校验工作流合法性"""
    node_ids = {n.node_id for n in workflow.nodes}

    start_nodes = [n for n in workflow.nodes if n.type == NodeType.START]
    if len(start_nodes) == 0:
        raise ValueError("工作流必须包含一个起始节点")
    if len(start_nodes) > 1:
        raise ValueError("工作流只能包含一个起始节点")

    end_nodes = [n for n in workflow.nodes if n.type == NodeType.END]
    if len(end_nodes) == 0:
        raise ValueError("工作流必须包含至少一个结束节点")

    for edge in workflow.edges:
        if edge.source_node_id not in node_ids:
            raise ValueError(f"边的源节点不存在: {edge.source_node_id}")
        if edge.target_node_id not in node_ids:
            raise ValueError(f"边的目标节点不存在: {edge.target_node_id}")

    node_id_counts: dict[str, int] = {}
    for node in workflow.nodes:
        node_id_counts[node.node_id] = node_id_counts.get(node.node_id, 0) + 1
    duplicates = [nid for nid, count in node_id_counts.items() if count > 1]
    if duplicates:
        raise ValueError(f"节点ID重复: {duplicates}")


# ==================== 可视化数据 ====================


async def get_workflow_visualization(workflow_id: str) -> dict[str, Any] | None:
    """获取工作流可视化数据

    返回前端流程图组件可直接使用的 JSON 格式。

    Args:
        workflow_id: 工作流ID

    Returns:
        可视化数据
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    nodes = []
    for n in workflow.nodes:
        node_data: dict[str, Any] = {
            "id": n.node_id,
            "type": n.type.value,
            "label": n.name or n.type.value,
            "position": {"x": n.position.x, "y": n.position.y},
            "data": {
                "description": n.description,
                "config": n.config,
            },
        }
        if n.type == NodeType.AGENT:
            node_data["data"]["agent_name"] = n.agent_name
        elif n.type == NodeType.TOOL:
            node_data["data"]["tool_name"] = n.tool_name
        elif n.type == NodeType.CONDITION:
            node_data["data"]["condition_expr"] = n.condition_expr
        nodes.append(node_data)

    edges = []
    for e in workflow.edges:
        edge_data: dict[str, Any] = {
            "id": e.edge_id,
            "source": e.source_node_id,
            "target": e.target_node_id,
            "sourcePort": e.source_port,
            "targetPort": e.target_port,
        }
        if e.condition:
            edge_data["label"] = e.condition
        elif e.label:
            edge_data["label"] = e.label
        edges.append(edge_data)

    return {
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "status": workflow.status.value,
        "nodes": nodes,
        "edges": edges,
    }


async def publish_workflow(workflow_id: str) -> Workflow | None:
    """发布工作流"""
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    if workflow.status != WorkflowStatus.DRAFT:
        raise ValueError("只有草稿状态的工作流可以发布")

    _validate_workflow(workflow)

    workflow.status = WorkflowStatus.PUBLISHED
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def resume_workflow_with_input(
    execution_id: str,
    node_id: str,
    user_input: dict[str, Any],
) -> bool:
    """向等待中的人工输入节点提交用户输入，唤醒工作流继续执行

    当工作流执行到 HUMAN_INPUT 节点时会暂停并等待，
    前端通过此接口提交用户输入后，工作流自动恢复执行。

    Args:
        execution_id: 工作流执行ID
        node_id: 人工输入节点ID
        user_input: 用户输入数据

    Returns:
        是否成功唤醒
    """
    confirm_key = f"{execution_id}:{node_id}"

    waiter = _human_input_waiters.get(confirm_key)
    if waiter is None:
        logger.warning("未找到等待中的人工输入: execution=%s node=%s", execution_id, node_id)
        return False

    _human_input_results[confirm_key] = user_input
    waiter.set()
    logger.info("人工输入已提交: execution=%s node=%s", execution_id, node_id)
    return True
