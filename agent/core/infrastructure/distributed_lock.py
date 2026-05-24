"""分布式锁服务

基于 Redis 实现的分布式锁，用于防止多个 Agent 同时操作同一业务对象
导致的数据不一致问题。

核心特性：
  - 原子获取锁：使用 Redis SET NX PX 命令保证原子性
  - 安全释放锁：使用 Lua 脚本确保只有锁持有者能释放
  - 自动续期：长时间操作可调用 extend 续期锁
  - 上下文管理器：支持 async with 自动获取/释放
  - 可重试获取：支持配置重试次数和重试间隔

使用方式：
    # 方式1：上下文管理器（推荐）
    async with distributed_lock("lock:approval:AP-001", "session-1:agent-1"):
        # 在锁保护下执行操作
        await approve_request("AP-001")

    # 方式2：手动获取/释放
    lock = DistributedLock("lock:approval:AP-001", "session-1:agent-1")
    acquired = await lock.acquire()
    if acquired:
        try:
            await approve_request("AP-001")
        finally:
            await lock.release()
"""

import asyncio
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Lua 脚本：安全释放锁，只有持有者才能释放
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Lua 脚本：续期锁，只有持有者才能续期
_EXTEND_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""

# 锁 Key 前缀
_LOCK_KEY_PREFIX = "dist_lock:"


