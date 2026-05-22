"""工作流可视化编辑路由"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.core.workflow_engine import (
    create_workflow,
    list_workflows,
    update_workflow,
    delete_workflow,
    add_node,
    update_node,
    remove_node,
    add_edge,
    remove_edge,
    execute_workflow,
    get_workflow_visualization,
    publish_workflow,
    resume_workflow_with_input,
    Workflow,
    WorkflowNode,
    WorkflowEdge,
    NodePosition,
    NodeType,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["工作流"])


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class AddNodeRequest(BaseModel):
    node_id: str = ""
    type: NodeType
    name: str = ""
    description: str = ""
    config: dict = Field(default_factory=dict)
    position: NodePosition = Field(default_factory=NodePosition)
    agent_name: str = ""
    tool_name: str = ""
    condition_expr: str = ""
    transform_expr: str = ""
    delay_seconds: int = 0


class AddEdgeRequest(BaseModel):
    source_node_id: str
    target_node_id: str
    source_port: str = "output"
    target_port: str = "input"
    condition: str = ""
    label: str = ""


class ExecuteWorkflowRequest(BaseModel):
    input_data: dict = Field(default_factory=dict)


@router.get("", response_model=list[Workflow], summary="列出工作流")
async def api_list_workflows(
    status: WorkflowStatus | None = None,
) -> list[Workflow]:
    """列出工作流"""
    return await list_workflows(status=status)


@router.post("", response_model=Workflow, summary="创建工作流")
async def api_create_workflow(request: CreateWorkflowRequest) -> Workflow:
    """创建工作流"""
    workflow = Workflow(
        name=request.name,
        description=request.description,
        tags=request.tags,
        nodes=request.nodes,
        edges=request.edges,
    )
    return await create_workflow(workflow)


@router.get("/{workflow_id}", summary="获取工作流详情")
async def api_get_workflow(workflow_id: str) -> dict:
    """获取工作流详情（含可视化数据）"""
    viz = await get_workflow_visualization(workflow_id)
    if not viz:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return viz


@router.put("/{workflow_id}", response_model=Workflow, summary="更新工作流")
async def api_update_workflow(workflow_id: str, request: CreateWorkflowRequest) -> Workflow:
    """更新工作流"""
    result = await update_workflow(workflow_id, {"name": request.name, "description": request.description, "tags": request.tags, "nodes": [n.model_dump() for n in request.nodes], "edges": [e.model_dump() for e in request.edges]})
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return result


@router.delete("/{workflow_id}", summary="删除工作流")
async def api_delete_workflow(workflow_id: str) -> dict:
    """删除工作流"""
    success = await delete_workflow(workflow_id)
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return {"status": "ok"}


@router.post("/{workflow_id}/publish", response_model=Workflow, summary="发布工作流")
async def api_publish_workflow(workflow_id: str) -> Workflow:
    """发布工作流"""
    result = await publish_workflow(workflow_id)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return result


@router.post("/{workflow_id}/nodes", response_model=Workflow, summary="添加工作流节点")
async def api_add_node(workflow_id: str, request: AddNodeRequest) -> Workflow:
    """添加节点"""
    node = WorkflowNode(**request.model_dump())
    result = await add_node(workflow_id, node)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return result


@router.put("/{workflow_id}/nodes/{node_id}", response_model=Workflow, summary="更新工作流节点")
async def api_update_node(workflow_id: str, node_id: str, request: AddNodeRequest) -> Workflow:
    """更新节点"""
    result = await update_node(workflow_id, node_id, request.model_dump(exclude_unset=True))
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流或节点不存在")
    return result


@router.delete("/{workflow_id}/nodes/{node_id}", response_model=Workflow, summary="移除工作流节点")
async def api_remove_node(workflow_id: str, node_id: str) -> Workflow:
    """移除节点"""
    result = await remove_node(workflow_id, node_id)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return result


@router.post("/{workflow_id}/edges", response_model=Workflow, summary="添加工作流连接")
async def api_add_edge(workflow_id: str, request: AddEdgeRequest) -> Workflow:
    """添加连接"""
    edge = WorkflowEdge(**request.model_dump())
    result = await add_edge(workflow_id, edge)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return result


@router.delete("/{workflow_id}/edges/{edge_id}", response_model=Workflow, summary="移除工作流连接")
async def api_remove_edge(workflow_id: str, edge_id: str) -> Workflow:
    """移除连接"""
    result = await remove_edge(workflow_id, edge_id)
    if not result:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="工作流不存在")
    return result


@router.post("/{workflow_id}/execute", summary="执行工作流")
async def api_execute_workflow(workflow_id: str, request: ExecuteWorkflowRequest):
    """执行工作流"""
    return await execute_workflow(workflow_id, request.input_data)


@router.get("/node-types", summary="列出节点类型")
async def api_list_node_types() -> dict:
    """列出节点类型"""
    return {
        "node_types": [
            {"id": "start", "name": "起始节点", "description": "工作流入口"},
            {"id": "end", "name": "结束节点", "description": "工作流出口"},
            {"id": "agent", "name": "Agent节点", "description": "调用指定Agent执行任务"},
            {"id": "condition", "name": "条件节点", "description": "根据条件分支执行"},
            {"id": "parallel", "name": "并行节点", "description": "并行执行多个分支"},
            {"id": "tool", "name": "工具节点", "description": "调用指定MCP工具"},
            {"id": "transform", "name": "转换节点", "description": "数据格式转换"},
            {"id": "human_input", "name": "人工输入", "description": "等待人工输入"},
            {"id": "delay", "name": "延迟节点", "description": "延迟指定时间"},
        ]
    }


class ResumeWorkflowInputRequest(BaseModel):
    """工作流人工输入提交请求"""

    execution_id: str = Field(..., description="工作流执行ID")
    node_id: str = Field(..., description="人工输入节点ID")
    user_input: dict = Field(default_factory=dict, description="用户输入数据")


@router.post("/human-input", summary="提交工作流人工输入")
async def api_resume_workflow_with_input(request: ResumeWorkflowInputRequest) -> dict:
    """向等待中的人工输入节点提交用户输入

    当工作流执行到 HUMAN_INPUT 节点时会暂停等待，
    前端通过此接口提交用户输入后，工作流自动恢复执行。
    """
    success = await resume_workflow_with_input(
        execution_id=request.execution_id,
        node_id=request.node_id,
        user_input=request.user_input,
    )
    if not success:
        from api.errors import AppException, ErrorCode
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, message="未找到等待中的人工输入节点，可能已超时或不存在")
    return {"status": "ok", "message": "输入已提交，工作流继续执行"}
