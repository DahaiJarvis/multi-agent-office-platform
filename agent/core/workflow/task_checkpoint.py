"""任务检查点存储与管理

为多Agent协作任务提供检查点保存和恢复能力，确保任务在中断后可从最近检查点继续。

核心能力：
  - 任务执行记录的创建、查询、更新
  - 步骤检查点的保存与读取
  - 中断任务扫描与标记
  - 心跳机制检测运行中的任务

存储结构（Redis）：
  - STRING task_exec:{execution_id} -> TaskExecution JSON, TTL 24h
  - STRING task_exec:session:{session_id} -> execution_id, TTL 24h
  - STRING task_exec:heartbeat:{execution_id} -> timestamp, TTL 60s
  - ZSET task_exec:running -> score=heartbeat_at, member=execution_id

与 long_task.py 的关系：
  long_task.py 管理显式定义的多步骤长任务（用户手动创建），
  本模块管理 Agent 协作任务的自动检查点（系统自动创建）。
  两者并行存在，互不影响。
"""

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.core.infrastructure.config import get_settings

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    """步骤类型枚举
    
    定义多Agent协作任务中可能出现的各种步骤类型。
    每种类型对应不同的处理逻辑和执行方式。
    
    继承自str和Enum，使得枚举值可以直接作为字符串使用，
    方便JSON序列化和日志输出。
    """
    
    INTENT_CLASSIFY = "intent_classify"
    AGENT_CALL = "agent_call"
    REVIEW = "review"
    AGGREGATE = "aggregate"
    HUMAN_CONFIRM = "human_confirm"
    TOOL_CALL = "tool_call"
    PARALLEL_EXEC = "parallel_exec"
    DEBATE_ROUND = "debate_round"
    VOTE_EXEC = "vote_exec"


class StepStatus(str, Enum):
    """步骤状态枚举
    
    定义步骤在执行过程中可能处于的各种状态。
    状态转换遵循特定的生命周期：
    PENDING -> RUNNING -> COMPLETED/FAILED/SKIPPED/DEGRADED
    
    继承自str和Enum，便于序列化和比较操作。
    """
    
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_CONFIRM = "waiting_confirm"
    DEGRADED = "degraded"


class FailurePolicy(str, Enum):
    """故障策略枚举
    
    定义当步骤执行失败时的处理策略。
    不同的策略适用于不同的业务场景和容错需求。
    
    继承自str和Enum，使得策略可以作为配置参数传递。
    
    策略说明：
        STRICT: 严格模式 - 任一步骤失败则暂停整个任务，等待人工决策
                适用于关键业务流程，要求完整性和准确性
        RELAXED: 宽松模式 - 尽量继续执行，缺失部分标注说明
                 适用于非关键业务，优先保证任务完成度
        MANUAL: 手动模式 - 每次失败都询问用户如何处理
                适用于需要用户参与决策的场景
    """

    STRICT = "strict"
    RELAXED = "relaxed"
    MANUAL = "manual"


