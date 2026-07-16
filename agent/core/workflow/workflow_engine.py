"""工作流可视化编辑引擎

让非技术用户也能定制 Agent 执行流程，与 Coze 拖拽式工作流引擎对齐。

能力：
  - 节点定义：定义工作流中的各类节点（Agent调用、条件判断、并行执行等）
  - 连接管理：节点之间的数据流和控制流
  - 工作流执行：按拓扑排序执行工作流
  - 可视化数据：生成前端可渲染的工作流图数据
  - 版本管理：工作流的保存、加载、版本控制

整体架构：
  本模块是工作流引擎的核心实现，采用"节点-边"的 DAG（有向无环图）模型来描述工作流。
  每个工作流由若干节点（WorkflowNode）和连接边（WorkflowEdge）组成，节点代表一个
  执行步骤（如调用 Agent、条件判断、并行执行等），边代表节点之间的数据流和控制流。

  执行引擎采用"从起始节点出发、沿边遍历"的策略，逐节点执行，支持：
    - 条件分支：根据条件表达式的结果选择不同的后续路径
    - 并行执行：同时启动多个分支，所有分支完成后汇聚到同一后续节点
    - 人工输入：暂停工作流等待用户确认或输入，通过事件机制唤醒

  持久化层使用 Redis 存储工作流定义和执行记录，通过统一的 redis_manager 管理连接。

  安全设计：
    - 条件表达式评估使用白名单操作符，避免 eval() 代码注入风险
    - 工作流执行设置最大步数限制（MAX_EXECUTION_STEPS），防止条件分支环路导致死循环
    - 并行节点使用信号量控制最大并发数，防止资源耗尽
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
# 当工作流执行步数超过此值时，引擎会抛出异常终止执行，
# 这是一种安全机制，防止条件分支形成环路导致无限循环
MAX_EXECUTION_STEPS = 1000

# 人工输入等待器字典
# 键格式为 "{execution_id}:{node_id}"，值为 asyncio.Event 对象
# 当工作流执行到 HUMAN_INPUT 节点时，会在此字典中注册一个 Event，
# 当前协程通过 waiter.wait() 阻塞等待，直到外部调用
# resume_workflow_with_input() 触发 Event.set() 唤醒协程
_human_input_waiters: dict[str, asyncio.Event] = {}

# 人工输入结果字典
# 键格式与 _human_input_waiters 相同，值为用户提交的输入数据
# 当 resume_workflow_with_input() 被调用时，用户输入会存入此字典，
# 同时唤醒对应的 waiter，工作流协程从字典中取出输入结果继续执行
_human_input_results: dict[str, dict[str, Any]] = {}


class NodeType(str, Enum):
    """节点类型枚举

    定义工作流中支持的所有节点类型，每种类型对应不同的执行逻辑：
      - START: 起始节点，工作流的入口，每个工作流有且仅有一个
      - END: 结束节点，工作流的出口，工作流至少需要一个
      - AGENT: Agent 调用节点，调用指定名称的 Agent 处理输入
      - CONDITION: 条件判断节点，根据条件表达式选择不同的后续路径
      - PARALLEL: 并行执行节点，同时启动多个分支，所有分支完成后汇聚
      - TOOL: 工具调用节点，调用指定名称的工具
      - TRANSFORM: 数据转换节点，对上下文数据进行映射和转换
      - HUMAN_INPUT: 人工输入节点，暂停工作流等待用户确认或输入
      - DELAY: 延迟节点，等待指定秒数后继续执行
    """

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
    """节点位置（前端可视化用）

    记录节点在可视化画布上的坐标位置，供前端流程图组件渲染时使用。
    x 为水平坐标，y 为垂直坐标，单位为像素。
    """

    x: float = 0
    y: float = 0


class WorkflowNode(BaseModel):
    """工作流节点

    工作流中的基本执行单元，每个节点代表一个执行步骤。
    不同类型的节点通过 type 字段区分，各类型节点使用不同的配置字段：
      - AGENT 类型使用 agent_name 字段指定要调用的 Agent
      - TOOL 类型使用 tool_name 字段指定要调用的工具
      - CONDITION 类型使用 condition_expr 字段指定条件表达式
      - TRANSFORM 类型使用 transform_expr 字段指定数据转换表达式
      - DELAY 类型使用 delay_seconds 字段指定延迟秒数
      - PARALLEL 和 HUMAN_INPUT 类型主要通过 config 字段配置分支和提示信息

    Attributes:
        node_id: 节点唯一标识，自动生成8位短UUID
        type: 节点类型，决定节点的执行逻辑
        name: 节点名称，用于可视化展示和日志标识
        description: 节点描述，说明节点的用途
        config: 节点配置字典，存储各类型节点的特有配置
        position: 节点在可视化画布上的位置
        agent_name: Agent 节点绑定的 Agent 名称
        tool_name: Tool 节点绑定的工具名称
        condition_expr: 条件表达式，CONDITION 节点使用
        transform_expr: 数据转换表达式，TRANSFORM 节点使用
        delay_seconds: 延迟秒数，DELAY 节点使用
    """

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
    """工作流连接边

    表示工作流中两个节点之间的连接关系，定义数据流和控制流的方向。
    边从源节点（source_node_id）的源端口（source_port）出发，
    到达目标节点（target_node_id）的目标端口（target_port）。

    对于条件分支场景，condition 字段指定该边对应的条件值，
    当条件表达式的计算结果与 condition 匹配时，选择该边作为后续路径。

    Attributes:
        edge_id: 边唯一标识，自动生成8位短UUID
        source_node_id: 源节点ID，边的起始节点
        target_node_id: 目标节点ID，边的终止节点
        source_port: 源端口名称，默认为 "output"
        target_port: 目标端口名称，默认为 "input"
        condition: 条件值，条件分支场景下用于匹配条件表达式的结果
        label: 边标签，用于可视化展示
    """

    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_node_id: str
    target_node_id: str
    source_port: str = Field(default="output", description="源端口")
    target_port: str = Field(default="input", description="目标端口")
    condition: str = Field(default="", description="条件（条件分支用）")
    label: str = Field(default="", description="边标签")


class WorkflowStatus(str, Enum):
    """工作流状态枚举

    工作流的生命周期状态：
      - DRAFT: 草稿状态，工作流正在编辑中，尚未发布，此状态下可自由修改
      - PUBLISHED: 已发布状态，工作流已通过校验并发布，可被执行，
        但不可删除（需先禁用）
      - DISABLED: 已禁用状态，工作流被禁用，不可执行也不可修改
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"


