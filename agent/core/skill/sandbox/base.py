"""沙箱后端抽象基类

定义沙箱执行的统一接口，所有沙箱后端（LocalSubprocess / E2B / Docker）
必须实现此接口，使 SkillRunner 可通过策略模式切换执行后端。

核心接口：
  - check_environment(): 检查后端环境是否就绪
  - execute(): 在沙箱中执行脚本
  - cleanup(): 清理资源

设计原则：
  - 接口最小化：只定义 SkillRunner 真正需要的方法
  - 异步优先：所有 IO 操作使用 async
  - 结果统一：所有后端返回 ExecutionResult
"""

import abc
from typing import Any

from agent.core.skill.sandbox.models import ExecutionResult, SandboxLevel


class SandboxBackendError(Exception):
    """沙箱后端不可用异常"""

    def __init__(self, backend_name: str, reason: str = "") -> None:
        self.backend_name = backend_name
        self.reason = reason
        msg = f"沙箱后端 '{backend_name}' 不可用"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class SandboxBackend(abc.ABC):
    """沙箱后端抽象基类

    所有沙箱后端必须实现此接口。SkillRunner 通过此接口
    与具体后端解耦，支持运行时切换执行策略。
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """后端名称标识（如 local / e2b / docker）"""
        ...

    @abc.abstractmethod
    async def check_environment(self) -> dict[str, Any]:
        """检查后端执行环境是否就绪

        Returns:
            环境检查结果字典，至少包含:
            - available (bool): 后端是否可用
            - backend (str): 后端名称
            - details (dict): 后端特定的详情
        """
        ...

    @abc.abstractmethod
    async def execute(
        self,
        skill_name: str,
        script_name: str,
        skill_dir: str,
        args: dict[str, Any] | None = None,
        sandbox_level: SandboxLevel = SandboxLevel.L2,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """在沙箱中执行 Skill 脚本

        Args:
            skill_name: Skill 名称
            script_name: 脚本文件名（相对于 scripts/ 目录）
            skill_dir: Skill 目录绝对路径
            args: 脚本参数
            sandbox_level: 沙箱隔离级别
            timeout: 超时时间（秒），None 使用默认值

        Returns:
            ExecutionResult 执行结果

        Raises:
            SandboxBackendError: 后端不可用
            SkillExecutionError: 执行失败
            SkillTimeoutError: 执行超时
        """
        ...

    async def cleanup(self) -> None:
        """清理后端资源（可选实现）

        在后端销毁时调用，用于释放容器、连接等资源。
        默认空实现，有资源需要清理的后端应覆盖此方法。
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name}>"
