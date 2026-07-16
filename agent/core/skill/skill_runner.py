"""Skill 脚本沙箱执行器

通过可插拔的沙箱后端为外部 Skill 的脚本提供安全隔离的执行环境，
防止恶意脚本危害宿主机系统。

核心能力：
  - 可插拔沙箱后端（LocalSubprocess / E2B / Docker）
  - 通过配置或环境变量切换后端
  - 自动降级：指定后端不可用时按优先级降级
  - 资源限制（CPU/内存/超时/网络）
  - 按信任等级分 Level 1/Level 2 沙箱
  - 执行审计日志
  - 自动禁用连续失败的 Skill

沙箱后端对比：
  - local:  本地子进程，轻量快速，适用于开发环境和可信脚本
  - e2b:    Firecracker MicroVM，硬件级隔离，适用于生产环境
  - docker: Docker 容器，兼容方案，隔离性介于 local 和 e2b 之间

使用方式：
  runner = SkillRunner()
  result = await runner.execute(
      skill_name="docx",
      script_name="create_docx.py",
      skill_dir="/path/to/docx",
      args={"title": "测试文档"},
      sandbox_level=SandboxLevel.L1,
  )

配置方式：
  - 环境变量 SKILL_SANDBOX_BACKEND=local|e2b|docker
  - 代码 SkillRunner(backend_name="e2b")
  - 默认: local
"""

import logging
import os
from typing import Any

from agent.core.skill.sandbox.base import SandboxBackend, SandboxBackendError
from agent.core.skill.sandbox.factory import SandboxFactory
from agent.core.skill.sandbox.models import (
    DEFAULT_TIMEOUT,
    RUNNER_IMAGE,
    AUDIT_LOG_DIR,
    DockerNotAvailableError,
    ExecutionResult,
    SandboxLevel,
    SkillExecutionError,
    SkillTimeoutError,
    write_audit_log,
)

logger = logging.getLogger(__name__)

# 容器池大小（保留兼容）
_POOL_SIZE = 2


class SkillRunner:
    """Skill 脚本沙箱执行器

    通过可插拔的沙箱后端为外部 Skill 的脚本提供安全隔离的执行环境。
    支持后端切换、自动降级、资源限制和审计日志。

    使用方式：
        # 默认后端（从环境变量 SKILL_SANDBOX_BACKEND 读取，默认 local）
        runner = SkillRunner()

        # 指定后端
        runner = SkillRunner(backend_name="e2b")

        # 执行脚本
        result = await runner.execute(
            skill_name="docx",
            script_name="create_docx.py",
            skill_dir="/path/to/docx",
            args={"title": "测试文档"},
            sandbox_level=SandboxLevel.L1,
        )
    """

    def __init__(self, backend_name: str | None = None) -> None:
        """初始化 SkillRunner

        Args:
            backend_name: 沙箱后端名称（local / e2b / docker），
                         None 时从环境变量 SKILL_SANDBOX_BACKEND 读取，默认 local
        """
        self._backend_name = backend_name or os.environ.get("SKILL_SANDBOX_BACKEND", "local")
        self._backend: SandboxBackend | None = None
        # 连续异常计数，用于自动禁用
        self._consecutive_errors: dict[str, int] = {}
        # 自动禁用阈值：同一 skill 连续失败 3 次则自动禁用
        self._auto_disable_threshold = 3
        # 已自动禁用的 skill 集合
        self._disabled_skills: set[str] = set()

    def _get_backend(self) -> SandboxBackend:
        """获取沙箱后端实例（延迟初始化）

        Returns:
            SandboxBackend 实例
        """
        if self._backend is None:
            self._backend = SandboxFactory.create(
                backend_name=self._backend_name,
                auto_fallback=True,
            )
            logger.info("SkillRunner 使用沙箱后端: %s", self._backend.name)
        return self._backend

    async def check_environment(self) -> dict[str, Any]:
        """检查执行环境是否就绪

        Returns:
            环境检查结果
        """
        backend = self._get_backend()
        check_result = await backend.check_environment()
        # 补充后端信息
        check_result["backend_name"] = backend.name
        return check_result

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
            args: 脚本参数
            skill_dir: Skill 目录绝对路径
            sandbox_level: 沙箱级别
            timeout: 超时时间（秒），None 使用默认值

        Returns:
            ExecutionResult 执行结果

        Raises:
            DockerNotAvailableError: Docker 后端不可用（保留兼容）
            SkillTimeoutError: 执行超时
            SkillExecutionError: 执行失败
            SandboxBackendError: 沙箱后端不可用
        """
        # 检查是否被自动禁用
        if skill_name in self._disabled_skills:
            return ExecutionResult(
                skill_name=skill_name,
                script_name=script_name,
                exit_code=-1,
                stdout="",
                stderr=f"Skill '{skill_name}' 已被自动禁用（连续执行失败超过阈值），需人工重新启用",
                duration_ms=0,
                sandbox_level=sandbox_level,
            )

        # 获取后端并执行
        backend = self._get_backend()

        try:
            result = await backend.execute(
                skill_name=skill_name,
                script_name=script_name,
                skill_dir=skill_dir,
                args=args,
                sandbox_level=sandbox_level,
                timeout=timeout,
            )

            # 成功时重置错误计数
            if result.success:
                self._consecutive_errors.pop(skill_name, None)
            else:
                self._record_error(skill_name)

            return result

        except SandboxBackendError as e:
            # 后端不可用，保留 DockerNotAvailableError 兼容
            if backend.name == "docker":
                raise DockerNotAvailableError() from e
            raise

        except SkillExecutionError:
            self._record_error(skill_name)
            raise

        except Exception as e:
            self._record_error(skill_name)
            raise SkillExecutionError(skill_name, script_name, str(e)) from e

    def _record_error(self, skill_name: str) -> None:
        """记录执行错误，超过阈值自动禁用

        Args:
            skill_name: Skill 名称
        """
        count = self._consecutive_errors.get(skill_name, 0) + 1
        self._consecutive_errors[skill_name] = count
        if count >= self._auto_disable_threshold:
            self._disabled_skills.add(skill_name)
            logger.warning(
                "Skill '%s' 连续执行失败 %d 次，已自动禁用，需人工重新启用",
                skill_name, count,
            )

    def re_enable_skill(self, skill_name: str) -> bool:
        """重新启用被自动禁用的 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否重新启用成功
        """
        if skill_name in self._disabled_skills:
            self._disabled_skills.discard(skill_name)
            self._consecutive_errors.pop(skill_name, None)
            logger.info("Skill '%s' 已重新启用", skill_name)
            return True
        return False

    def list_disabled_skills(self) -> list[str]:
        """列出被自动禁用的 Skill

        Returns:
            被禁用的 Skill 名称列表
        """
        return list(self._disabled_skills)

    @property
    def backend_name(self) -> str:
        """当前使用的后端名称"""
        backend = self._backend
        return backend.name if backend else self._backend_name


# 全局单例
_skill_runner: SkillRunner | None = None


def get_skill_runner(backend_name: str | None = None) -> SkillRunner:
    """获取全局 SkillRunner 单例

    Args:
        backend_name: 沙箱后端名称，None 时使用默认值

    Returns:
        SkillRunner 实例
    """
    global _skill_runner
    if _skill_runner is None:
        _skill_runner = SkillRunner(backend_name=backend_name)
    return _skill_runner


def reset_skill_runner() -> None:
    """重置全局 SkillRunner 单例

    主要用于测试或切换后端时重置。
    """
    global _skill_runner
    _skill_runner = None
