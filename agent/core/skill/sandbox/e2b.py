"""E2B MicroVM 沙箱后端

通过 E2B (e2b.dev) 的 Firecracker MicroVM 为脚本提供硬件级隔离的执行环境，
是生产环境推荐的安全执行方案。

核心优势：
  - 硬件级隔离：每个沙箱拥有独立内核，容器逃逸无法影响宿主机
  - 极速启动：冷启动 ~150ms
  - 多语言支持：Python / JavaScript / TypeScript 等
  - 文件系统隔离：沙箱内操作不影响宿主机
  - 网络控制：可按需禁用网络访问

依赖：
  - e2b-code-interpreter Python 包
  - E2B API Key（通过环境变量 E2B_API_KEY 配置）

使用方式：
  sandbox = E2BSandbox()
  result = await sandbox.execute(
      skill_name="docx",
      script_name="create_docx.py",
      skill_dir="/path/to/docx",
      args={"title": "测试"},
      sandbox_level=SandboxLevel.L1,
  )

参考文档：
  - https://e2b.dev/docs
  - https://github.com/e2b-dev/e2b
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
    DEFAULT_TIMEOUT,
    write_audit_log,
)

logger = logging.getLogger(__name__)


class E2BSandbox(SandboxBackend):
    """E2B Firecracker MicroVM 沙箱后端

    通过 E2B 的 Code Interpreter SDK 在 MicroVM 中执行脚本，
    提供硬件级隔离，是生产环境推荐的安全执行方案。
    """

    def __init__(self, api_key: str | None = None) -> None:
        """初始化 E2B 沙箱后端

        Args:
            api_key: E2B API Key，None 时从环境变量 E2B_API_KEY 读取
        """
        self._api_key = api_key or os.environ.get("E2B_API_KEY", "")
        self._sandbox_client = None

    @property
    def name(self) -> str:
        return "e2b"

    async def check_environment(self) -> dict[str, Any]:
        """检查 E2B 环境是否就绪

        检查项：
          - e2b_code_interpreter 包是否安装
          - E2B_API_KEY 是否配置
          - E2B 服务是否可达
        """
        # 检查包是否安装
        package_installed = False
        package_version = ""
        try:
            import e2b_code_interpreter
            package_installed = True
            package_version = getattr(e2b_code_interpreter, "__version__", "unknown")
        except ImportError:
            pass

        # 检查 API Key
        api_key_configured = bool(self._api_key)

        # 检查服务可达性（仅在包和 Key 都就绪时检查）
        service_reachable = False
        if package_installed and api_key_configured:
            try:
                from e2b_code_interpreter import Sandbox
                # 尝试创建并立即关闭沙箱，验证服务可达
                sbx = Sandbox(api_key=self._api_key)
                sbx.close()
                service_reachable = True
            except Exception as e:
                logger.warning("E2B 服务不可达: %s", e)

        available = package_installed and api_key_configured and service_reachable

        return {
            "available": available,
            "backend": self.name,
            "details": {
                "package_installed": package_installed,
                "package_version": package_version,
                "api_key_configured": api_key_configured,
                "service_reachable": service_reachable,
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
        """在 E2B MicroVM 中执行 Skill 脚本

        将脚本及其依赖上传到 E2B 沙箱，在 MicroVM 中执行，
        然后收集输出结果。
        """
        # 检查依赖
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as e:
            raise SandboxBackendError(
                self.name,
                "e2b-code-interpreter 包未安装，请执行: pip install e2b-code-interpreter",
            ) from e

        if not self._api_key:
            raise SandboxBackendError(self.name, "E2B_API_KEY 未配置")

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

        try:
            # 在线程池中执行 E2B 同步 API（E2B SDK 主要是同步接口）
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                self._execute_sync,
                Sandbox, skill_name, script_name, script_path,
                skill_dir, scripts_dir, args, sandbox_level, timeout,
            )
            return result

        except SkillExecutionError:
            raise
        except SandboxBackendError:
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

    def _execute_sync(
        self,
        sandbox_cls: type,
        skill_name: str,
        script_name: str,
        script_path: Path,
        skill_dir: str,
        scripts_dir: Path,
        args: dict[str, Any],
        sandbox_level: SandboxLevel,
        timeout: int,
    ) -> ExecutionResult:
        """同步执行 E2B 沙箱脚本（在线程池中调用）

        Args:
            sandbox_cls: E2B Sandbox 类
            skill_name: Skill 名称
            script_name: 脚本文件名
            script_path: 脚本绝对路径
            skill_dir: Skill 目录路径
            scripts_dir: scripts 子目录路径
            args: 脚本参数
            sandbox_level: 沙箱级别
            timeout: 超时时间

        Returns:
            ExecutionResult 执行结果
        """
        start_time = time.monotonic()
        sbx = None

        try:
            # 创建 E2B 沙箱
            sbx = sandbox_cls(api_key=self._api_key, timeout=timeout)

            # 上传 scripts 目录到沙箱
            # E2B 沙箱内有自己的文件系统，需要将脚本上传进去
            for file_path in scripts_dir.rglob("*"):
                if file_path.is_file():
                    relative = file_path.relative_to(scripts_dir)
                    remote_path = f"/home/user/skill/scripts/{relative}"
                    sbx.files.write(remote_path, file_path.read_text(encoding="utf-8", errors="replace"))

            # 上传 Skill 根目录下的非脚本文件（如配置文件）
            skill_root = Path(skill_dir)
            for file_path in skill_root.iterdir():
                if file_path.is_file() and file_path.suffix in (".yaml", ".yml", ".json", ".txt", ".md"):
                    remote_path = f"/home/user/skill/{file_path.name}"
                    try:
                        sbx.files.write(remote_path, file_path.read_text(encoding="utf-8", errors="replace"))
                    except Exception:
                        pass  # 非关键文件，上传失败不影响执行

            # 构建执行代码：设置环境变量 + 执行脚本
            args_json = json.dumps(args, ensure_ascii=False)
            exec_code = f"""