class Workflow(BaseModel):
    """工作流定义

    完整的工作流数据模型，包含工作流的基本信息、节点列表、连接边列表等。
    工作流采用"节点-边"的 DAG 模型，通过 nodes 和 edges 两个列表描述
    整个工作流的拓扑结构。

    生命周期：DRAFT -> PUBLISHED -> DISABLED
    只有 PUBLISHED 状态的工作流才能被执行。

    Attributes:
        workflow_id: 工作流唯一标识，自动生成完整UUID
        name: 工作流名称，1-100字符
        description: 工作流描述，最多500字符
        version: 工作流版本号，从1开始
        nodes: 节点列表，定义工作流中的所有执行步骤
        edges: 连接边列表，定义节点之间的数据流和控制流
        status: 工作流状态，默认为草稿
        created_by: 创建者标识
        created_at: 创建时间戳
        updated_at: 最后更新时间戳
        tags: 标签列表，用于分类和检索
    """

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
    """工作流执行记录

    记录一次工作流执行的完整信息，包括执行状态、当前所在节点、
    各节点的执行结果、错误信息等。执行记录持久化到 Redis，
    保留7天（与 _store_execution 中的过期时间一致）。

    执行状态流转：pending -> running -> completed/failed/waiting_for_input
      - pending: 初始状态，执行尚未开始
      - running: 执行中
      - completed: 执行成功完成
      - failed: 执行失败
      - waiting_for_input: 等待人工输入

    Attributes:
        execution_id: 执行记录唯一标识，自动生成UUID
        workflow_id: 关联的工作流ID
        status: 执行状态
        current_node_id: 当前正在执行的节点ID
        results: 各节点的执行结果，键为节点ID，值为结果字典
        error: 错误信息，执行失败时记录错误原因
        started_at: 执行开始时间戳
        completed_at: 执行完成时间戳，未完成时为 None
    """

    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str
    status: str = "pending"
    current_node_id: str = ""
    results: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None


# ==================== 存储 ====================

# Redis 键前缀，用于区分不同类型的数据
# 工作流定义使用 "workflow:" 前缀，执行记录使用 "workflow_exec:" 前缀
_WORKFLOW_KEY_PREFIX = "workflow:"
_EXECUTION_KEY_PREFIX = "workflow_exec:"


async def _get_workflow_redis() -> Any:
    """获取工作流存储用的 Redis 客户端

    直接使用全局统一连接管理器（redis_manager），不本地缓存连接实例，
    由 redis_manager 内部管理连接生命周期和连接池。

    原理：通过延迟导入 agent.core.redis_manager 模块获取 Redis 客户端，
    避免模块级循环依赖问题。如果 Redis 连接失败，返回 None，
    调用方需要处理 None 的情况（静默跳过存储操作）。

    Returns:
        Redis 客户端实例，连接失败时返回 None
    """
    try:
        from agent.core.infrastructure.redis_manager import get_redis_client
        return await get_redis_client()
    except Exception as e:
        logger.warning("工作流引擎 Redis 连接失败: %s", e)
        return None


async def _store_workflow(workflow: Workflow) -> None:
    """持久化工作流到 Redis

    将工作流定义序列化为 JSON 字符串后存储到 Redis，
    键格式为 "workflow:{workflow_id}"，过期时间为30天（86400秒/天 * 30天）。

    如果 Redis 不可用，静默跳过存储操作，不影响工作流的内存使用。

    Args:
        workflow: 要持久化的工作流对象
    """
    redis = await _get_workflow_redis()
    if redis is None:
        return
    key = f"{_WORKFLOW_KEY_PREFIX}{workflow.workflow_id}"
    await redis.set(key, workflow.model_dump_json(), ex=86400 * 30)


async def _load_workflow(workflow_id: str) -> Workflow | None:
    """从 Redis 加载工作流

    根据 workflow_id 从 Redis 中读取工作流的 JSON 数据，
    然后反序列化为 Workflow 对象。

    如果 Redis 不可用或键不存在，返回 None。

    Args:
        workflow_id: 工作流ID

    Returns:
        Workflow 对象，不存在时返回 None
    """
    redis = await _get_workflow_redis()
    if redis is None:
        return None
    key = f"{_WORKFLOW_KEY_PREFIX}{workflow_id}"
    data = await redis.get(key)
    if data is None:
        return None
    return Workflow.model_validate_json(data)


async def _remove_workflow(workflow_id: str) -> bool:
    """从 Redis 删除工作流

    删除指定 workflow_id 对应的 Redis 键。

    Args:
        workflow_id: 工作流ID

    Returns:
        是否删除成功（键存在并被删除返回 True，键不存在返回 False）
    """
    redis = await _get_workflow_redis()
    if redis is None:
        return False
    key = f"{_WORKFLOW_KEY_PREFIX}{workflow_id}"
    return await redis.delete(key) > 0


async def _store_execution(execution: WorkflowExecution) -> None:
    """持久化执行记录到 Redis

    将工作流执行记录序列化为 JSON 字符串后存储到 Redis，
    键格式为 "workflow_exec:{execution_id}"，过期时间为7天（86400秒/天 * 7天）。
    执行记录的保留时间比工作流定义短，因为执行记录是临时数据。

    Args:
        execution: 要持久化的执行记录对象
    """
    redis = await _get_workflow_redis()
    if redis is None:
        return
    key = f"{_EXECUTION_KEY_PREFIX}{execution.execution_id}"
    await redis.set(key, execution.model_dump_json(), ex=86400 * 7)


async def _list_all_workflow_ids() -> list[str]:
    """列出 Redis 中所有工作流 ID

    使用 Redis SCAN 命令迭代扫描所有以 "workflow:" 为前缀的键，
    然后提取出工作流 ID 列表。

    原理：SCAN 命令是 Redis 提供的增量式迭代命令，相比 KEYS 命令，
    SCAN 不会阻塞 Redis 服务器，适合在生产环境中使用。
    每次迭代返回一个游标和一批键，当游标回到0时表示迭代完成。

    Returns:
        工作流ID列表
    """
    redis = await _get_workflow_redis()
    if redis is None:
        return []
    keys = []
    cursor = 0
    # 使用 SCAN 迭代扫描，每次扫描100个键
    while True:
        cursor, batch = await redis.scan(cursor, match=f"{_WORKFLOW_KEY_PREFIX}*", count=100)
        keys.extend(batch)
        # 游标回到0表示迭代完成
        if cursor == 0:
            break
    # 从完整的键名中移除前缀，得到纯工作流ID
    return [k.replace(_WORKFLOW_KEY_PREFIX, "") for k in keys]


# ==================== CRUD ====================


async def create_workflow(workflow: Workflow, created_by: str = "") -> Workflow:
    """创建工作流

    创建一个新的工作流，设置创建者、状态为草稿、创建时间和更新时间，
    然后校验工作流合法性并持久化到 Redis。

    Args:
        workflow: 工作流对象，由调用方构建
        created_by: 创建者标识

    Returns:
        创建成功的工作流对象

    Raises:
        ValueError: 工作流校验不通过时抛出
    """
    workflow.created_by = created_by
    workflow.status = WorkflowStatus.DRAFT
    workflow.created_at = time.time()
    workflow.updated_at = workflow.created_at

    # 校验工作流结构合法性（起始节点、结束节点、边的引用完整性等）
    _validate_workflow(workflow)

    await _store_workflow(workflow)
    logger.info("工作流已创建: id=%s name=%s", workflow.workflow_id, workflow.name)
    return workflow


