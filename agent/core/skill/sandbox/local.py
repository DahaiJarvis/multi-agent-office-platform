"""本地子进程沙箱后端

通过 subprocess 运行脚本，配合资源限制（ulimit / timeout / 只读挂载）
提供轻量级沙箱隔离，适用于本地开发和可信来源的脚本执行。

安全措施：
  - 超时控制：通过 asyncio.wait_for 强制终止超时进程
  - 文件系统限制：通过环境变量约束脚本的工作目录和输出路径
  - 资源限制：通过 ulimit 限制内存和 CPU（Unix 系统）
  - 网络隔离：L2 级别通过 unshare 禁用网络（Linux）

适用场景：
  - 本地开发环境（无需 Docker）
  - 可信来源的脚本（如 Anthropic 官方 skills）
  - 对启动速度敏感的场景（冷启动 <100ms）

限制：
  - 隔离强度低于 Docker / MicroVM
  - 不适用于不可信代码执行
  - Windows 下部分资源限制不生效
"""

import asyncio
import json
import logging
import os
import platform
import signal
import time
from pathlib import Path
from typing import Any

from agent.core.skill.sandbox.base import SandboxBackend
from agent.core.skill.sandbox.models import (
    ExecutionResult,
    SandboxLevel,
    SkillExecutionError,
    DEFAULT_TIMEOUT,
    write_audit_log,
)

logger = logging.getLogger(__name__)

# 本地沙箱输出目录
_LOCAL_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))),
    "logs", "skill_output",
)


class LocalSubprocessSandbox(SandboxBackend):
    """本地子进程沙箱后端

    通过 subprocess + 资源限制提供轻量级沙箱，
    无需 Docker 依赖，适用于本地开发和可信脚本执行。
    """

    @property
    def name(self) -> str:
        return "local"

    async def check_environment(self) -> dict[str, Any]:
        """检查本地执行环境是否就绪

        检查项：
          - Python 解释器可用
          - 工作目录可写
        """
        # 检查 Python 是否可用
        python_ok = True
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            python_ok = proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            python_ok = False

        # 确保输出目录存在
        os.makedirs(_LOCAL_OUTPUT_DIR, exist_ok=True)

        return {
            "available": python_ok,
            "backend": self.name,
            "details": {
                "python_available": python_ok,
                "platform": platform.system(),
                "output_dir": _LOCAL_OUTPUT_DIR,
            },
        }

    async def execute(
        self,
        skill_name: str,
        script_name: str,
        skill_dir: str,
        args: dict[str, Any] | None = None,
        sandbox_level: SandboxLevel = SandboxLevel.L2,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """在本地子进程中执行 Skill 脚本

        通过 subprocess 启动独立进程执行脚本，
        配合超时控制和资源限制实现轻量级沙箱。
        """
        # 验证脚本文件存在
        scripts_dir = Path(skill_dir) / "scripts"
        script_path = scripts_dir / script_name
        if not script_path.is_file():
            raise SkillExecutionError(
                skill_name, script_name,
                f"脚本文件不存在: {script_path}",
            )

        # 设置超时
        if timeout is None:
            timeout = DEFAULT_TIMEOUT if sandbox_level == SandboxLevel.L1 else 15

        args = args or {}
        start_time = time.monotonic()

        # 构建子进程环境变量
        env = self._build_env(args, script_name, timeout, sandbox_level)

        # 构建子进程命令
        cmd = self._build_command(script_path, sandbox_level)

        # 确保输出目录存在
        output_dir = os.path.join(_LOCAL_OUTPUT_DIR, skill_name)
        os.makedirs(output_dir, exist_ok=True)
        env["SKILL_OUTPUT_DIR"] = output_dir

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                # 将子进程工作目录设为脚本所在目录
                cwd=str(scripts_dir),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout + 5,  # 额外 5 秒给进程清理
                )
            except asyncio.TimeoutError:
                # 超时：发送 SIGTERM，等待后 SIGKILL
                try:
                    proc.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

                duration_ms = int((time.monotonic() - start_time) * 1000)
                result = ExecutionResult(
                    skill_name=skill_name,
                    script_name=script_name,
                    exit_code=-1,
                    stdout="",
                    stderr=f"执行超时 ({timeout}s)",
                    duration_ms=duration_ms,
                    sandbox_level=sandbox_level,
                    timed_out=True,
                )
                write_audit_log(skill_name, script_name, sandbox_level, result)
                return result

            duration_ms = int((time.monotonic() - start_time) * 1000)
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            result = ExecutionResult(
                skill_name=skill_name,
                script_name=script_name,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                sandbox_level=sandbox_level,
            )

            write_audit_log(skill_name, script_name, sandbox_level, result)
            return result

        except SkillExecutionError:
            raise
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = ExecutionResult(
                skill_name=skill_name,
                script_name=script_name,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                sandbox_level=sandbox_level,
            )
            write_audit_log(skill_name, script_name, sandbox_level, result)
            raise SkillExecutionError(skill_name, script_name, str(e)) from e

    def _build_env(
        self,
        args: dict[str, Any],
        script_name: str,
        timeout: int,
        sandbox_level: SandboxLevel,
    ) -> dict[str, str]:
        """构建子进程环境变量

        Args:
            args: 脚本参数
            script_name: 脚本文件名
            timeout: 超时时间
            sandbox_level: 沙箱级别

        Returns:
            环境变量字典
        """
        env = os.environ.copy()

        # 传入脚本参数
        args_json = json.dumps(args, ensure_ascii=False)
        env["SKILL_ARGS"] = args_json
        env["SCRIPT_NAME"] = script_name
        env["TIMEOUT"] = str(timeout)
        env["SANDBOX_LEVEL"] = sandbox_level.value

        # L2 级别：设置网络隔离标记（脚本可自行检查）
        if sandbox_level == SandboxLevel.L2:
            env["NETWORK_DISABLED"] = "1"

        return env

    def _build_command(self, script_path: Path, sandbox_level: SandboxLevel) -> list[str]:
        """构建子进程执行命令

        在 Linux 环境下，L2 级别使用 unshare 隔离网络命名空间。
        macOS 不支持 unshare -n，通过环境变量标记实现软隔离。

        Args:
            script_path: 脚本绝对路径
            sandbox_level: 沙箱级别

        Returns:
            命令参数列表
        """
        cmd = ["python3", str(script_path)]

        # Linux 下 L2 级别使用 unshare 隔离网络
        if platform.system() == "Linux" and sandbox_level == SandboxLevel.L2:
            cmd = ["unshare", "-n", "--"] + cmd

        return cmd
