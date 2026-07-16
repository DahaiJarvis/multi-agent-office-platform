"""Docker 容器沙箱后端

通过 Docker 容器为脚本提供隔离的执行环境，
是原有的沙箱实现方案，保留兼容性。

安全措施：
  - 容器隔离：独立文件系统和进程空间
  - 资源限制：CPU / 内存 / PID 限制
  - 网络隔离：L2 级别禁用网络
  - 只读挂载：Skill 目录以只读方式挂载

依赖：
  - Docker Desktop（Mac 环境）
  - 预构建的 skill-runner 镜像

限制：
  - Docker 容器共享宿主机内核，不是真正的安全边界
  - 冷启动较慢（1-3s）
  - Mac 上 Docker Desktop 性能较差
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from agent.core.skill.sandbox.base import SandboxBackend, SandboxBackendError
from agent.core.skill.sandbox.models import (
    ExecutionResult,
    SandboxLevel,
    SkillExecutionError,
    SkillTimeoutError,
    DEFAULT_TIMEOUT,
    RUNNER_IMAGE,
    write_audit_log,
)

logger = logging.getLogger(__name__)

# Dockerfile 所在目录
_DOCKERFILE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))),
    "skills", "scripts",
)


async def _check_docker_available() -> bool:
    """检查 Docker 是否可用"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        return proc.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return False


async def _ensure_runner_image() -> bool:
    """确保 skill-runner 镜像存在，不存在则尝试构建"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", RUNNER_IMAGE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        if proc.returncode == 0:
            return True
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return False

    # 镜像不存在，尝试构建
    dockerfile_path = os.path.join(_DOCKERFILE_DIR, "Dockerfile")
    if not os.path.isfile(dockerfile_path):
        logger.warning("skill-runner Dockerfile 不存在: %s", dockerfile_path)
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "build", "-t", RUNNER_IMAGE, "-f", dockerfile_path, _DOCKERFILE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        return proc.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
        logger.warning("构建 skill-runner 镜像失败: %s", e)
        return False


def _build_docker_args(
    skill_dir: str,
    script_name: str,
    args: dict[str, Any],
    sandbox_level: SandboxLevel,
    timeout: int,
) -> list[str]:
    """构建 Docker 运行参数"""
    cmd = ["docker", "run", "--rm"]

    # 资源限制
    if sandbox_level == SandboxLevel.L1:
        cmd.extend(["--memory=512m", "--cpus=1", "--pids-limit=50"])
    elif sandbox_level == SandboxLevel.L2:
        cmd.extend(["--memory=256m", "--cpus=1", "--pids-limit=20"])
        cmd.append("--network=none")

    # 挂载 Skill 目录（只读）
    cmd.extend(["-v", f"{skill_dir}:/skill:ro"])

    # 临时输出目录（可写）
    cmd.extend(["-v", f"skill-output-{int(time.time())}:/output"])

    # 传入参数
    args_json = json.dumps(args, ensure_ascii=False)
    cmd.extend(["-e", f"SKILL_ARGS={args_json}"])
    cmd.extend(["-e", f"SCRIPT_NAME={script_name}"])
    cmd.extend(["-e", f"TIMEOUT={timeout}"])

    # 镜像和入口命令
    cmd.append(RUNNER_IMAGE)
    cmd.extend(["python", f"/skill/scripts/{script_name}"])

    return cmd


class DockerSandbox(SandboxBackend):
    """Docker 容器沙箱后端

    通过 Docker 容器提供隔离的脚本执行环境，
    保留原有实现逻辑，作为兼容方案。
    """

    def __init__(self) -> None:
        self._docker_available: bool | None = None
        self._image_available: bool | None = None

    @property
    def name(self) -> str:
        return "docker"

    async def check_environment(self) -> dict[str, Any]:
        """检查 Docker 环境是否就绪"""
        docker_ok = await _check_docker_available()
        image_ok = False
        if docker_ok:
            image_ok = await _ensure_runner_image()

        self._docker_available = docker_ok
        self._image_available = image_ok

        return {
            "available": docker_ok and image_ok,
            "backend": self.name,
            "details": {
                "docker_available": docker_ok,
                "runner_image_available": image_ok,
                "runner_image": RUNNER_IMAGE,
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
        """在 Docker 容器中执行 Skill 脚本"""
        # 检查 Docker 环境
        if self._docker_available is None:
            await self.check_environment()

        if not self._docker_available:
            raise SandboxBackendError(self.name, "Docker 不可用，请确保 Docker Desktop 已启动")

        if not self._image_available:
            raise SandboxBackendError(
                self.name,
                f"沙箱镜像 {RUNNER_IMAGE} 不可用，请先构建",
            )

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

        # 构建 Docker 运行参数
        docker_args = _build_docker_args(skill_dir, script_name, args, sandbox_level, timeout)

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout + 10,  # 额外 10 秒给 Docker 清理
                )
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

        except SandboxBackendError:
            raise
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