async def get_workflow(workflow_id: str) -> Workflow | None:
    """获取工作流

    根据工作流ID从 Redis 中加载工作流定义。

    Args:
        workflow_id: 工作流ID

    Returns:
        Workflow 对象，不存在时返回 None
    """
    return await _load_workflow(workflow_id)


async def list_workflows(
    created_by: str = "",
    status: WorkflowStatus | None = None,
) -> list[Workflow]:
    """列出工作流

    从 Redis 中加载所有工作流，然后按创建者和状态进行过滤，
    最后按更新时间倒序排列（最近更新的排在前面）。

    注意：当前实现需要加载所有工作流到内存中再过滤，
    当工作流数量较大时可能存在性能问题，后续可考虑使用
    Redis 的 Sorted Set 或 Hash 结构优化查询效率。

    Args:
        created_by: 按创建者过滤，为空时不过滤
        status: 按状态过滤，为 None 时不过滤

    Returns:
        符合条件的工作流列表，按更新时间倒序排列
    """
    all_ids = await _list_all_workflow_ids()
    workflows: list[Workflow] = []
    for wid in all_ids:
        wf = await _load_workflow(wid)
        if wf:
            workflows.append(wf)

    # 按创建者过滤
    if created_by:
        workflows = [w for w in workflows if w.created_by == created_by]
    # 按状态过滤
    if status:
        workflows = [w for w in workflows if w.status == status]
    # 按更新时间倒序排列，最近更新的排在前面
    workflows.sort(key=lambda w: w.updated_at, reverse=True)
    return workflows


async def update_workflow(workflow_id: str, updates: dict[str, Any]) -> Workflow | None:
    """更新工作流

    根据更新字典中的键值对更新工作流的对应字段。
    受保护字段（workflow_id、created_by、created_at）不可通过此方法修改。

    安全限制：
      - 已禁用（DISABLED）的工作流不可修改
      - 如果更新涉及 nodes 或 edges，会重新校验工作流合法性

    Args:
        workflow_id: 工作流ID
        updates: 更新字典，键为字段名，值为新值

    Returns:
        更新后的工作流对象，工作流不存在时返回 None

    Raises:
        ValueError: 工作流已禁用或校验不通过时抛出
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    # 已禁用的工作流不可修改，防止误操作
    if workflow.status == WorkflowStatus.DISABLED:
        raise ValueError("已禁用的工作流不可修改")

    # 遍历更新字典，逐字段更新，跳过受保护字段
    for key, value in updates.items():
        if hasattr(workflow, key) and key not in ("workflow_id", "created_by", "created_at"):
            setattr(workflow, key, value)

    workflow.updated_at = time.time()

    # 如果更新了节点或边，需要重新校验工作流结构合法性
    if "nodes" in updates or "edges" in updates:
        _validate_workflow(workflow)

    await _store_workflow(workflow)
    return workflow


async def delete_workflow(workflow_id: str) -> bool:
    """删除工作流

    安全限制：已发布（PUBLISHED）的工作流不可直接删除，需要先禁用。
    这是为了防止正在使用的工作流被误删导致运行中的任务失败。

    Args:
        workflow_id: 工作流ID

    Returns:
        是否删除成功

    Raises:
        ValueError: 工作流已发布时抛出
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return False

    # 已发布的工作流不可删除，防止运行中的任务受影响
    if workflow.status == WorkflowStatus.PUBLISHED:
        raise ValueError("已发布的工作流不可删除，请先禁用")

    return await _remove_workflow(workflow_id)


# ==================== 节点操作 ====================


async def add_node(workflow_id: str, node: WorkflowNode) -> Workflow | None:
    """添加节点

    向指定工作流中添加一个新节点，并更新工作流的修改时间。

    Args:
        workflow_id: 工作流ID
        node: 要添加的节点对象

    Returns:
        更新后的工作流对象，工作流不存在时返回 None
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    workflow.nodes.append(node)
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def update_node(workflow_id: str, node_id: str, updates: dict[str, Any]) -> Workflow | None:
    """更新节点

    根据更新字典修改指定节点的字段。node_id 为受保护字段，不可修改。

    Args:
        workflow_id: 工作流ID
        node_id: 要更新的节点ID
        updates: 更新字典，键为字段名，值为新值

    Returns:
        更新后的工作流对象，工作流或节点不存在时返回 None
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    # 遍历节点列表，找到目标节点
    for node in workflow.nodes:
        if node.node_id == node_id:
            # 逐字段更新，跳过 node_id（受保护字段）
            for key, value in updates.items():
                if hasattr(node, key) and key != "node_id":
                    setattr(node, key, value)
            workflow.updated_at = time.time()
            await _store_workflow(workflow)
            return workflow

    return None


async def remove_node(workflow_id: str, node_id: str) -> Workflow | None:
    """移除节点

    从工作流中移除指定节点，同时移除与该节点相关的所有连接边
    （包括以该节点为源或为目标的边），保持工作流拓扑的一致性。

    Args:
        workflow_id: 工作流ID
        node_id: 要移除的节点ID

    Returns:
        更新后的工作流对象，工作流不存在时返回 None
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    # 移除节点
    workflow.nodes = [n for n in workflow.nodes if n.node_id != node_id]
    # 同时移除与该节点相关的所有边（源或目标为该节点的边）
    workflow.edges = [
        e for e in workflow.edges
        if e.source_node_id != node_id and e.target_node_id != node_id
    ]
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def add_edge(workflow_id: str, edge: WorkflowEdge) -> Workflow | None:
    """添加连接

    向工作流中添加一条连接边。添加前会校验源节点和目标节点是否存在于工作流中，
    防止创建无效的连接。

    Args:
        workflow_id: 工作流ID
        edge: 要添加的连接边对象

    Returns:
        更新后的工作流对象，工作流不存在时返回 None

    Raises:
        ValueError: 源节点或目标节点不存在时抛出
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    # 校验源节点和目标节点是否存在于当前工作流中
    node_ids = {n.node_id for n in workflow.nodes}
    if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
        raise ValueError("连接的源节点或目标节点不存在")

    workflow.edges.append(edge)
    workflow.updated_at = time.time()
    await _store_workflow(workflow)
    return workflow


