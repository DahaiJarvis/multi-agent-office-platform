"""用户凭证存储

提供用户凭证的持久化存储和查询能力。
优先使用 PostgreSQL 数据库存储，数据库不可用时降级到内存存储。

数据模型:
  - user_id: 用户唯一标识
  - password_hash: bcrypt 密码哈希
  - roles: 角色列表
  - departments: 部门列表
  - is_active: 是否启用
  - created_at: 创建时间
  - updated_at: 更新时间

使用示例:
  store = get_user_store()
  user = await store.get_user("admin001")
  await store.verify_password("admin001", "admin123")
  await store.create_user("new_user", "password", ["employee"])
"""

import logging
import time
from typing import Any

from api.routes.auth_routes import _hash_password

logger = logging.getLogger(__name__)


class UserRecord:
    """用户记录"""

    __slots__ = ("user_id", "password_hash", "roles", "departments", "is_active", "created_at", "updated_at")

    def __init__(
        self,
        user_id: str,
        password_hash: str,
        roles: list[str] | None = None,
        departments: list[str] | None = None,
        is_active: bool = True,
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> None:
        self.user_id = user_id
        self.password_hash = password_hash
        self.roles = roles or ["employee"]
        self.departments = departments or []
        self.is_active = is_active
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "user_id": self.user_id,
            "password_hash": self.password_hash,
            "roles": self.roles,
            "departments": self.departments,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class UserStore:
    """用户凭证存储

    双层存储策略:
    - L1: 进程内字典缓存，毫秒级读取
    - L2: PostgreSQL 持久化，数据持久可靠

    写入时同时写入 L1 和 L2，读取时优先 L1，未命中查 L2。
    """

    def __init__(self) -> None:
        self._cache: dict[str, UserRecord] = {}
        self._initialized: bool = False
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        """获取数据库连接池"""
        if self._pool is not None:
            return self._pool

        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from agent.core.config import get_settings

            settings = get_settings()
            engine = create_async_engine(
                settings.postgres_dsn,
                pool_size=3,
                max_overflow=5,
                pool_pre_ping=True,
            )
            self._pool = async_sessionmaker(engine, expire_on_commit=False)
            return self._pool
        except Exception as e:
            logger.warning("用户存储数据库连接失败: %s", e)
            return None

    async def _init_tables(self) -> None:
        """初始化数据库表结构"""
        pool = await self._get_pool()
        if pool is None:
            return

        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
            from agent.core.config import get_settings

            settings = get_settings()
            engine = create_async_engine(settings.postgres_dsn, pool_size=1, max_overflow=2)

            async with engine.begin() as conn:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id VARCHAR(64) PRIMARY KEY,
                        password_hash VARCHAR(128) NOT NULL,
                        roles JSONB DEFAULT '["employee"]',
                        departments JSONB DEFAULT '[]',
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """))

            await engine.dispose()
            logger.info("用户存储表结构初始化完成")
        except Exception as e:
            logger.warning("用户存储表初始化失败: %s", e)

    async def initialize(self) -> None:
        """初始化用户存储

        1. 初始化数据库表
        2. 从数据库加载用户到缓存
        3. 如果数据库为空，写入默认用户
        """
        if self._initialized:
            return

        await self._init_tables()
        await self._load_from_db()

        # 如果数据库为空，写入默认用户
        if not self._cache:
            await self._seed_default_users()

        self._initialized = True
        logger.info("用户存储初始化完成，共 %d 个用户", len(self._cache))

    async def _load_from_db(self) -> None:
        """从数据库加载所有用户到缓存"""
        pool = await self._get_pool()
        if pool is None:
            return

        try:
            from sqlalchemy import text
            import json

            async with pool() as session:
                result = await session.execute(
                    text("SELECT user_id, password_hash, roles, departments, is_active, created_at, updated_at FROM users WHERE is_active = TRUE")
                )
                for row in result.mappings().all():
                    roles = row["roles"] if isinstance(row["roles"], list) else json.loads(row["roles"])
                    departments = row["departments"] if isinstance(row["departments"], list) else json.loads(row["departments"])
                    self._cache[row["user_id"]] = UserRecord(
                        user_id=row["user_id"],
                        password_hash=row["password_hash"],
                        roles=roles,
                        departments=departments,
                        is_active=row["is_active"],
                        created_at=row["created_at"].timestamp() if hasattr(row["created_at"], "timestamp") else time.time(),
                        updated_at=row["updated_at"].timestamp() if hasattr(row["updated_at"], "timestamp") else time.time(),
                    )
        except Exception as e:
            logger.warning("从数据库加载用户失败: %s", e)

    async def _seed_default_users(self) -> None:
        """写入默认用户到数据库"""
        default_users = [
            ("admin001", "admin123", ["admin"], ["技术部"]),
            ("mgr001", "mgr123", ["manager"], ["产品部"]),
            ("hr001", "hr123", ["hr_specialist"], ["人力资源部"]),
            ("fin001", "fin123", ["finance"], ["财务部"]),
            ("emp001", "emp123", ["employee"], ["运营部"]),
        ]

        for user_id, password, roles, departments in default_users:
            await self.create_user(user_id, password, roles, departments)

        logger.info("默认用户已写入数据库，共 %d 个", len(default_users))

    async def get_user(self, user_id: str) -> UserRecord | None:
        """获取用户记录

        优先从缓存读取，未命中则查询数据库。

        Args:
            user_id: 用户ID

        Returns:
            UserRecord 或 None
        """
        # L1 缓存
        if user_id in self._cache:
            return self._cache[user_id]

        # L2 数据库
        pool = await self._get_pool()
        if pool is None:
            return None

        try:
            from sqlalchemy import text
            import json

            async with pool() as session:
                result = await session.execute(
                    text("SELECT user_id, password_hash, roles, departments, is_active, created_at, updated_at FROM users WHERE user_id = :uid AND is_active = TRUE"),
                    {"uid": user_id},
                )
                row = result.mappings().first()
                if row is None:
                    return None

                roles = row["roles"] if isinstance(row["roles"], list) else json.loads(row["roles"])
                departments = row["departments"] if isinstance(row["departments"], list) else json.loads(row["departments"])
                record = UserRecord(
                    user_id=row["user_id"],
                    password_hash=row["password_hash"],
                    roles=roles,
                    departments=departments,
                    is_active=row["is_active"],
                )
                self._cache[user_id] = record
                return record
        except Exception as e:
            logger.error("查询用户失败: user_id=%s error=%s", user_id, e)
            return None

    async def create_user(
        self,
        user_id: str,
        password: str,
        roles: list[str] | None = None,
        departments: list[str] | None = None,
    ) -> bool:
        """创建用户

        将用户凭证写入数据库和缓存。密码使用 bcrypt 哈希存储。

        Args:
            user_id: 用户ID
            password: 明文密码
            roles: 角色列表
            departments: 部门列表

        Returns:
            是否创建成功
        """
        import json

        password_hash = _hash_password(password)
        record = UserRecord(
            user_id=user_id,
            password_hash=password_hash,
            roles=roles or ["employee"],
            departments=departments or [],
        )

        # 写入数据库
        pool = await self._get_pool()
        if pool is not None:
            try:
                from sqlalchemy import text

                async with pool() as session:
                    await session.execute(
                        text("""
                            INSERT INTO users (user_id, password_hash, roles, departments, is_active, created_at, updated_at)
                            VALUES (:uid, :ph, :roles, :depts, TRUE, NOW(), NOW())
                            ON CONFLICT (user_id) DO UPDATE SET
                                password_hash = :ph,
                                roles = :roles,
                                departments = :depts,
                                updated_at = NOW()
                        """),
                        {
                            "uid": user_id,
                            "ph": password_hash,
                            "roles": json.dumps(roles or ["employee"], ensure_ascii=False),
                            "depts": json.dumps(departments or [], ensure_ascii=False),
                        },
                    )
                    await session.commit()
            except Exception as e:
                logger.error("创建用户到数据库失败: user_id=%s error=%s", user_id, e)

        # 更新缓存
        self._cache[user_id] = record
        return True

    async def update_password(self, user_id: str, new_password: str) -> bool:
        """更新用户密码

        Args:
            user_id: 用户ID
            new_password: 新密码明文

        Returns:
            是否更新成功
        """
        new_hash = _hash_password(new_password)

        pool = await self._get_pool()
        if pool is not None:
            try:
                from sqlalchemy import text

                async with pool() as session:
                    await session.execute(
                        text("UPDATE users SET password_hash = :ph, updated_at = NOW() WHERE user_id = :uid"),
                        {"uid": user_id, "ph": new_hash},
                    )
                    await session.commit()
            except Exception as e:
                logger.error("更新用户密码失败: user_id=%s error=%s", user_id, e)
                return False

        # 更新缓存
        if user_id in self._cache:
            self._cache[user_id].password_hash = new_hash
            self._cache[user_id].updated_at = time.time()

        return True

    async def update_password_hash(self, user_id: str, new_hash: str) -> None:
        """更新用户密码哈希（用于 SHA-256 到 bcrypt 自动升级）

        Args:
            user_id: 用户ID
            new_hash: 新的 bcrypt 哈希值
        """
        pool = await self._get_pool()
        if pool is not None:
            try:
                from sqlalchemy import text

                async with pool() as session:
                    await session.execute(
                        text("UPDATE users SET password_hash = :ph, updated_at = NOW() WHERE user_id = :uid"),
                        {"uid": user_id, "ph": new_hash},
                    )
                    await session.commit()
            except Exception as e:
                logger.warning("更新用户密码哈希到数据库失败: user_id=%s error=%s", user_id, e)

        # 更新缓存
        if user_id in self._cache:
            self._cache[user_id].password_hash = new_hash
            self._cache[user_id].updated_at = time.time()

    async def user_exists(self, user_id: str) -> bool:
        """检查用户是否存在

        Args:
            user_id: 用户ID

        Returns:
            用户是否存在
        """
        if user_id in self._cache:
            return self._cache[user_id].is_active
        return await self.get_user(user_id) is not None

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """列出所有用户

        Args:
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            用户列表
        """
        users = list(self._cache.values())
        result = []
        for u in users[offset : offset + limit]:
            result.append({
                "user_id": u.user_id,
                "roles": u.roles,
                "departments": u.departments,
                "is_active": u.is_active,
            })
        return result


# 全局用户存储实例
_user_store: UserStore | None = None


def get_user_store() -> UserStore:
    """获取全局用户存储实例"""
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
    return _user_store
