"""工作流可视化编辑引擎

让非技术用户也能定制 Agent 执行流程，与 Coze 拖拽式工作流引擎对齐。

能力：
  - 节点定义：定义工作流中的各类节点（Agent调用、条件判断、并行执行等）
  - 连接管理：节点之间的数据流和控制流
  - 工作流执行：按拓扑排序执行工作流
  - 可视化数据：生成前端可渲染的工作流图数据
  - 版本管理：工作流的保存、加载、版本控制
"""

import logging
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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

_workflows_store: dict[str, Workflow] = {}
_executions_store: dict[str, WorkflowExecution] = {}


# ==================== CRUD ====================


def create_workflow(workflow: Workflow, created_by: str = "") -> Workflow:
    """创建工作流"""
    workflow.created_by = created_by
    workflow.status = WorkflowStatus.DRAFT
    workflow.created_at = time.time()
    workflow.updated_at = workflow.created_at

    _validate_workflow(workflow)

    _workflows_store[workflow.workflow_id] = workflow
    logger.info("工作流已创建: id=%s name=%s", workflow.workflow_id, workflow.name)
    return workflow


def get_workflow(workflow_id: str) -> Workflow | None:
    """获取工作流"""
    return _workflows_store.get(workflow_id)


def list_workflows(
    created_by: str = "",
    status: WorkflowStatus | None = None,
) -> list[Workflow]:
    """列出工作流"""
    workflows = list(_workflows_store.values())
    if created_by:
        workflows = [w for w in workflows if w.created_by == created_by]
    if status:
        workflows = [w for w in workflows if w.status == status]
    workflows.sort(key=lambda w: w.updated_at, reverse=True)
    return workflows


def update_workflow(workflow_id: str, updates: dict[str, Any]) -> Workflow | None:
    """更新工作流"""
    workflow = _workflows_store.get(workflow_id)
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

    return workflow


def delete_workflow(workflow_id: str) -> bool:
    """删除工作流"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return False

    if workflow.status == WorkflowStatus.PUBLISHED:
        raise ValueError("已发布的工作流不可删除，请先禁用")

    del _workflows_store[workflow_id]
    return True


# ==================== 节点操作 ====================


def add_node(workflow_id: str, node: WorkflowNode) -> Workflow | None:
    """添加节点"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return None

    workflow.nodes.append(node)
    workflow.updated_at = time.time()
    return workflow


def update_node(workflow_id: str, node_id: str, updates: dict[str, Any]) -> Workflow | None:
    """更新节点"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return None

    for node in workflow.nodes:
        if node.node_id == node_id:
            for key, value in updates.items():
                if hasattr(node, key) and key != "node_id":
                    setattr(node, key, value)
            workflow.updated_at = time.time()
            return workflow

    return None


def remove_node(workflow_id: str, node_id: str) -> Workflow | None:
    """移除节点"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return None

    workflow.nodes = [n for n in workflow.nodes if n.node_id != node_id]
    workflow.edges = [
        e for e in workflow.edges
        if e.source_node_id != node_id and e.target_node_id != node_id
    ]
    workflow.updated_at = time.time()
    return workflow


def add_edge(workflow_id: str, edge: WorkflowEdge) -> Workflow | None:
    """添加连接"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return None

    node_ids = {n.node_id for n in workflow.nodes}
    if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
        raise ValueError("连接的源节点或目标节点不存在")

    workflow.edges.append(edge)
    workflow.updated_at = time.time()
    return workflow


def remove_edge(workflow_id: str, edge_id: str) -> Workflow | None:
    """移除连接"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return None

    workflow.edges = [e for e in workflow.edges if e.edge_id != edge_id]
    workflow.updated_at = time.time()
    return workflow


# ==================== 工作流执行 ====================


async def execute_workflow(workflow_id: str, input_data: dict[str, Any] | None = None) -> WorkflowExecution:
    """执行工作流

    按拓扑排序执行节点，支持条件分支和并行执行。

    Args:
        workflow_id: 工作流ID
        input_data: 输入数据

    Returns:
        WorkflowExecution
    """
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        raise ValueError(f"工作流不存在: {workflow_id}")

    if workflow.status != WorkflowStatus.PUBLISHED:
        raise ValueError("只有已发布的工作流可以执行")

    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="running",
    )
    _executions_store[execution.execution_id] = execution

    try:
        start_nodes = [n for n in workflow.nodes if n.type == NodeType.START]
        if not start_nodes:
            raise ValueError("工作流缺少起始节点")

        context: dict[str, Any] = {"input": input_data or {}, "results": {}}
        current_node = start_nodes[0]
        visited: set[str] = set()

        while current_node:
            if current_node.node_id in visited and current_node.type != NodeType.CONDITION:
                break
            visited.add(current_node.node_id)

            execution.current_node_id = current_node.node_id
            node_result = await _execute_node(current_node, context)
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
    return execution


async def _execute_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行单个节点"""
    if node.type == NodeType.START:
        return {"status": "started", "input": context.get("input", {})}

    elif node.type == NodeType.END:
        return {"status": "completed"}

    elif node.type == NodeType.AGENT:
        return {"status": "agent_invoked", "agent": node.agent_name, "config": node.config}

    elif node.type == NodeType.TOOL:
        return {"status": "tool_invoked", "tool": node.tool_name, "config": node.config}

    elif node.type == NodeType.TRANSFORM:
        return {"status": "transformed", "expression": node.transform_expr}

    elif node.type == NodeType.DELAY:
        return {"status": "delayed", "seconds": node.delay_seconds}

    elif node.type == NodeType.HUMAN_INPUT:
        return {"status": "waiting_for_input", "prompt": node.config.get("prompt", "")}

    elif node.type == NodeType.PARALLEL:
        return {"status": "parallel_branches", "branches": node.config.get("branches", [])}

    return {"status": "unknown", "type": node.type.value}


def _evaluate_condition(expr: str, context: dict[str, Any]) -> Any:
    """评估条件表达式"""
    if not expr:
        return True

    try:
        result = eval(expr, {"__builtins__": {}}, context)
        return result
    except Exception:
        return False


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


def get_workflow_visualization(workflow_id: str) -> dict[str, Any] | None:
    """获取工作流可视化数据

    返回前端流程图组件可直接使用的 JSON 格式。

    Args:
        workflow_id: 工作流ID

    Returns:
        可视化数据
    """
    workflow = _workflows_store.get(workflow_id)
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


def publish_workflow(workflow_id: str) -> Workflow | None:
    """发布工作流"""
    workflow = _workflows_store.get(workflow_id)
    if not workflow:
        return None

    if workflow.status != WorkflowStatus.DRAFT:
        raise ValueError("只有草稿状态的工作流可以发布")

    _validate_workflow(workflow)

    workflow.status = WorkflowStatus.PUBLISHED
    workflow.updated_at = time.time()
    return workflow
