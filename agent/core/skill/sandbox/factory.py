"""沙箱后端工厂

根据配置创建对应的沙箱后端实例，支持运行时切换。
通过环境变量 SKILL_SANDBOX_BACKEND 控制使用哪个后端。

后端优先级（自动降级）：
  1. 配置指定的后端
  2. 配置后端不可用时，按优先级尝试降级
     - e2b -> local -> docker
     - docker -> local
     - local -> 无降级（始终可用）

配置方式：
  - 环境变量: SKILL_SANDBOX_BACKEND=local|e2b|docker
  - 代码: SandboxFactory.create("local")
  - 默认: local（本地开发友好）
"""

import logging
from typing import Any

from agent.core.skill.sandbox.base import SandboxBackend, SandboxBackendError

logger = logging.getLogger(__name__)

# 后端名称到类的延迟映射（避免循环导入）
_BACKEND_REGISTRY: dict[str, str] = {
    "local": "agent.core.skill.sandbox.local.LocalSubprocessSandbox",
    "e2b": "agent.core.skill.sandbox.e2b.E2BSandbox",
    "docker": "agent.core.skill.sandbox.docker.DockerSandbox",
}

# 自动降级链：当前后端不可用时，按顺序尝试降级
_FALLBACK_CHAIN: dict[str, list[str]] = {
    "e2b": ["local", "docker"],
    "docker": ["local"],
    "local": [],  # local 始终可用，无需降级
}


def _import_backend_class(class_path: str) -> type:
    """延迟导入后端类

    Args:
        class_path: 完整类路径（如 agent.core.skill.sandbox.local.LocalSubprocessSandbox）

    Returns:
        后端类
    """
    module_path, class_name = class_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class SandboxFactory:
    """沙箱后端工厂

    根据配置创建沙箱后端实例，支持自动降级。
    """

    @staticmethod
    def create(
        backend_name: str | None = None,
        auto_fallback: bool = True,
        **kwargs: Any,
    ) -> SandboxBackend:
        """创建沙箱后端实例

        Args:
            backend_name: 后端名称（local / e2b / docker），None 时从环境变量读取
            auto_fallback: 当指定后端不可用时是否自动降级
            **kwargs: 传递给后端构造函数的参数

        Returns:
            SandboxBackend 实例

        Raises:
            SandboxBackendError: 所有后端均不可用
        """
        import os

        # 确定后端名称
        if backend_name is None:
            backend_name = os.environ.get("SKILL_SANDBOX_BACKEND", "local")

        if backend_name not in _BACKEND_REGISTRY:
            raise SandboxBackendError(
                backend_name,
                f"未知的沙箱后端: {backend_name}，可选值: {list(_BACKEND_REGISTRY.keys())}",
            )

        # 尝试创建指定后端
        backend = SandboxFactory._try_create(backend_name, **kwargs)
        if backend is not None:
            logger.info("沙箱后端已创建: %s", backend_name)
            return backend

        # 指定后端不可用，尝试降级
        if auto_fallback:
            fallback_chain = _FALLBACK_CHAIN.get(backend_name, [])
            for fallback_name in fallback_chain:
                logger.warning(
                    "沙箱后端 '%s' 不可用，尝试降级到 '%s'",
                    backend_name, fallback_name,
                )
                backend = SandboxFactory._try_create(fallback_name, **kwargs)
                if backend is not None:
                    logger.info("沙箱后端已降级到: %s", fallback_name)
                    return backend

        raise SandboxBackendError(
            backend_name,
            "所有沙箱后端均不可用，请检查环境配置",
        )

    @staticmethod
    def _try_create(backend_name: str, **kwargs: Any) -> SandboxBackend | None:
        """尝试创建后端实例并检查可用性

        Args:
            backend_name: 后端名称
            **kwargs: 构造参数

        Returns:
            可用的后端实例，不可用时返回 None
        """
        import asyncio

        class_path = _BACKEND_REGISTRY.get(backend_name)
        if not class_path:
            return None

        try:
            backend_cls = _import_backend_class(class_path)
            backend = backend_cls(**kwargs)

            # 同步检查环境可用性
            try:
                loop = asyncio.get_running_loop()
                # 已有事件循环运行中，创建任务检查
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, backend.check_environment())
                    check_result = future.result(timeout=10)
            except RuntimeError:
                # 没有运行中的事件循环，直接运行
                check_result = asyncio.run(backend.check_environment())

            if check_result.get("available", False):
                return backend
            else:
                logger.warning(
                    "沙箱后端 '%s' 环境检查未通过: %s",
                    backend_name,
                    check_result.get("details", {}),
                )
                return None

        except Exception as e:
            logger.warning("沙箱后端 '%s' 创建失败: %s", backend_name, e)
            return None

    @staticmethod
    def list_backends() -> list[str]:
        """列出所有已注册的后端名称

        Returns:
            后端名称列表
        """
        return list(_BACKEND_REGISTRY.keys())