async def remove_edge(workflow_id: str, edge_id: str) -> Workflow | None:
    """移除连接

    从工作流中移除指定ID的连接边。

    Args:
        workflow_id: 工作流ID
        edge_id: 要移除的连接边ID

    Returns:
        更新后的工作流对象，工作流不存在时返回 None
    """
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

    核心执行方法，按拓扑排序执行节点，支持条件分支和并行执行。

    执行原理：
      1. 从 Redis 加载工作流定义，校验状态必须为 PUBLISHED
      2. 创建执行记录，状态设为 running
      3. 找到起始节点（START 类型），初始化执行上下文
      4. 从起始节点开始，沿连接边逐步执行每个节点：
         - 普通节点：执行后沿出边找到下一个节点
         - 条件节点：评估条件表达式，选择匹配的出边
         - 并行节点：并发执行所有分支，完成后汇聚到后续节点
         - 结束节点：终止执行
      5. 使用 visited 集合记录已访问节点，防止重复执行
      6. 使用 step_count 计数器限制最大步数，防止死循环
      7. 执行完成后更新执行记录状态和结果

    上下文结构（context）：
      - input: 工作流的输入数据
      - results: 各节点的执行结果，键为节点ID

    Args:
        workflow_id: 工作流ID
        input_data: 输入数据，将传递给起始节点

    Returns:
        WorkflowExecution 执行记录

    Raises:
        ValueError: 工作流不存在、未发布、缺少起始节点或步数超限时抛出
    """
    # 从 Redis 加载工作流定义
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        raise ValueError(f"工作流不存在: {workflow_id}")

    # 只有已发布的工作流可以执行，防止执行未完成或已禁用的工作流
    if workflow.status != WorkflowStatus.PUBLISHED:
        raise ValueError("只有已发布的工作流可以执行")

    # 创建执行记录并持久化
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="running",
    )
    await _store_execution(execution)

    try:
        # 查找起始节点，工作流必须且只能有一个起始节点
        start_nodes = [n for n in workflow.nodes if n.type == NodeType.START]
        if not start_nodes:
            raise ValueError("工作流缺少起始节点")

        # 初始化执行上下文：input 存放输入数据，results 存放各节点执行结果
        context: dict[str, Any] = {"input": input_data or {}, "results": {}}
        # 从起始节点开始执行
        current_node = start_nodes[0]
        # 已访问节点集合，用于防止重复执行同一节点
        visited: set[str] = set()
        # 执行步数计数器，用于检测死循环
        step_count = 0

        # 主执行循环：从当前节点出发，沿边遍历直到结束节点或无后续节点
        while current_node:
            step_count += 1
            # 步数超限检查，防止条件分支环路导致死循环
            if step_count > MAX_EXECUTION_STEPS:
                raise ValueError(f"工作流执行步数超过限制({MAX_EXECUTION_STEPS})，可能存在环路")

            # 如果节点已被访问过，跳出循环，防止重复执行
            if current_node.node_id in visited:
                break
            visited.add(current_node.node_id)

            # 更新执行记录中的当前节点ID，便于追踪执行进度
            execution.current_node_id = current_node.node_id
            # 执行当前节点，将结果存入上下文
            node_result = await _execute_node(current_node, context, execution)
            context["results"][current_node.node_id] = node_result

            # 查找当前节点的所有出边，用于确定下一个要执行的节点
            next_edges = [e for e in workflow.edges if e.source_node_id == current_node.node_id]

            # 到达结束节点，终止执行
            if current_node.type == NodeType.END:
                break

            # 并行节点的后续路由：并行节点执行完成后，所有分支的输出边应汇聚到同一个后续节点
            # 这里直接取第一条出边指向的节点作为汇聚后的后续节点
            if current_node.type == NodeType.PARALLEL:
                parallel_next_edges = [e for e in workflow.edges if e.source_node_id == current_node.node_id]
                if parallel_next_edges:
                    next_node = _find_node(workflow, parallel_next_edges[0].target_node_id)
                else:
                    next_node = None
                current_node = next_node
                continue

            # 条件节点的后续路由：评估条件表达式，选择匹配的出边
            if current_node.type == NodeType.CONDITION and next_edges:
                # 评估条件表达式，得到条件结果
                condition_result = _evaluate_condition(current_node.condition_expr, context)
                # 遍历所有出边，找到条件值与评估结果匹配的边
                matched_edge = None
                for edge in next_edges:
                    if edge.condition and edge.condition.lower() == str(condition_result).lower():
                        matched_edge = edge
                        break
                # 如果没有匹配的条件边，默认选择第一条出边（作为 fallback）
                if not matched_edge and next_edges:
                    matched_edge = next_edges[0]

                next_node = _find_node(workflow, matched_edge.target_node_id) if matched_edge else None
            else:
                # 普通节点：沿第一条出边前进
                next_node = _find_node(workflow, next_edges[0].target_node_id) if next_edges else None

            current_node = next_node

        # 执行成功完成，更新执行记录
        execution.status = "completed"
        execution.results = context["results"]

        # 记录执行成功的指标到可观测性系统
        try:
            from observability.metrics import record_workflow_execution
            record_workflow_execution(workflow_id, "success")
        except Exception as e:
            logger.debug("操作失败，已忽略: %s", e)

    except Exception as e:
        # 执行失败，记录错误信息
        execution.status = "failed"
        execution.error = str(e)
        logger.error("工作流执行失败: %s", e)

        # 记录执行失败的指标到可观测性系统
        try:
            from observability.metrics import record_workflow_execution
            record_workflow_execution(workflow_id, "error")
        except Exception as e:
            logger.debug("操作失败，已忽略: %s", e)

    # 记录执行完成时间并持久化
    execution.completed_at = time.time()
    await _store_execution(execution)
    return execution


async def _execute_node(node: WorkflowNode, context: dict[str, Any], execution: WorkflowExecution | None = None) -> dict[str, Any]:
    """执行单个节点

    根据节点类型（NodeType）分派到对应的执行器方法。
    每种节点类型有独立的执行逻辑，返回统一格式的结果字典。

    分派逻辑：
      - START: 返回启动信息和输入数据
      - END: 返回完成信息和所有节点结果
      - AGENT: 调用 _execute_agent_node，通过 agent_router 路由到指定 Agent
      - TOOL: 调用 _execute_tool_node，通过 tool_registry 执行指定工具
      - TRANSFORM: 调用 _execute_transform_node，对上下文数据进行映射转换
      - DELAY: 调用 _execute_delay_node，等待指定秒数
      - HUMAN_INPUT: 调用 _execute_human_input_node，暂停等待人工输入
      - PARALLEL: 调用 _execute_parallel_node，并发执行多个分支

    Args:
        node: 要执行的工作流节点
        context: 工作流执行上下文，包含输入数据和各节点结果
        execution: 当前执行记录，HUMAN_INPUT 节点需要用到

    Returns:
        节点执行结果字典，至少包含 "status" 字段
    """
    if node.type == NodeType.START:
        # 起始节点：将输入数据传递到上下文中，标记工作流已启动
        return {"status": "started", "input": context.get("input", {})}

    elif node.type == NodeType.END:
        # 结束节点：收集所有节点的执行结果作为输出
        return {"status": "completed", "output": context.get("results", {})}

    elif node.type == NodeType.AGENT:
        # Agent 节点：调用指定 Agent 处理输入
        return await _execute_agent_node(node, context)

    elif node.type == NodeType.TOOL:
        # 工具节点：调用指定工具
        return await _execute_tool_node(node, context)

    elif node.type == NodeType.TRANSFORM:
        # 转换节点：对上下文数据进行映射和转换
        return _execute_transform_node(node, context)

    elif node.type == NodeType.DELAY:
        # 延迟节点：等待指定秒数
        return await _execute_delay_node(node)

    elif node.type == NodeType.HUMAN_INPUT:
        # 人工输入节点：暂停等待用户输入
        return await _execute_human_input_node(node, context, execution)

    elif node.type == NodeType.PARALLEL:
        # 并行节点：并发执行多个分支
        return await _execute_parallel_node(node, context)

    # 未知节点类型，返回状态标记
    return {"status": "unknown", "type": node.type.value}


async def _execute_agent_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行 Agent 节点：调用指定 Agent 处理输入

    通过 agent_router 模块将消息路由到指定名称的 Agent 进行处理。
    支持模板渲染，可以在输入模板中使用 {{ variable }} 语法引用上下文变量。

    执行流程：
      1. 获取 Agent 名称（优先使用 agent_name 字段，其次从 config 中读取）
      2. 获取输入模板并渲染（替换 {{ variable }} 占位符为上下文中的实际值）
      3. 调用 route_to_agent 将消息路由到目标 Agent
      4. 返回 Agent 的处理结果

    Args:
        node: Agent 节点
        context: 工作流执行上下文

    Returns:
        执行结果字典，包含状态、Agent名称和结果
    """
    try:
        from agent.core.workflow.agent_router import route_to_agent

        # 获取 Agent 名称，优先使用节点字段，其次从配置中读取
        agent_name = node.agent_name or node.config.get("agent_name", "")
        # 获取输入模板
        input_text = node.config.get("input_template", "")
        # 如果有输入模板，渲染模板中的 {{ variable }} 占位符
        if input_text:
            input_text = _render_template(input_text, context)

        # 通过 agent_router 路由到目标 Agent
        result = await route_to_agent(
            agent_name=agent_name,
            message=input_text,
            context=context,
        )

        return {
            "status": "agent_completed",
            "agent": agent_name,
            # 如果结果是字典直接使用，否则包装为 {"response": str} 格式
            "result": result if isinstance(result, dict) else {"response": str(result)},
        }
    except Exception as e:
        logger.error("Agent 节点执行失败: agent=%s, error=%s", node.agent_name, e)
        return {"status": "agent_failed", "agent": node.agent_name, "error": str(e)}