class DistributedLock:
    """分布式锁

    基于 Redis 的分布式锁实现，使用 SET NX PX 原子命令获取锁，
    使用 Lua 脚本保证释放和续期的安全性。

    Attributes:
        lock_key: 锁标识，格式 "lock:{resource_type}:{resource_id}"
        holder_id: 持有者标识（session_id + agent_name + request_id）
        ttl_ms: 锁超时时间（毫秒），防止死锁
        retry_count: 获取锁失败时的重试次数
        retry_interval_ms: 重试间隔（毫秒）
    """

    def __init__(
        self,
        lock_key: str,
        holder_id: str | None = None,
        ttl_ms: int = 30000,
        retry_count: int = 3,
        retry_interval_ms: int = 200,
    ) -> None:
        """初始化分布式锁

        Args:
            lock_key: 锁标识，建议格式 "lock:{resource_type}:{resource_id}"
            holder_id: 持有者标识，不传时自动生成
            ttl_ms: 锁超时时间（毫秒），默认30秒
            retry_count: 获取锁重试次数，默认3次
            retry_interval_ms: 重试间隔（毫秒），默认200毫秒
        """
        self.lock_key = lock_key
        self.holder_id = holder_id or f"{uuid.uuid4().hex[:12]}"
        self.ttl_ms = ttl_ms
        self.retry_count = retry_count
        self.retry_interval_ms = retry_interval_ms
        self._acquired_at: float = 0.0
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端"""
        if self._redis is not None:
            return self._redis
        try:
            from agent.core.infrastructure.redis_manager import get_redis_client
            self._redis = await get_redis_client()
            return self._redis
        except Exception as e:
            logger.warning("分布式锁 Redis 连接失败: %s", e)
            return None

    @property
    def _redis_key(self) -> str:
        """Redis 中的锁 Key"""
        if self.lock_key.startswith(_LOCK_KEY_PREFIX):
            return self.lock_key
        return f"{_LOCK_KEY_PREFIX}{self.lock_key}"

    async def acquire(self) -> bool:
        """获取分布式锁

        使用 Redis SET key value NX PX ttl 原子命令获取锁。
        获取失败时按配置的重试次数和间隔进行重试。

        Returns:
            是否成功获取锁
        """
        redis = await self._get_redis()
        if redis is None:
            logger.warning("Redis 不可用，分布式锁获取失败: key=%s", self.lock_key)
            return False

        for attempt in range(self.retry_count + 1):
            try:
                result = await redis.set(
                    self._redis_key,
                    self.holder_id,
                    nx=True,
                    px=self.ttl_ms,
                )
                if result:
                    self._acquired_at = time.time()
                    logger.debug(
                        "分布式锁获取成功: key=%s holder=%s",
                        self.lock_key, self.holder_id,
                    )
                    return True

                if attempt < self.retry_count:
                    logger.debug(
                        "分布式锁获取重试(%d/%d): key=%s",
                        attempt + 1, self.retry_count, self.lock_key,
                    )
                    await asyncio.sleep(self.retry_interval_ms / 1000.0)

            except Exception as e:
                logger.warning("分布式锁获取异常: key=%s error=%s", self.lock_key, e)
                if attempt < self.retry_count:
                    await asyncio.sleep(self.retry_interval_ms / 1000.0)

        logger.warning(
            "分布式锁获取失败（已重试%d次）: key=%s holder=%s",
            self.retry_count, self.lock_key, self.holder_id,
        )
        return False

    async def release(self) -> bool:
        """释放分布式锁

        使用 Lua 脚本确保只有锁持有者才能释放，防止误删其他持有者的锁。

        Returns:
            是否成功释放锁
        """
        redis = await self._get_redis()
        if redis is None:
            return False

        try:
            result = await redis.eval(
                _RELEASE_LOCK_LUA,
                1,
                self._redis_key,
                self.holder_id,
            )
            released = result == 1
            if released:
                logger.debug(
                    "分布式锁释放成功: key=%s holder=%s",
                    self.lock_key, self.holder_id,
                )
            else:
                logger.warning(
                    "分布式锁释放失败（非持有者或已过期）: key=%s holder=%s",
                    self.lock_key, self.holder_id,
                )
            return released
        except Exception as e:
            logger.warning("分布式锁释放异常: key=%s error=%s", self.lock_key, e)
            return False

    async def extend(self, ttl_ms: int | None = None) -> bool:
        """续期分布式锁

        使用 Lua 脚本确保只有锁持有者才能续期。
        适用于长时间操作，需要延长锁的持有时间。

        Args:
            ttl_ms: 新的超时时间（毫秒），不传时使用初始化时的 ttl_ms

        Returns:
            是否成功续期
        """
        redis = await self._get_redis()
        if redis is None:
            return False

        new_ttl = ttl_ms or self.ttl_ms
        try:
            result = await redis.eval(
                _EXTEND_LOCK_LUA,
                1,
                self._redis_key,
                self.holder_id,
                new_ttl,
            )
            extended = result == 1
            if extended:
                logger.debug(
                    "分布式锁续期成功: key=%s holder=%s ttl=%dms",
                    self.lock_key, self.holder_id, new_ttl,
                )
            else:
                logger.warning(
                    "分布式锁续期失败（非持有者或已过期）: key=%s holder=%s",
                    self.lock_key, self.holder_id,
                )
            return extended
        except Exception as e:
            logger.warning("分布式锁续期异常: key=%s error=%s", self.lock_key, e)
            return False

    async def is_locked(self) -> bool:
        """检查锁是否被持有

        Returns:
            锁是否被任何持有者持有
        """
        redis = await self._get_redis()
        if redis is None:
            return False

        try:
            return await redis.exists(self._redis_key) == 1
        except Exception:
            return False

    async def get_holder(self) -> str | None:
        """获取当前锁的持有者ID

        Returns:
            持有者ID，锁不存在时返回 None
        """
        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            return await redis.get(self._redis_key)
        except Exception:
            return None


@asynccontextmanager
async def distributed_lock(
    lock_key: str,
    holder_id: str | None = None,
    ttl_ms: int = 30000,
    retry_count: int = 3,
    retry_interval_ms: int = 200,
):
    """分布式锁上下文管理器

    使用 async with 自动获取和释放锁，确保锁一定会被释放。

    Args:
        lock_key: 锁标识
        holder_id: 持有者标识
        ttl_ms: 锁超时时间（毫秒）
        retry_count: 获取锁重试次数
        retry_interval_ms: 重试间隔（毫秒）

    Yields:
        DistributedLock 实例

    Raises:
        DistributedLockError: 获取锁失败时抛出

    使用示例：
        async with distributed_lock("lock:approval:AP-001", "session-1") as lock:
            await approve_request("AP-001")
    """
    lock = DistributedLock(
        lock_key=lock_key,
        holder_id=holder_id,
        ttl_ms=ttl_ms,
        retry_count=retry_count,
        retry_interval_ms=retry_interval_ms,
    )

    acquired = await lock.acquire()
    if not acquired:
        raise DistributedLockError(
            f"获取分布式锁失败: key={lock_key}, holder={lock.holder_id}"
        )

    try:
        yield lock
    finally:
        await lock.release()


class DistributedLockError(Exception):
    """分布式锁异常"""


def build_lock_key(resource_type: str, resource_id: str) -> str:
    """构建标准化的锁 Key

    Args:
        resource_type: 资源类型（如 approval、email、calendar）
        resource_id: 资源标识（如审批单号、邮件ID、时间段）

    Returns:
        标准化的锁 Key，格式 "lock:{resource_type}:{resource_id}"
    """
    return f"lock:{resource_type}:{resource_id}"


def build_holder_id(session_id: str, agent_name: str, request_id: str = "") -> str:
    """构建标准化的持有者标识

    Args:
        session_id: 会话ID
        agent_name: Agent 名称
        request_id: 请求ID（可选）

    Returns:
        标准化的持有者标识
    """
    parts = [session_id, agent_name]
    if request_id:
        parts.append(request_id)
    return ":".join(parts)


def extract_resource_key(
    tool_name: str,
    tool_input: dict[str, Any],
    resource_key_mapping: dict[str, str] | None = None,
) -> str | None:
    """从工具调用参数中提取资源标识，构造锁 Key

    根据 TOOL_RESOURCE_KEY_MAPPING 配置从 tool_input 中提取资源ID，
    若未找到映射则使用 tool_name + hash(tool_input) 作为兜底。

    Args:
        tool_name: 工具名称，格式 "资源:操作"
        tool_input: 工具输入参数
        resource_key_mapping: 工具参数到资源标识的映射配置

    Returns:
        锁 Key，无法提取时返回 None
    """
    if not tool_input:
        return None

    mapping = resource_key_mapping or {}

    # 优先从映射配置中查找
    param_key = mapping.get(tool_name)
    if param_key and param_key in tool_input:
        resource_id = str(tool_input[param_key])
        resource_type = tool_name.split(":")[0] if ":" in tool_name else tool_name
        return build_lock_key(resource_type, resource_id)

    # 兜底：使用 tool_name + hash(tool_input) 作为锁 Key
    # 仅对写操作生成锁 Key
    write_actions = {"send", "approve", "reject", "delete", "update", "create", "cancel", "modify"}
    action = tool_name.split(":")[-1] if ":" in tool_name else ""
    if action.lower() in write_actions:
        input_hash = hashlib.md5(str(sorted(tool_input.items())).encode()).hexdigest()[:12]
        resource_type = tool_name.split(":")[0] if ":" in tool_name else tool_name
        return build_lock_key(resource_type, f"hash:{input_hash}")

    return None