class TaskStatus(str, Enum):
    """任务状态枚举
    
    定义任务在整个生命周期中可能处于的状态。
    状态转换遵循任务执行流程：
    PENDING -> RUNNING -> COMPLETED/FAILED/CANCELLED/INTERRUPTED
    
    继承自str和Enum，便于状态比较和序列化。
    
    状态说明：
        PENDING: 待执行 - 任务已创建但尚未开始执行
        RUNNING: 执行中 - 任务正在执行，心跳正常
        PAUSED: 已暂停 - 任务被主动暂停，可恢复
        COMPLETED: 已完成 - 任务成功完成所有步骤
        FAILED: 已失败 - 任务执行失败，无法继续
        CANCELLED: 已取消 - 任务被用户或系统取消
        INTERRUPTED: 已中断 - 任务因异常（如心跳超时）被中断
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StepCheckpoint(BaseModel):
    """步骤检查点数据模型
    
    记录单个步骤的执行状态和结果，用于任务恢复和状态追踪。
    每完成一个步骤自动保存检查点，确保任务可从任意步骤恢复。
    
    检查点的作用：
        1. 故障恢复：任务中断后可从最近的检查点继续执行
        2. 状态追踪：记录每个步骤的执行细节和结果
        3. 调试分析：提供完整的执行轨迹用于问题诊断
    
    继承自Pydantic的BaseModel，提供数据验证、序列化和类型提示功能。
    
    Attributes:
        step_index: 步骤索引，从0开始，标识步骤在任务中的位置
        step_type: 步骤类型，决定步骤的处理逻辑
        step_name: 步骤名称，人类可读的描述性名称
        agent_name: 执行该步骤的Agent名称，空字符串表示无Agent执行
        status: 步骤当前状态，默认为PENDING
        input_data: 步骤输入数据字典，记录步骤接收的参数
        output_data: 步骤输出数据字典，记录步骤产生的结果
        error: 错误信息字符串，步骤失败时记录错误详情
        fallback_used: 使用的降级策略名称，记录降级处理方式
        retry_count: 重试次数，记录步骤已重试的次数
        created_at: 检查点创建时间戳（Unix时间戳）
    """

    step_index: int
    step_type: StepType
    step_name: str
    agent_name: str = ""
    status: StepStatus = StepStatus.PENDING
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    fallback_used: str = ""
    failure_reason: str = ""
    retry_count: int = 0
    reasoning_chain: str = Field(default="", description="该步骤的推理链 JSON 字符串")
    created_at: float = Field(default_factory=time.time)

    # ===== 新增字段（spec 03 第 4.4 节）：版本链与回放溯源 =====
    version: int = Field(default=0, description="版本号，同 step_index 从0递增")
    source: str = Field(
        default="original",
        description="检查点来源：original(原始执行) / replay(回放生成) / retry(重试生成)",
    )
    source_execution_id: str = Field(
        default="",
        description="回放溯源：若 source=replay，记录原 execution_id",
    )


class TaskExecution(BaseModel):
    """任务执行记录数据模型
    
    记录一次完整的多Agent协作任务执行过程，包括任务规划、
    执行状态、检查点列表等所有相关信息。这是任务检查点
    系统的核心数据结构。
    
    任务执行记录的生命周期：
        1. 创建：任务开始时创建记录，状态为PENDING
        2. 执行：任务执行中状态变为RUNNING，持续更新心跳
        3. 完成：任务完成状态变为COMPLETED，记录最终结果
        4. 失败/中断：异常情况记录错误信息和当前状态
    
    继承自Pydantic的BaseModel，提供完整的数据验证和序列化能力。
    
    Attributes:
        execution_id: 执行记录唯一标识符，格式为"exec-{10位UUID}"
                     用于在整个系统中唯一标识一次任务执行
        session_id: 关联的会话ID，用于追踪用户会话上下文
        user_id: 用户ID，标识任务所属用户
        original_message: 用户原始消息内容，记录任务触发源
        intent_result: 意图分类结果字典，记录意图识别的详细信息
        collaboration_mode: 协作模式名称，如"sequential变体"、"parallel平行"等
        failure_policy: 故障处理策略，决定步骤失败时的行为
        steps: 预定义步骤列表，每个元素是一个步骤描述字典
        current_step: 当前执行到的步骤索引，从0开始
        checkpoints: 检查点列表，记录已完成步骤的状态
        status: 任务当前状态
        error: 错误信息，任务失败时记录错误详情
        result: 最终结果字典，任务成功完成时记录结果
        supplementary_messages: 补充消息列表，记录执行过程中的额外信息
        heartbeat_at: 最后心跳时间戳，用于检测任务是否存活
        created_at: 任务创建时间戳（Unix时间戳）
        updated_at: 任务最后更新时间戳（Unix时间戳）
    """

    execution_id: str = Field(default_factory=lambda: f"exec-{uuid.uuid4().hex[:10]}")
    session_id: str = ""
    user_id: str = ""
    original_message: str = ""
    intent_result: dict[str, Any] = Field(default_factory=dict)
    collaboration_mode: str = ""
    failure_policy: FailurePolicy = FailurePolicy.RELAXED
    steps: list[dict[str, Any]] = Field(default_factory=list)
    current_step: int = 0
    checkpoints: list[StepCheckpoint] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    error: str = ""
    result: dict[str, Any] | None = None
    supplementary_messages: list[str] = Field(default_factory=list)
    heartbeat_at: float = Field(default_factory=time.time)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    # ===== 新增字段（spec 03 第 4.5 节）：回放溯源 =====
    source: str = Field(
        default="original",
        description="执行来源：original(原始执行) / replay(回放生成)",
    )
    source_execution_id: str = Field(
        default="",
        description="回放溯源：若 source=replay，记录原 execution_id",
    )


class TaskCheckpointStore:
    """任务检查点存储管理器
    
    提供任务执行记录的完整生命周期管理，包括CRUD操作、
    检查点保存、心跳更新和中断任务扫描等核心功能。
    
    存储策略：
        优先使用Redis作为持久化存储，支持分布式部署和数据共享。
        当Redis不可用时，自动降级为内存存储，保证系统可用性。
    
    Redis数据结构设计：
        1. 任务记录：STRING类型，key为task_exec:{execution_id}
           存储完整的TaskExecution JSON，TTL为24小时
        2. 会话索引：STRING类型，key为task_exec:session:{session_id}
           存储execution_id，用于通过会话ID快速查找任务
        3. 心跳记录：STRING类型，key为task_exec:heartbeat:{execution_id}
           存储心跳时间戳，TTL为60秒，用于快速检测任务存活
        4. 运行任务集合：ZSET类型，key为task_exec:running
           score为心跳时间戳，member为execution_id
           用于高效扫描中断的任务
    
    内存降级模式：
        当Redis连接失败时，自动切换到内存存储模式。
        内存模式仅适用于单实例部署，不支持跨进程共享数据。
    """

    EXEC_KEY_PREFIX = "task_exec:"
    SESSION_INDEX_PREFIX = "task_exec:session:"
    HEARTBEAT_KEY_PREFIX = "task_exec:heartbeat:"
    RUNNING_ZSET_KEY = "task_exec:running"
    EXEC_TTL = 86400
    HEARTBEAT_TTL = 60
    HEARTBEAT_TIMEOUT = 120

    # ===== 新增常量（spec 03 第 3.1/4.1 节）：版本链与回放锁 =====
    VERSIONS_LIST_SUFFIX = ":versions"
    VERSION_INDEX_SUFFIX = ":version"
    REPLAY_LOCK_SUFFIX = ":replay:lock"
    MAX_VERSIONS_PER_STEP = 20  # 单步骤版本链最大长度
    REPLAY_LOCK_TTL = 600       # 回放互斥锁 TTL（秒）

    def __init__(self) -> None:
        """初始化任务检查点存储管理器

        初始化Redis连接、内存存储和降级标志。
        采用延迟初始化策略，Redis连接在首次使用时建立。
        """
        self._redis: Any = None
        self._memory_store: dict[str, str] = {}
        self._memory_session_index: dict[str, str] = {}
        self._use_memory_fallback: bool = False
        # 内存降级模式下的版本链存储：{execution_id: [StepCheckpoint, ...]}
        self._memory_versions: dict[str, list[StepCheckpoint]] = {}
        # 内存降级模式下的回放锁：{execution_id: lock_holder}
        self._memory_replay_locks: dict[str, str] = {}

    async def _get_redis(self) -> Any:
        """获取Redis客户端连接
        
        实现延迟初始化和自动降级机制：
            1. 如果已启用内存降级模式，直接返回None
            2. 如果已有连接，通过ping检查连接是否有效
            3. 如果连接无效或不存在，尝试建立新连接
            4. 如果建立连接失败，启用内存降级模式
        
        Returns:
            Redis客户端实例，或None（使用内存存储）
        
        设计原理：
            延迟初始化避免启动时依赖Redis服务，
            自动降级确保系统在Redis故障时仍可运行，
            连接检查防止使用已断开的连接。
        """
        if self._use_memory_fallback:
            return None
        if self._redis is not None:
            try:
                await self._redis.ping()
                return self._redis
            except Exception:
                self._use_memory_fallback = True
                return None
        try:
            from agent.core.infrastructure.redis_manager import get_redis_client
            redis = await get_redis_client()
            if redis is None:
                self._use_memory_fallback = True
                return None
            self._redis = redis
            return self._redis
        except Exception as e:
            logger.warning("TaskCheckpointStore Redis不可用，启用内存降级: %s", e)
            self._use_memory_fallback = True
            return None

    async def create_execution(self, task: TaskExecution) -> str:
        """创建任务执行记录
        
        将任务执行记录持久化存储，并建立必要的索引。
        这是任务执行的起点，后续所有操作都基于此记录。
        
        存储操作包括：
            1. 存储完整的任务记录（JSON格式）
            2. 建立会话索引（如果提供了session_id）
            3. 将任务添加到运行中任务集合
        
        Args:
            task: 任务执行记录对象，包含任务的所有初始信息
        
        Returns:
            execution_id: 任务执行记录的唯一标识符
        
        设计原理：
            使用Redis的SET命令配合TTL实现自动过期清理，
            ZADD命令将任务加入有序集合便于后续扫描，
            会话索引支持通过会话ID快速查找任务。
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"{self.EXEC_KEY_PREFIX}{task.execution_id}"
                await redis.set(key, task.model_dump_json(), ex=self.EXEC_TTL)
                if task.session_id:
                    session_key = f"{self.SESSION_INDEX_PREFIX}{task.session_id}"
                    await redis.set(session_key, task.execution_id, ex=self.EXEC_TTL)
                await redis.zadd(self.RUNNING_ZSET_KEY, {task.execution_id: task.heartbeat_at})
                logger.info("任务执行记录已创建: execution_id=%s session=%s", task.execution_id, task.session_id)
            except Exception as e:
                logger.warning("任务执行记录存储失败: %s", e)
        else:
            self._memory_store[task.execution_id] = task.model_dump_json()
            if task.session_id:
                self._memory_session_index[task.session_id] = task.execution_id

        return task.execution_id

    async def get_execution(self, execution_id: str) -> TaskExecution | None:
        """获取任务执行记录
        
        根据execution_id从存储中读取任务执行记录。
        支持Redis和内存两种存储后端。
        
        Args:
            execution_id: 任务执行记录的唯一标识符
        
        Returns:
            TaskExecution对象，如果不存在则返回None
        
        设计原理：
            使用Redis的GET命令读取数据，
            Pydantic的model_validate_json方法反序列化JSON，
            确保数据类型正确和验证通过。
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"{self.EXEC_KEY_PREFIX}{execution_id}"
                data = await redis.get(key)
                if data is None:
                    return None
                return TaskExecution.model_validate_json(data)
            except Exception as e:
                logger.warning("获取任务执行记录失败: %s", e)
                return None
        else:
            data = self._memory_store.get(execution_id)
            if data is None:
                return None
            return TaskExecution.model_validate_json(data)

    async def get_execution_by_session(self, session_id: str) -> TaskExecution | None:
        """通过会话ID获取任务执行记录
        
        先通过会话索引查找execution_id，再获取完整记录。
        这是一种间接查询方式，适用于需要根据会话上下文
        查找任务的场景。
        
        Args:
            session_id: 会话ID
        
        Returns:
            TaskExecution对象，如果不存在则返回None
        
        设计原理：
            两步查询：先查索引获取ID，再查主记录获取详情。
            这种设计减少了数据冗余，同时支持高效的会话查询。
        """
        redis = await self._get_redis()
        execution_id = None
        if redis is not None:
            try:
                session_key = f"{self.SESSION_INDEX_PREFIX}{session_id}"
                execution_id = await redis.get(session_key)
            except Exception:
                return None
        else:
            execution_id = self._memory_session_index.get(session_id)

        if not execution_id:
            return None
        return await self.get_execution(execution_id)

    async def update_execution(self, task: TaskExecution) -> None:
        """更新任务执行记录
        
        将修改后的任务记录写回存储，覆盖原有记录。
        自动更新updated_at时间戳，记录最后修改时间。
        
        Args:
            task: 更新后的TaskExecution对象
        
        设计原理：
            使用Redis的SET命令覆盖写入，保持相同的TTL。
            更新操作不改变过期时间，确保任务记录在
            预期的时间窗口内自动清理。
        """
        task.updated_at = time.time()
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"{self.EXEC_KEY_PREFIX}{task.execution_id}"
                await redis.set(key, task.model_dump_json(), ex=self.EXEC_TTL)
            except Exception as e:
                logger.warning("更新任务执行记录失败: %s", e)
        else:
            self._memory_store[task.execution_id] = task.model_dump_json()

    async def save_checkpoint(
        self,
        execution_id: str,
        checkpoint: StepCheckpoint,
        overwrite: bool = False,
    ) -> str:
        """保存步骤检查点（改造后支持 append-only 版本链）

        在保留原覆盖式逻辑的前提下，新增 overwrite 参数：
            - overwrite=True：使用原覆盖式逻辑（兼容旧调用方显式指定）
            - overwrite=False（默认）：采用 append-only 版本链存储

        Args:
            execution_id: 任务执行记录ID
            checkpoint: 步骤检查点对象
            overwrite: 是否使用覆盖式存储，默认 False 使用 append-only

        Returns:
            版本号字符串（append-only 模式）或空字符串（overwrite 模式）

        存储流程（append-only 模式）：
            1. 为 checkpoint 分配版本号（同 step_index 递增）
            2. RPUSH 到 task_exec:{id}:versions 列表
            3. SET task_exec:{id}:version:{step_index}:{version} 索引
            4. 更新 task_exec:{id} 的 current_step 和 updated_at
            5. 触发版本链长度检查，超限淘汰最旧版本
        """
        task = await self.get_execution(execution_id)
        if task is None:
            logger.warning("保存检查点失败: 任务 %s 不存在", execution_id)
            return ""

        if overwrite:
            # 原覆盖式逻辑（保留，向后兼容）
            task.checkpoints = [c for c in task.checkpoints if c.step_index != checkpoint.step_index]
            task.checkpoints.append(checkpoint)
            task.current_step = checkpoint.step_index + 1
            task.updated_at = time.time()
            await self.update_execution(task)
            logger.info(
                "检查点已保存(覆盖式): execution=%s step=%d/%d status=%s",
                execution_id, checkpoint.step_index + 1, len(task.steps), checkpoint.status.value,
            )
            return ""

        # append-only 版本链模式（spec 03 第 5.1 节）
        version = await self._append_version(execution_id, checkpoint)
        task.current_step = checkpoint.step_index + 1
        task.updated_at = time.time()
        # 同步更新 checkpoints 字段（向后兼容读取场景，保留最新版本）
        task.checkpoints = [c for c in task.checkpoints if c.step_index != checkpoint.step_index]
        task.checkpoints.append(checkpoint)
        await self.update_execution(task)

        # 版本链长度检查，超限淘汰最旧版本
        await self._evict_oldest_version(execution_id, checkpoint.step_index)

        logger.info(
            "检查点已保存(append-only): execution=%s step=%d version=%d status=%s",
            execution_id, checkpoint.step_index, version, checkpoint.status.value,
        )
        return str(version)

    async def update_step_status(
        self,
        execution_id: str,
        step_index: int,
        status: StepStatus,
        error: str = "",
        output_data: dict[str, Any] | None = None,
    ) -> None:
        """更新步骤状态
        
        更新指定步骤检查点的状态、错误信息和输出数据。
        这是一个部分更新操作，只修改指定字段。
        
        Args:
            execution_id: 任务执行记录ID
            step_index: 要更新的步骤索引
            status: 新的步骤状态
            error: 错误信息（可选）
            output_data: 输出数据（可选）
        
        设计原理：
            遍历检查点列表找到目标步骤，更新相关字段。
            使用条件判断确保只在有值时才更新字段，
            避免覆盖已有的数据。
        """
        task = await self.get_execution(execution_id)
        if task is None:
            return

        for checkpoint in task.checkpoints:
            if checkpoint.step_index == step_index:
                checkpoint.status = status
                if error:
                    checkpoint.error = error
                if output_data is not None:
                    checkpoint.output_data = output_data
                break

        await self.update_execution(task)

    async def update_heartbeat(self, execution_id: str) -> None:
        """更新任务心跳
        
        更新任务的心跳时间戳，用于检测任务是否仍在运行。
        心跳机制是任务存活检测的核心，定期更新心跳可以
        防止任务被误判为中断。
        
        Args:
            execution_id: 任务执行记录ID
        
        心跳更新流程：
            1. 记录当前时间戳
            2. 更新Redis中的心跳记录（独立key，短TTL）
            3. 更新运行中任务集合的score
            4. 更新任务记录中的heartbeat_at字段
        
        设计原理：
            使用独立的心跳key（短TTL）实现快速存活检测，
            ZSET的score更新支持按时间排序扫描，
            任务记录中的heartbeat_at作为备份和查询字段。
            三重更新确保心跳信息的一致性和可靠性。
        """
        now = time.time()
        redis = await self._get_redis()
        if redis is not None:
            try:
                hb_key = f"{self.HEARTBEAT_KEY_PREFIX}{execution_id}"
                await redis.set(hb_key, str(now), ex=self.HEARTBEAT_TTL)
                await redis.zadd(self.RUNNING_ZSET_KEY, {execution_id: now})
            except Exception as e:
                logger.debug("操作失败，已忽略: %s", e)

        task = await self.get_execution(execution_id)
        if task is not None:
            task.heartbeat_at = now
            await self.update_execution(task)

    async def mark_interrupted(self, execution_id: str) -> None:
        """将任务标记为中断状态
        
        当检测到任务心跳超时时调用，将任务状态更新为INTERRUPTED。
        这是任务异常处理的关键环节，标记后的任务可以被恢复或清理。
        
        Args:
            execution_id: 任务执行记录ID
        
        标记条件：
            只有状态为RUNNING的任务才会被标记为INTERRUPTED，
            避免重复标记已完成或已失败的任务。
        
        设计原理：
            中断标记记录了错误原因和当前步骤，
            便于后续分析中断原因和恢复执行。
            只标记RUNNING状态的任务，防止状态混乱。
        """
        task = await self.get_execution(execution_id)
        if task is None:
            return

        if task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.INTERRUPTED
            task.error = "任务执行中断（心跳超时）"
            task.updated_at = time.time()
            await self.update_execution(task)
            logger.info("任务已标记为中断: execution_id=%s current_step=%d", execution_id, task.current_step)

    async def scan_interrupted_tasks(self) -> list[TaskExecution]:
        """扫描中断的任务
        
        检查所有标记为running的任务，将心跳超时的任务标记为interrupted。
        这是后台定时任务调用的方法，用于清理僵尸任务。
        
        Returns:
            中断的任务列表
        
        扫描逻辑：
            1. 获取运行中任务集合的所有成员
            2. 逐个检查任务状态和心跳时间
            3. 心跳超时的任务标记为INTERRUPTED
            4. 收集所有中断的任务返回
        
        设计原理：
            使用ZSET存储运行中任务，支持高效的范围查询。
            按心跳时间排序，可以优先处理长时间未更新的任务。
            扫描过程不影响正常执行的任务，只标记真正超时的任务。
        """
        interrupted: list[TaskExecution] = []
        now = time.time()
        redis = await self._get_redis()

        if redis is not None:
            try:
                running_ids = await redis.zrange(self.RUNNING_ZSET_KEY, 0, -1)
                for eid in running_ids:
                    task = await self.get_execution(eid)
                    if task and task.status == TaskStatus.RUNNING:
                        if now - task.heartbeat_at > self.HEARTBEAT_TIMEOUT:
                            await self.mark_interrupted(eid)
                            interrupted.append(task)
            except Exception as e:
                logger.warning("扫描中断任务失败: %s", e)
        else:
            for eid, data in self._memory_store.items():
                try:
                    task = TaskExecution.model_validate_json(data)
                    if task.status == TaskStatus.RUNNING and now - task.heartbeat_at > self.HEARTBEAT_TIMEOUT:
                        await self.mark_interrupted(eid)
                        interrupted.append(task)
                except Exception as e:
                    logger.debug("操作失败，已忽略: %s", e)

        return interrupted

    async def remove_from_running(self, execution_id: str) -> None:
        """从运行中任务集合移除
        
        任务完成、失败或取消时调用，清理运行状态标记。
        这是任务生命周期的终点操作之一。
        
        Args:
            execution_id: 任务执行记录ID
        
        清理操作：
            1. 从运行中任务集合（ZSET）中移除
            2. 删除心跳记录（独立key）
        
        设计原理：
            及时清理运行状态，避免干扰后续的扫描操作。
            心跳记录独立存储，需要单独删除。
            任务记录本身保留（有TTL自动过期），用于历史查询。
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.zrem(self.RUNNING_ZSET_KEY, execution_id)
                hb_key = f"{self.HEARTBEAT_KEY_PREFIX}{execution_id}"
                await redis.delete(hb_key)
            except Exception as e:
                logger.debug("操作失败，已忽略: %s", e)

    # ==================== 新增方法（spec 03）：版本链与回放 ====================

    async def _append_version(
        self,
        execution_id: str,
        checkpoint: StepCheckpoint,
    ) -> int:
        """追加版本到版本链，返回分配的版本号

        版本号分配规则：同 step_index 的版本号从 0 递增。
        不同 step_index 的版本号独立计数。

        Returns:
            分配的版本号
        """
        # 计算下一个版本号：同 step_index 的最大版本号 + 1（淘汰后版本号不连续，避免冲突）
        existing = await self.list_step_versions(execution_id, checkpoint.step_index)
        if existing:
            next_version = max(c.version for c in existing) + 1
        else:
            next_version = 0
        checkpoint.version = next_version

        redis = await self._get_redis()
        if redis is not None:
            try:
                versions_key = f"{self.EXEC_KEY_PREFIX}{execution_id}{self.VERSIONS_LIST_SUFFIX}"
                index_key = (
                    f"{self.EXEC_KEY_PREFIX}{execution_id}{self.VERSION_INDEX_SUFFIX}"
                    f":{checkpoint.step_index}:{next_version}"
                )
                # 管道批量执行：RPUSH 版本链 + SET 版本索引
                pipe = redis.pipeline()
                pipe.rpush(versions_key, checkpoint.model_dump_json())
                pipe.expire(versions_key, self.EXEC_TTL)
                pipe.set(index_key, checkpoint.model_dump_json(), ex=self.EXEC_TTL)
                await pipe.execute()
            except Exception as e:
                logger.warning("版本链追加失败(execution=%s): %s", execution_id, e)
        else:
            # 内存降级模式
            if execution_id not in self._memory_versions:
                self._memory_versions[execution_id] = []
            self._memory_versions[execution_id].append(checkpoint)

        return next_version

    async def _evict_oldest_version(
        self,
        execution_id: str,
        step_index: int,
    ) -> None:
        """淘汰指定步骤的最旧版本

        当某步骤的版本数超过 MAX_VERSIONS_PER_STEP 时，
        删除最旧版本（版本号最小的）以控制存储空间。
        """
        versions = await self.list_step_versions(execution_id, step_index)
        if len(versions) <= self.MAX_VERSIONS_PER_STEP:
            return

        # 需要淘汰的数量
        evict_count = len(versions) - self.MAX_VERSIONS_PER_STEP

        redis = await self._get_redis()
        if redis is not None:
            try:
                versions_key = f"{self.EXEC_KEY_PREFIX}{execution_id}{self.VERSIONS_LIST_SUFFIX}"
                for i in range(evict_count):
                    evict_version = versions[i].version
                    index_key = (
                        f"{self.EXEC_KEY_PREFIX}{execution_id}{self.VERSION_INDEX_SUFFIX}"
                        f":{step_index}:{evict_version}"
                    )
                    pipe = redis.pipeline()
                    pipe.lrem(versions_key, 1, versions[i].model_dump_json())
                    pipe.delete(index_key)
                    await pipe.execute()
                    logger.info(
                        "版本链淘汰: execution=%s step=%d version=%d",
                        execution_id, step_index, evict_version,
                    )
            except Exception as e:
                logger.warning("版本链淘汰失败(execution=%s): %s", execution_id, e)
        else:
            # 内存降级模式：按 version 升序排序，淘汰最旧的 evict_count 个版本
            if execution_id in self._memory_versions:
                step_versions = sorted(
                    [c for c in self._memory_versions[execution_id] if c.step_index == step_index],
                    key=lambda c: c.version,
                )
                evict_versions = {c.version for c in step_versions[:evict_count]}
                self._memory_versions[execution_id] = [
                    c for c in self._memory_versions[execution_id]
                    if not (c.step_index == step_index and c.version in evict_versions)
                ]

    async def list_step_versions(
        self,
        execution_id: str,
        step_index: int,
    ) -> list[StepCheckpoint]:
        """列出指定步骤的所有历史版本（时间旅行基础）

        从版本链中筛选出指定 step_index 的全部版本，按版本号升序返回。

        Args:
            execution_id: 任务执行记录ID
            step_index: 步骤索引

        Returns:
            该步骤的全部历史版本列表，按版本号升序排列
            若任务不存在或无版本记录，返回空列表
        """
        redis = await self._get_redis()
        if redis is not None:
            try:
                versions_key = f"{self.EXEC_KEY_PREFIX}{execution_id}{self.VERSIONS_LIST_SUFFIX}"
                raw_list = await redis.lrange(versions_key, 0, -1)
                if not raw_list:
                    return []
                checkpoints: list[StepCheckpoint] = []
                for raw in raw_list:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        cp = StepCheckpoint.model_validate_json(raw)
                        if cp.step_index == step_index:
                            checkpoints.append(cp)
                    except Exception:
                        continue
                checkpoints.sort(key=lambda c: c.version)
                return checkpoints
            except Exception as e:
                logger.warning("版本链查询失败(execution=%s): %s", execution_id, e)
                return []
        else:
            # 内存降级模式
            versions = self._memory_versions.get(execution_id, [])
            checkpoints = [c for c in versions if c.step_index == step_index]
            checkpoints.sort(key=lambda c: c.version)
            return checkpoints

    async def get_state_at_step(
        self,
        execution_id: str,
        step_index: int,
        version: int | None = None,
    ) -> TaskExecution:
        """获取任务在指定步骤/版本时的完整状态快照

        重建任务在指定步骤执行完毕后的完整状态，用于时间旅行和回放。

        Args:
            execution_id: 任务执行记录ID
            step_index: 目标步骤索引
            version: 指定版本号，None 表示该步骤的最新版本

        Returns:
            重建后的 TaskExecution 对象

        Raises:
            ValueError: 任务不存在、步骤索引越界、版本号不存在时抛出
        """
        task = await self.get_execution(execution_id)
        if task is None:
            raise ValueError(f"任务不存在: {execution_id}")

        # 步骤索引越界检查（基于 task.steps 长度）
        if task.steps and step_index >= len(task.steps):
            raise ValueError(
                f"步骤索引越界: step_index={step_index}, max={len(task.steps) - 1}"
            )
        if step_index < 0:
            raise ValueError(f"步骤索引越界: step_index={step_index}")

        # 读取版本链全部版本
        redis = await self._get_redis()
        all_versions: list[StepCheckpoint] = []
        if redis is not None:
            try:
                versions_key = f"{self.EXEC_KEY_PREFIX}{execution_id}{self.VERSIONS_LIST_SUFFIX}"
                raw_list = await redis.lrange(versions_key, 0, -1)
                for raw in raw_list:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        all_versions.append(StepCheckpoint.model_validate_json(raw))
                    except Exception:
                        continue
            except Exception as e:
                logger.warning("状态快照重建读取版本链失败: %s", e)
        else:
            all_versions = list(self._memory_versions.get(execution_id, []))

        # 如果版本链为空，回退到 task.checkpoints（向后兼容无版本链的旧任务）
        if not all_versions and task.checkpoints:
            all_versions = list(task.checkpoints)

        # 筛选 step_index <= 目标步骤的所有版本
        relevant = [c for c in all_versions if c.step_index <= step_index]
        if not relevant:
            raise ValueError(f"步骤索引越界或无版本记录: step_index={step_index}")

        # 每个 step_index 取指定版本（默认最新版本）
        step_indices = sorted({c.step_index for c in relevant})
        selected: list[StepCheckpoint] = []
        for si in step_indices:
            step_versions = [c for c in relevant if c.step_index == si]
            step_versions.sort(key=lambda c: c.version)
            if version is not None and si == step_index:
                # 目标步骤取指定版本
                target = next((c for c in step_versions if c.version == version), None)
                if target is None:
                    raise ValueError(
                        f"版本号不存在: step_index={step_index} version={version}"
                    )
                selected.append(target)
            else:
                # 非目标步骤取最新版本
                selected.append(step_versions[-1])

        # 组装新的 TaskExecution 快照
        snapshot = task.model_copy(deep=True)
        snapshot.checkpoints = selected
        snapshot.current_step = step_index + 1
        snapshot.status = TaskStatus.RUNNING
        return snapshot

    async def _acquire_replay_lock(
        self,
        execution_id: str,
    ) -> bool:
        """获取回放互斥锁

        使用 Redis SET NX EX 实现分布式锁，
        防止同一任务并发回放导致状态污染。

        Returns:
            True 表示获取成功，False 表示已有回放进行中
        """
        lock_holder = f"{uuid.uuid4().hex[:12]}:{time.time()}"
        redis = await self._get_redis()
        if redis is not None:
            try:
                lock_key = f"{self.EXEC_KEY_PREFIX}{execution_id}{self.REPLAY_LOCK_SUFFIX}"
                result = await redis.set(lock_key, lock_holder, ex=self.REPLAY_LOCK_TTL, nx=True)
                if result:
                    return True
                return False
            except Exception as e:
                logger.warning("获取回放锁失败(execution=%s): %s", execution_id, e)
                return False
        else:
            # 内存降级模式
            if execution_id in self._memory_replay_locks:
                return False
            self._memory_replay_locks[execution_id] = lock_holder
            return True

    async def _release_replay_lock(
        self,
        execution_id: str,
    ) -> None:
        """释放回放互斥锁"""
        redis = await self._get_redis()
        if redis is not None:
            try:
                lock_key = f"{self.EXEC_KEY_PREFIX}{execution_id}{self.REPLAY_LOCK_SUFFIX}"
                await redis.delete(lock_key)
            except Exception as e:
                logger.debug("操作失败，已忽略: %s", e)
        else:
            self._memory_replay_locks.pop(execution_id, None)

    async def replay_from_step(
        self,
        execution_id: str,
        from_step: int,
        from_version: int | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        """从指定步骤/版本回放执行

        回放流程：
            1. 获取回放互斥锁（防止并发回放污染）
            2. 重建 from_step 时的状态快照（调用 get_state_at_step）
            3. 应用 overrides（修改输入数据/替换 Agent）
            4. 生成新的 execution_id（不污染原执行记录）
            5. 从 from_step 重新执行后续步骤（使用 Mock 工具客户端）
            6. 将回放轨迹记录到新 execution_id 的版本链
            7. 释放回放互斥锁

        Returns:
            新生成的 execution_id，记录回放轨迹

        Raises:
            ValueError: 任务不存在、步骤越界、版本不存在时抛出
            RuntimeError: 回放互斥锁获取失败（已有回放进行中）时抛出
        """
        # 延迟导入避免循环依赖
        from agent.core.workflow.execution_replayer import get_execution_replayer

        replayer = get_execution_replayer()
        return await replayer.replay(
            execution_id=execution_id,
            from_step=from_step,
            from_version=from_version,
            overrides=overrides,
            checkpoint_store=self,
        )


_task_checkpoint_store: TaskCheckpointStore | None = None


def get_task_checkpoint_store() -> TaskCheckpointStore:
    """获取全局任务检查点存储管理器
    
    实现单例模式，确保整个应用使用同一个存储管理器实例。
    优先从AppContext获取，支持依赖注入和测试替换。
    
    获取顺序：
        1. 尝试从AppContext获取（支持依赖注入）
        2. 如果AppContext未初始化或未设置，使用模块级单例
        3. 如果单例不存在，创建新实例
    
    Returns:
        TaskCheckpointStore实例
    
    设计原理：
        单例模式确保全局唯一实例，避免重复创建连接。
        AppContext优先支持测试时注入Mock对象。
        模块级单例作为后备方案，确保向后兼容。
    """
    global _task_checkpoint_store
    try:
        from agent.core.session.app_context import get_app_context
        ctx = get_app_context()
        if ctx.initialized and ctx.get_task_checkpoint_store() is not None:
            return ctx.get_task_checkpoint_store()
    except Exception as e:
        logger.debug("操作失败，已忽略: %s", e)
    if _task_checkpoint_store is None:
        _task_checkpoint_store = TaskCheckpointStore()
    return _task_checkpoint_store
