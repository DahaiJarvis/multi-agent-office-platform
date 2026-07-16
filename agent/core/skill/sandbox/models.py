"""沙箱执行共享数据模型

定义沙箱执行过程中使用的共享数据类和常量，
避免 skill_runner.py 和 sandbox/ 子模块之间的循环导入。

本模块不依赖 skill 包内的其他模块，可安全被任何模块导入。
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Docker 镜像名称（DockerSandbox 使用）
RUNNER_IMAGE = "skill-runner:latest"

# 默认容器超时时间（秒）
DEFAULT_TIMEOUT = 30

# 审计日志目录
AUDIT_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "logs", "skill_audit",
)


class SandboxLevel(str, Enum):
    """沙箱隔离级别"""

    L0 = "l0"  # 无沙箱，仅用于项目自建 builtin skills
    L1 = "l1"  # 受限沙箱：允许白名单网络，512MB 内存，30秒超时
    L2 = "l2"  # 完全隔离：无网络，256MB 内存，15秒超时


class DockerNotAvailableError(Exception):
    """Docker 不可用异常（保留兼容）"""

    def __init__(self) -> None:
        super().__init__("Docker 不可用，请确保 Docker Desktop 已启动")


class SkillExecutionError(Exception):
    """Skill 脚本执行异常"""

    def __init__(self, skill_name: str, script_name: str, reason: str) -> None:
        self.skill_name = skill_name
        self.script_name = script_name
        self.reason = reason
        super().__init__(f"Skill 脚本执行失败: {skill_name}/{script_name}: {reason}")


class SkillTimeoutError(SkillExecutionError):
    """Skill 脚本执行超时"""

    def __init__(self, skill_name: str, script_name: str, timeout: int) -> None:
        super().__init__(skill_name, script_name, f"执行超时 ({timeout}s)")


@dataclass
class ExecutionResult:
    """脚本执行结果"""

    skill_name: str                                     # Skill 名称
    script_name: str                                    # 脚本名称
    exit_code: int                                      # 退出码（0 表示成功）
    stdout: str                                         # 标准输出
    stderr: str                                         # 标准错误
    duration_ms: int                                    # 执行耗时（毫秒）
    sandbox_level: SandboxLevel                         # 沙箱级别
    container_id: str = ""                              # 容器 ID（Docker 后端使用）
    timed_out: bool = False                             # 是否超时

    @property
    def success(self) -> bool:
        """是否执行成功"""
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "skill_name": self.skill_name,
            "script_name": self.script_name,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:4096],  # 限制输出长度
            "stderr": self.stderr[:2048],
            "duration_ms": self.duration_ms,
            "sandbox_level": self.sandbox_level.value,
            "success": self.success,
            "timed_out": self.timed_out,
        }


def write_audit_log(
    skill_name: str,
    script_name: str,
    sandbox_level: SandboxLevel,
    result: ExecutionResult,
) -> None:
    """写入审计日志

    Args:
        skill_name: Skill 名称
        script_name: 脚本名称
        sandbox_level: 沙箱级别
        result: 执行结果
    """
    try:
        os.makedirs(AUDIT_LOG_DIR, exist_ok=True)
        log_file = os.path.join(AUDIT_LOG_DIR, f"{time.strftime('%Y-%m-%d')}.jsonl")
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "skill_name": skill_name,
            "script_name": script_name,
            "sandbox_level": sandbox_level.value,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "success": result.success,
            "timed_out": result.timed_out,
            "stdout_length": len(result.stdout),
            "stderr_length": len(result.stderr),
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("审计日志写入失败: %s", e)