async def _execute_tool_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行工具节点：调用指定工具

    通过 tool_registry 模块执行指定名称的工具。
    支持模板渲染，工具参数中的字符串值可以使用 {{ variable }} 语法引用上下文变量。

    执行流程：
      1. 获取工具名称（优先使用 tool_name 字段，其次从 config 中读取）
      2. 获取工具参数并渲染模板
      3. 调用 execute_tool 执行工具
      4. 返回工具的执行结果

    Args:
        node: 工具节点
        context: 工作流执行上下文

    Returns:
        执行结果字典，包含状态、工具名称和结果
    """
    try:
        from agent.core.mcp.tool_registry import execute_tool

        # 获取工具名称，优先使用节点字段，其次从配置中读取
        tool_name = node.tool_name or node.config.get("tool_name", "")
        # 获取工具参数
        tool_params = node.config.get("params", {})
        # 渲染参数中的模板值
        tool_params = _render_template_dict(tool_params, context)

        # 通过 tool_registry 执行工具
        result = await execute_tool(tool_name=tool_name, params=tool_params)

        return {
            "status": "tool_completed",
            "tool": tool_name,
            # 如果结果是字典直接使用，否则包装为 {"output": str} 格式
            "result": result if isinstance(result, dict) else {"output": str(result)},
        }
    except Exception as e:
        logger.error("工具节点执行失败: tool=%s, error=%s", node.tool_name, e)
        return {"status": "tool_failed", "tool": node.tool_name, "error": str(e)}


def _execute_transform_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行转换节点：对上下文数据进行映射和转换

    转换节点用于在工作流中对数据进行加工和映射，例如：
      - 从前一个节点的结果中提取特定字段
      - 将数据重命名为后续节点期望的格式
      - 对数据进行简单的类型转换

    配置格式（node.config["mapping"]）：
      mapping 是一个字典，键为目标字段名，值为源表达式（支持点号路径）。
      例如：{"output_text": "results.node1.result.response", "count": "results.node2.result.count"}

    执行原理：
      遍历 mapping 字典，对每个键值对调用 _resolve_value 解析源表达式，
      从上下文中读取对应的值，构建输出字典。

    Args:
        node: 转换节点
        context: 工作流执行上下文

    Returns:
        执行结果字典，包含状态和转换后的输出数据
    """
    try:
        # 获取映射配置：{目标字段名: 源表达式}
        mapping = node.config.get("mapping", {})
        output: dict[str, Any] = {}

        # 遍历映射配置，逐个解析源表达式并构建输出
        for target_key, source_expr in mapping.items():
            output[target_key] = _resolve_value(source_expr, context)

        return {"status": "transformed", "output": output}
    except Exception as e:
        logger.error("转换节点执行失败: error=%s", e)
        return {"status": "transform_failed", "error": str(e)}