import os
import sys
import json

os.environ['SKILL_ARGS'] = {repr(args_json)}
os.environ['SCRIPT_NAME'] = {repr(script_name)}
os.environ['TIMEOUT'] = {repr(str(timeout))}
os.environ['SANDBOX_LEVEL'] = {repr(sandbox_level.value)}
os.environ['SKILL_OUTPUT_DIR'] = '/home/user/output'

os.chdir('/home/user/skill/scripts')
sys.path.insert(0, '/home/user/skill/scripts')

exec(open('/home/user/skill/scripts/{script_name}').read())
"""

            # 执行脚本
            execution = sbx.run_code(exec_code, language="python")

            duration_ms = int((time.monotonic() - start_time) * 1000)

            # 收集输出
            stdout_parts = []
            stderr_parts = []

            if execution.logs:
                for log in execution.logs:
                    if hasattr(log, 'text') and log.text:
                        stdout_parts.append(log.text)
                    elif isinstance(log, str):
                        stdout_parts.append(log)

            if execution.error:
                stderr_parts.append(str(execution.error))

            stdout = "\n".join(stdout_parts)
            stderr = "\n".join(stderr_parts)
            exit_code = 0 if not execution.error else 1

            # 下载输出文件（如果有）
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )))),
                "logs", "skill_output", skill_name,
            )
            os.makedirs(output_dir, exist_ok=True)

            try:
                # 尝试获取沙箱输出目录中的文件
                output_files = sbx.files.list("/home/user/output")
                for f in output_files:
                    if hasattr(f, 'name') and hasattr(f, 'path'):
                        try:
                            content = sbx.files.read(f.path)
                            local_path = os.path.join(output_dir, f.name)
                            with open(local_path, "w", encoding="utf-8") as lf:
                                lf.write(content if isinstance(content, str) else str(content))
                        except Exception:
                            pass
            except Exception:
                pass  # 输出文件下载非关键

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

        finally:
            # 确保沙箱被关闭
            if sbx is not None:
                try:
                    sbx.close()
                except Exception:
                    pass

    async def cleanup(self) -> None:
        """清理 E2B 沙箱资源"""
        if self._sandbox_client is not None:
            try:
                self._sandbox_client.close()
            except Exception:
                pass
            self._sandbox_client = None
