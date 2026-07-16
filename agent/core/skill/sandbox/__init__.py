"""沙箱后端模块

提供可插拔的沙箱执行后端，支持通过配置切换执行策略。

后端列表：
  - LocalSubprocessSandbox: 本地子进程沙箱（默认，开发环境推荐）
  - E2BSandbox: E2B Firecracker MicroVM 沙箱（生产环境推荐）
  - DockerSandbox: Docker 容器沙箱（兼容方案）

使用方式：
  from agent.core.skill.sandbox import SandboxFactory

  # 自动选择可用后端
  backend = SandboxFactory.create()

  # 指定后端
  backend = SandboxFactory.create("e2b")

  # 环境变量控制
  # SKILL_SANDBOX_BACKEND=local|e2b|docker
"""

from agent.core.skill.sandbox.base import SandboxBackend, SandboxBackendError
from agent.core.skill.sandbox.local import LocalSubprocessSandbox
from agent.core.skill.sandbox.e2b import E2BSandbox
from agent.core.skill.sandbox.docker import DockerSandbox
from agent.core.skill.sandbox.factory import SandboxFactory

__all__ = [
    "SandboxBackend",
    "SandboxBackendError",
    "LocalSubprocessSandbox",
    "E2BSandbox",
    "DockerSandbox",
    "SandboxFactory",
]