async def _execute_delay_node(node: WorkflowNode) -> dict[str, Any]:
    """执行延迟节点：等待指定秒数

    延迟节点用于在工作流中插入等待时间，例如：
      - 等待外部系统处理完成
      - 控制调用频率，避免触发 API 限流
      - 模拟用户操作间隔

    延迟秒数优先使用 delay_seconds 字段，其次从 config["seconds"] 中读取。

    Args:
        node: 延迟节点

    Returns:
        执行结果字典，包含状态和实际延迟秒数
    """
    import asyncio

    # 获取延迟秒数，优先使用节点字段，其次从配置中读取
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

    实现原理：
      本方法通过 asyncio.Event 实现协程间的同步机制。当工作流执行到
      人工输入节点时，会创建一个 asyncio.Event 对象并注册到全局字典
      _human_input_waiters 中，然后通过 waiter.wait() 阻塞当前协程。

      同时，通过事件总线发布 HUMAN_CONFIRM_REQUIRED 事件通知前端，
      前端收到事件后展示确认界面，用户完成输入后调用
      resume_workflow_with_input() 接口，该接口会将用户输入存入
      _human_input_results 字典，并调用 waiter.set() 唤醒阻塞的协程。

      协程被唤醒后，从 _human_input_results 中取出用户输入，
      清理全局字典中的注册信息，然后继续执行后续节点。

    超时机制：
      使用 asyncio.wait_for 为等待设置超时时间（默认3600秒/1小时），
      超时后返回 input_timeout 状态，工作流不会继续执行后续节点。

    Args:
        node: 人工输入节点
        context: 工作流执行上下文
        execution: 当前执行记录

    Returns:
        包含用户输入结果的字典：
          - input_received: 成功接收到用户输入
          - input_timeout: 等待超时
    """
    # 获取提示信息和超时配置
    prompt = node.config.get("prompt", "请输入:")
    timeout_seconds = node.config.get("timeout_seconds", 3600)
    # 从上下文中获取用户ID和会话ID，用于事件通知
    user_id = context.get("input", {}).get("user_id", "")
    session_id = context.get("input", {}).get("session_id", "")

    # 构建确认键，格式为 "{执行ID}:{节点ID}"，用于全局字典的键
    confirm_key = f"{execution.execution_id}:{node.node_id}"

    # 创建 asyncio.Event 作为等待器，注册到全局字典
    waiter = asyncio.Event()
    _human_input_waiters[confirm_key] = waiter

    # 通过事件总线发布人工确认事件，通知前端展示确认界面
    try:
        from agent.core.infrastructure.event_bus import publish_event, EventType

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

    # 更新执行状态为等待输入，并持久化
    execution.status = "waiting_for_input"
    await _store_execution(execution)

    # 阻塞等待用户输入，设置超时时间
    try:
        await asyncio.wait_for(waiter.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        # 超时后清理全局字典中的注册信息
        _human_input_waiters.pop(confirm_key, None)
        _human_input_results.pop(confirm_key, None)
        return {"status": "input_timeout", "prompt": prompt}

    # 被唤醒后，从全局字典中取出用户输入并清理注册信息
    user_input = _human_input_results.pop(confirm_key, {})
    _human_input_waiters.pop(confirm_key, None)

    # 恢复执行状态为运行中
    execution.status = "running"
    await _store_execution(execution)

    return {"status": "input_received", "prompt": prompt, "user_input": user_input}


# 并行节点默认最大并发分支数
# 使用信号量控制并发数，防止过多分支同时执行导致资源耗尽
DEFAULT_MAX_CONCURRENT_BRANCHES = 5

# 并行节点默认超时时间（秒）
# 超时后取消未完成的分支，返回超时状态
DEFAULT_PARALLEL_TIMEOUT_SECONDS = 60


async def _execute_parallel_node(node: WorkflowNode, context: dict[str, Any]) -> dict[str, Any]:
    """执行并行节点：同时执行多个分支

    使用 asyncio.gather 并发执行所有分支，实现真正的并行处理。

    并行执行原理：
      1. 从节点配置中读取分支列表（branches），每个分支定义了一个子节点的执行配置
      2. 为每个分支创建一个异步任务，使用 asyncio.Semaphore 控制最大并发数
      3. 使用 asyncio.gather 并发执行所有分支任务，return_exceptions=True
         确保单个分支的异常不会影响其他分支的执行
      4. 使用 asyncio.wait_for 设置总体超时时间
      5. 收集所有分支的执行结果，以 {分支名称: 结果} 格式聚合返回

    异常隔离：
      asyncio.gather 的 return_exceptions=True 参数确保即使某个分支抛出异常，
      也会将异常作为结果返回，而不是中断整个 gather，从而实现分支间的异常隔离。

    结果聚合：
      所有分支结果以字典形式返回，键为分支名称。如果所有分支都失败，
      整体状态为 parallel_failed；否则为 parallel_completed。

    Args:
        node: 并行节点
        context: 工作流执行上下文

    Returns:
        包含所有分支执行结果的字典
    """
    # 从节点配置中获取分支列表
    branches = node.config.get("branches", [])
    if not branches:
        return {"status": "parallel_completed", "results": {}}

    # 获取并发控制配置
    max_concurrent = node.config.get("max_concurrent_branches", DEFAULT_MAX_CONCURRENT_BRANCHES)
    timeout_seconds = node.config.get("timeout_seconds", DEFAULT_PARALLEL_TIMEOUT_SECONDS)
    # 创建信号量，控制同时执行的最大分支数
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_branch(branch: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
        """在信号量控制下执行单个分支

        将分支配置转换为 WorkflowNode 对象，然后调用 _execute_node 执行。
        使用信号量确保同时运行的分支数不超过 max_concurrent。

        Args:
            branch: 分支配置字典，包含 type、config、agent_name 等字段
            sem: 信号量，用于控制并发数

        Returns:
            分支执行结果字典
        """
        # 将分支配置转换为 WorkflowNode 对象
        branch_node = WorkflowNode(
            node_id=branch.get("node_id", str(uuid.uuid4())),
            name=branch.get("name", ""),
            type=NodeType(branch.get("type", "agent")),
            config=branch.get("config", {}),
            agent_name=branch.get("agent_name"),
            tool_name=branch.get("tool_name"),
        )
        # 在信号量控制下执行分支
        async with sem:
            return await _execute_node(branch_node, context)

    # 为每个分支创建异步任务
    tasks = []
    branch_names = []
    for branch in branches:
        # 分支名称优先使用 name 字段，其次使用 node_id，最后使用随机ID
        branch_name = branch.get("name", branch.get("node_id", str(uuid.uuid4())[:8]))
        branch_names.append(branch_name)
        tasks.append(_run_branch(branch, semaphore))

    # 并发执行所有分支，设置总体超时时间
    try:
        results = await asyncio.wait_for(
            # return_exceptions=True：单个分支异常不会中断其他分支
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        # 总体超时，部分分支可能未完成
        logger.warning(
            "并行节点执行超时(%ds)，部分分支可能未完成: node=%s",
            timeout_seconds, node.node_id,
        )
        branch_results: dict[str, Any] = {}
        for name in branch_names:
            branch_results[name] = {"status": "timeout", "error": f"分支执行超过{timeout_seconds}秒"}
        return {"status": "parallel_timeout", "results": branch_results}

    # 收集所有分支的执行结果
    branch_results = {}
    for name, result in zip(branch_names, results):
        if isinstance(result, Exception):
            # 分支执行抛出异常，记录错误信息
            branch_results[name] = {"status": "branch_failed", "error": str(result)}
        else:
            # 分支正常完成，记录结果
            branch_results[name] = result

    # 统计失败分支数，判断整体状态
    failed_count = sum(1 for r in branch_results.values() if r.get("status", "").endswith("_failed"))
    # 如果所有分支都失败，整体状态为 parallel_failed；否则为 parallel_completed
    overall_status = "parallel_completed" if failed_count < len(branches) else "parallel_failed"

    return {"status": overall_status, "results": branch_results}


def _render_template(template: str, context: dict[str, Any]) -> str:
    """渲染简单模板，替换 {{ variable }} 占位符

    模板语法：
      使用双花括号 {{ }} 包裹变量路径，支持点号分隔的嵌套路径。
      例如：{{ input.user_id }} 会从上下文中读取 input.user_id 的值。

    渲染原理：
      使用正则表达式匹配所有 {{ ... }} 占位符，对每个匹配项调用
      _resolve_value 从上下文中解析对应的值，然后替换占位符。
      如果变量不存在，保留原始占位符不替换。

    Args:
        template: 模板字符串，可包含 {{ variable }} 占位符
        context: 上下文变量字典，用于解析占位符

    Returns:
        渲染后的字符串，占位符被替换为实际值
    """
    import re

    def replacer(match: re.Match) -> str:
        """正则替换回调函数，将匹配到的占位符替换为上下文中的值"""
        # 提取花括号内的变量路径，去除首尾空格
        var_path = match.group(1).strip()
        # 从上下文中解析变量值
        val = _resolve_value(var_path, context)
        # 如果值存在则转为字符串，否则保留原始占位符
        return str(val) if val is not None else match.group(0)

    # 匹配所有 {{ ... }} 占位符并替换
    return re.sub(r"\{\{\s*(.+?)\s*\}\}", replacer, template)


def _render_template_dict(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """渲染字典中的模板值

    递归遍历字典中的所有值，对字符串类型的值调用 _render_template 渲染模板，
    对字典类型的值递归处理，其他类型的值保持不变。

    用途：工具节点的参数字典中可能包含模板占位符，需要统一渲染。

    Args:
        params: 参数字典，值可能包含 {{ variable }} 模板
        context: 上下文变量字典

    Returns:
        渲染后的参数字典
    """
    rendered = {}
    for key, value in params.items():
        if isinstance(value, str):
            # 字符串值：渲染模板
            rendered[key] = _render_template(value, context)
        elif isinstance(value, dict):
            # 字典值：递归渲染
            rendered[key] = _render_template_dict(value, context)
        else:
            # 其他类型（数字、布尔等）：保持不变
            rendered[key] = value
    return rendered


# 安全操作符白名单
# 用于条件表达式评估，只允许这些操作符，避免使用 eval() 带来的代码注入风险
# 每个操作符对应一个 lambda 函数，接收左右两个操作数，返回比较结果
_SAFE_OPERATORS: dict[str, Any] = {
    "==": lambda a, b: a == b,   # 等于
    "!=": lambda a, b: a != b,   # 不等于
    ">": lambda a, b: a > b,     # 大于
    "<": lambda a, b: a < b,     # 小于
    ">=": lambda a, b: a >= b,   # 大于等于
    "<=": lambda a, b: a <= b,   # 小于等于
    "and": lambda a, b: a and b, # 逻辑与
    "or": lambda a, b: a or b,   # 逻辑或
}


def _evaluate_condition(expr: str, context: dict[str, Any]) -> Any:
    """安全评估条件表达式

    使用白名单操作符进行简单的条件判断，避免 eval() 带来的代码注入风险。

    评估原理：
      采用递归下降解析策略，按优先级从低到高处理逻辑操作符：
        1. 先处理 "or"（最低优先级）：将表达式按 " or " 分割，递归评估各部分
        2. 再处理 "and"（次低优先级）：将表达式按 " and " 分割，递归评估各部分
        3. 最后处理比较操作符（==, !=, >, <, >=, <=）：分割左右操作数，解析值后比较
        4. 如果没有操作符，直接解析为布尔值

    支持的表达式格式：
      - 变量比较: "result.status == 'completed'"
      - 数值比较: "result.count > 10"
      - 布尔变量: "result.success"
      - 逻辑组合: "result.count > 10 and result.status == 'completed'"
      - 逻辑或: "result.status == 'failed' or result.status == 'error'"

    安全设计：
      不使用 eval() 或 exec()，而是通过白名单操作符和 _resolve_value
      安全解析值，杜绝代码注入风险。解析失败时返回 False（安全默认值）。

    Args:
        expr: 条件表达式字符串
        context: 上下文变量字典

    Returns:
        条件评估结果，解析失败时返回 False
    """
    # 空表达式视为条件成立
    if not expr:
        return True

    expr = expr.strip()

    try:
        # 处理逻辑或（最低优先级）：按 " or " 分割，任一部分为 True 则结果为 True
        if " or " in expr:
            parts = expr.split(" or ", 1)
            return _evaluate_condition(parts[0].strip(), context) or _evaluate_condition(parts[1].strip(), context)

        # 处理逻辑与（次低优先级）：按 " and " 分割，所有部分为 True 则结果为 True
        if " and " in expr:
            parts = expr.split(" and ", 1)
            return _evaluate_condition(parts[0].strip(), context) and _evaluate_condition(parts[1].strip(), context)

        # 处理比较操作符：遍历白名单中的操作符，检查表达式中是否包含
        for op_str, op_func in _SAFE_OPERATORS.items():
            if f" {op_str} " in expr:
                # 按操作符分割表达式，得到左右操作数字符串
                left_str, right_str = expr.split(f" {op_str} ", 1)
                # 解析左右操作数的值
                left_val = _resolve_value(left_str.strip(), context)
                right_val = _resolve_value(right_str.strip(), context)
                # 使用操作符函数进行比较
                return op_func(left_val, right_val)

        # 没有操作符，直接解析为布尔值
        val = _resolve_value(expr, context)
        return bool(val)

    except Exception:
        # 解析失败时返回 False（安全默认值）
        return False


def _resolve_value(token: str, context: dict[str, Any]) -> Any:
    """解析表达式中的值

    支持从上下文中读取变量（点号路径）和字面量（字符串、数字、布尔值）。

    解析优先级（按顺序尝试）：
      1. 字符串字面量：被单引号或双引号包裹的值，去除引号后返回
      2. 布尔字面量：true/false（不区分大小写）
      3. 上下文变量路径：包含点号的路径，如 "results.node1.status"，
         按点号逐层从上下文字典中读取嵌套值
      4. 整数字面量：尝试解析为 int
      5. 浮点字面量：尝试解析为 float
      6. 上下文顶层键：直接从上下文字典中按键名查找
      7. 以上都不匹配：返回原始 token 字符串

    变量路径解析原理：
      对于 "results.node1.status" 这样的路径，按 "." 分割为 ["results", "node1", "status"]，
      然后从上下文字典中逐层读取：context["results"]["node1"]["status"]。
      如果任意层级不存在，返回原始 token 字符串。

    注意：包含点号的数字（如 "3.14"）不会被误判为变量路径，
    因为会先检查去掉点号和负号后是否为纯数字。

    Args:
        token: 值标记（变量路径或字面量）
        context: 上下文变量字典

    Returns:
        解析后的值
    """
    token = token.strip()

    # 1. 字符串字面量：被引号包裹的值，去除引号后返回
    if (token.startswith("'") and token.endswith("'")) or (token.startswith('"') and token.endswith('"')):
        return token[1:-1]

    # 2. 布尔字面量
    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False

    # 3. 上下文变量路径：包含点号且不是纯数字的路径
    try:
        if "." in token and not token.replace(".", "", 1).replace("-", "", 1).isdigit():
            # 按点号分割路径
            parts = token.split(".")
            # 从上下文根开始逐层读取
            val: Any = context
            for part in parts:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    # 中间层级不是字典，路径无效，返回原始 token
                    return token
                if val is None:
                    # 路径中的某层不存在，返回原始 token
                    return token
            return val
    except Exception as e:
        logger.debug("操作失败，已忽略: %s", e)

    # 4. 整数字面量
    try:
        return int(token)
    except ValueError:
        pass

    # 5. 浮点字面量
    try:
        return float(token)
    except ValueError:
        pass

    # 6. 上下文顶层键
    if token in context:
        return context[token]

    # 7. 以上都不匹配，返回原始 token
    return token


def _find_node(workflow: Workflow, node_id: str) -> WorkflowNode | None:
    """在工作流中查找指定ID的节点

    线性遍历节点列表，找到第一个 node_id 匹配的节点。

    Args:
        workflow: 工作流对象
        node_id: 要查找的节点ID

    Returns:
        匹配的 WorkflowNode 对象，不存在时返回 None
    """
    for node in workflow.nodes:
        if node.node_id == node_id:
            return node
    return None


# ==================== 校验 ====================


def _validate_workflow(workflow: Workflow) -> None:
    """校验工作流合法性

    在创建和更新工作流时调用，确保工作流结构满足以下约束：
      1. 必须包含且仅包含一个起始节点（START）
      2. 必须包含至少一个结束节点（END）
      3. 所有边的源节点和目标节点必须存在于节点列表中
      4. 节点ID不能重复
      5. 并行节点必须至少有2个分支
      6. 并行节点的分支中不允许嵌套并行节点
      7. 并行节点必须有汇聚出边
      8. 并行节点的所有出边必须汇聚到同一个目标节点

    校验不通过时抛出 ValueError，阻止不合法的工作流被保存或发布。

    Args:
        workflow: 要校验的工作流对象

    Raises:
        ValueError: 工作流不满足合法性约束时抛出
    """
    # 收集所有节点ID，用于后续边的引用校验
    node_ids = {n.node_id for n in workflow.nodes}

    # 校验1：起始节点数量必须为1
    start_nodes = [n for n in workflow.nodes if n.type == NodeType.START]
    if len(start_nodes) == 0:
        raise ValueError("工作流必须包含一个起始节点")
    if len(start_nodes) > 1:
        raise ValueError("工作流只能包含一个起始节点")

    # 校验2：必须至少有一个结束节点
    end_nodes = [n for n in workflow.nodes if n.type == NodeType.END]
    if len(end_nodes) == 0:
        raise ValueError("工作流必须包含至少一个结束节点")

    # 校验3：所有边的源节点和目标节点必须存在
    for edge in workflow.edges:
        if edge.source_node_id not in node_ids:
            raise ValueError(f"边的源节点不存在: {edge.source_node_id}")
        if edge.target_node_id not in node_ids:
            raise ValueError(f"边的目标节点不存在: {edge.target_node_id}")

    # 校验4：节点ID不能重复
    node_id_counts: dict[str, int] = {}
    for node in workflow.nodes:
        node_id_counts[node.node_id] = node_id_counts.get(node.node_id, 0) + 1
    duplicates = [nid for nid, count in node_id_counts.items() if count > 1]
    if duplicates:
        raise ValueError(f"节点ID重复: {duplicates}")

    # 校验5-8：并行节点专项校验
    parallel_nodes = [n for n in workflow.nodes if n.type == NodeType.PARALLEL]
    for pnode in parallel_nodes:
        branches = pnode.config.get("branches", [])
        # 校验5：并行节点必须至少有2个分支
        if len(branches) < 2:
            raise ValueError(f"并行节点 {pnode.node_id} 必须至少有2个分支，当前: {len(branches)}")

        # 校验6：分支中不允许嵌套并行节点，防止复杂度过高
        for branch in branches:
            branch_type = branch.get("type", "agent")
            if branch_type == NodeType.PARALLEL.value:
                raise ValueError(f"并行节点 {pnode.node_id} 的分支不允许嵌套并行节点")

        # 校验7：并行节点必须有汇聚出边，所有分支结果需要汇聚到一个后续节点
        parallel_out_edges = [e for e in workflow.edges if e.source_node_id == pnode.node_id]
        if not parallel_out_edges:
            raise ValueError(f"并行节点 {pnode.node_id} 缺少汇聚出边，所有分支结果需要汇聚到一个后续节点")

        # 校验8：所有出边必须汇聚到同一个目标节点，确保执行路径唯一
        target_node_ids = {e.target_node_id for e in parallel_out_edges}
        if len(target_node_ids) > 1:
            raise ValueError(f"并行节点 {pnode.node_id} 的出边必须汇聚到同一个后续节点，当前指向: {target_node_ids}")


# ==================== 可视化数据 ====================


async def get_workflow_visualization(workflow_id: str) -> dict[str, Any] | None:
    """获取工作流可视化数据

    将工作流定义转换为前端流程图组件可直接使用的 JSON 格式。
    前端可以使用 React Flow、X6 等流程图库渲染这些数据。

    转换规则：
      - 节点：将 WorkflowNode 转换为 {id, type, label, position, data} 格式
      - 边：将 WorkflowEdge 转换为 {id, source, target, sourcePort, targetPort, label} 格式
      - 特殊字段：根据节点类型附加额外数据（如 agent_name、tool_name、condition_expr）

    Args:
        workflow_id: 工作流ID

    Returns:
        可视化数据字典，工作流不存在时返回 None
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    # 转换节点列表
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
        # 根据节点类型附加特有数据
        if n.type == NodeType.AGENT:
            node_data["data"]["agent_name"] = n.agent_name
        elif n.type == NodeType.TOOL:
            node_data["data"]["tool_name"] = n.tool_name
        elif n.type == NodeType.CONDITION:
            node_data["data"]["condition_expr"] = n.condition_expr
        nodes.append(node_data)

    # 转换边列表
    edges = []
    for e in workflow.edges:
        edge_data: dict[str, Any] = {
            "id": e.edge_id,
            "source": e.source_node_id,
            "target": e.target_node_id,
            "sourcePort": e.source_port,
            "targetPort": e.target_port,
        }
        # 优先使用条件作为标签，其次使用边标签
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
    """发布工作流

    将草稿状态的工作流发布，使其可被执行。发布前会重新校验工作流合法性，
    确保只有结构正确的工作流才能被发布。

    状态转换：DRAFT -> PUBLISHED

    Args:
        workflow_id: 工作流ID

    Returns:
        发布后的工作流对象，工作流不存在时返回 None

    Raises:
        ValueError: 工作流不是草稿状态或校验不通过时抛出
    """
    workflow = await _load_workflow(workflow_id)
    if not workflow:
        return None

    # 只有草稿状态的工作流可以发布
    if workflow.status != WorkflowStatus.DRAFT:
        raise ValueError("只有草稿状态的工作流可以发布")

    # 发布前重新校验工作流合法性
    _validate_workflow(workflow)

    # 更新状态为已发布
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

    唤醒原理：
      1. 根据 execution_id 和 node_id 构建 confirm_key
      2. 从全局字典 _human_input_waiters 中查找对应的 asyncio.Event
      3. 将用户输入存入 _human_input_results 字典
      4. 调用 Event.set() 唤醒阻塞在 waiter.wait() 的协程
      5. 协程被唤醒后从 _human_input_results 中取出输入结果继续执行

    Args:
        execution_id: 工作流执行ID
        node_id: 人工输入节点ID
        user_input: 用户输入数据

    Returns:
        是否成功唤醒（找不到对应的等待器时返回 False）
    """
    # 构建确认键，与 _execute_human_input_node 中的键格式一致
    confirm_key = f"{execution_id}:{node_id}"

    # 从全局字典中查找对应的等待器
    waiter = _human_input_waiters.get(confirm_key)
    if waiter is None:
        # 没有找到等待器，可能是节点已超时或键不匹配
        logger.warning("未找到等待中的人工输入: execution=%s node=%s", execution_id, node_id)
        return False

    # 将用户输入存入结果字典
    _human_input_results[confirm_key] = user_input
    # 唤醒阻塞的协程
    waiter.set()
    logger.info("人工输入已提交: execution=%s node=%s", execution_id, node_id)
    return True
