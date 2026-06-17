"""Skill 安全扫描模块

对外部 Skills 的脚本内容进行静态安全分析，
检测潜在的危险操作模式，防止恶意 Skill 执行危害操作。

扫描能力：
  - 危险模式检测：反弹 Shell、加密混淆、密钥窃取等
  - 网络外联检测：requests/urllib/socket 等网络访问
  - 文件系统越界检测：访问 /etc/、~/.ssh/ 等敏感路径
  - 环境变量窃取检测：读取 os.environ、.env 文件
  - 子进程调用检测：subprocess/os.system 等

风险等级：
  - critical: 直接拒绝加载（反弹Shell、加密混淆、密钥窃取）
  - high: 仅允许 Level 2 沙箱执行（网络外联、文件越界、环境变量窃取）
  - medium: 允许 Level 1 沙箱执行（受限子进程调用）
  - low: 正常加载（无风险）
"""

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 单个脚本文件最大扫描大小（64KB）
_SCRIPT_MAX_SIZE = 64 * 1024


class SecurityRiskLevel(str, Enum):
    """安全风险等级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityAction(str, Enum):
    """安全处置动作"""

    ALLOW = "allow"           # 允许加载，无需沙箱
    SANDBOX_L1 = "sandbox_l1"  # 允许加载，Level 1 沙箱执行
    SANDBOX_L2 = "sandbox_l2"  # 允许加载，Level 2 沙箱执行（无网络）
    BLOCK = "block"           # 拒绝加载


@dataclass
class SecurityPattern:
    """安全检测模式"""

    name: str                           # 模式名称
    pattern: re.Pattern                 # 正则表达式
    risk_level: SecurityRiskLevel       # 风险等级
    description: str                    # 风险描述


@dataclass
class SecurityMatch:
    """安全检测匹配项"""

    pattern_name: str                   # 匹配的模式名称
    risk_level: SecurityRiskLevel       # 风险等级
    description: str                    # 风险描述
    matched_text: str                   # 匹配到的文本
    line_number: int                    # 行号
    file_path: str                      # 文件路径


@dataclass
class SecurityScanResult:
    """安全扫描结果"""

    skill_name: str                                     # Skill 名称
    source: str                                         # Skill 来源
    risk_level: SecurityRiskLevel = SecurityRiskLevel.LOW  # 综合风险等级
    action: SecurityAction = SecurityAction.ALLOW        # 处置动作
    matches: list[SecurityMatch] = field(default_factory=list)  # 匹配项列表
    scanned_files: int = 0                              # 扫描文件数
    total_size: int = 0                                 # 扫描总大小
    errors: list[str] = field(default_factory=list)     # 扫描错误

    @property
    def is_safe(self) -> bool:
        """是否安全（允许加载）"""
        return self.action != SecurityAction.BLOCK

    @property
    def needs_sandbox(self) -> bool:
        """是否需要沙箱执行"""
        return self.action in (SecurityAction.SANDBOX_L1, SecurityAction.SANDBOX_L2)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "skill_name": self.skill_name,
            "source": self.source,
            "risk_level": self.risk_level.value,
            "action": self.action.value,
            "is_safe": self.is_safe,
            "needs_sandbox": self.needs_sandbox,
            "match_count": len(self.matches),
            "scanned_files": self.scanned_files,
            "total_size": self.total_size,
            "matches": [
                {
                    "pattern_name": m.pattern_name,
                    "risk_level": m.risk_level.value,
                    "description": m.description,
                    "matched_text": m.matched_text[:100],
                    "line_number": m.line_number,
                    "file_path": m.file_path,
                }
                for m in self.matches
            ],
        }


# ==================== 安全检测模式定义 ====================

# critical 级别：直接拒绝
_CRITICAL_PATTERNS: list[SecurityPattern] = [
    SecurityPattern(
        name="reverse_shell",
        pattern=re.compile(r"bash\s+-i|nc\s+-e|/dev/tcp/|python\s+-c\s+.*socket.*exec", re.IGNORECASE),
        risk_level=SecurityRiskLevel.CRITICAL,
        description="检测到反弹 Shell 模式，可能用于建立远程控制通道",
    ),
    SecurityPattern(
        name="encrypted_exec",
        pattern=re.compile(r"base64\.b64decode\s*\(.*\)\s*(?:;|\n)\s*exec\s*\(|eval\s*\(.*base64|exec\s*\(.*decode", re.IGNORECASE),
        risk_level=SecurityRiskLevel.CRITICAL,
        description="检测到加密混淆执行模式，可能隐藏恶意代码",
    ),
    SecurityPattern(
        name="ssh_key_access",
        pattern=re.compile(r"~/\.ssh/|id_rsa|id_ed25519|\.pem|ssh.*private.*key", re.IGNORECASE),
        risk_level=SecurityRiskLevel.CRITICAL,
        description="检测到 SSH 密钥访问，可能窃取认证凭据",
    ),
    SecurityPattern(
        name="credential_file_access",
        pattern=re.compile(r"credentials\.json|service_account\.json|\.aws/credentials|\.gcp.*key", re.IGNORECASE),
        risk_level=SecurityRiskLevel.CRITICAL,
        description="检测到云服务凭据文件访问，可能窃取服务账号",
    ),
]

# high 级别：仅允许 Level 2 沙箱
_HIGH_PATTERNS: list[SecurityPattern] = [
    SecurityPattern(
        name="network_access",
        pattern=re.compile(r"requests\.(get|post|put|delete|patch)\s*\(|urllib\.request|urlopen\s*\(|socket\.socket\s*\(", re.IGNORECASE),
        risk_level=SecurityRiskLevel.HIGH,
        description="检测到网络访问操作，可能外传数据",
    ),
    SecurityPattern(
        name="filesystem_escape",
        pattern=re.compile(r"os\.system\s*\(|subprocess\.(call|run|Popen)\s*\(|open\s*\(\s*['\"]/(etc|var|tmp|root)/", re.IGNORECASE),
        risk_level=SecurityRiskLevel.HIGH,
        description="检测到文件系统越界访问，可能读取敏感系统文件",
    ),
    SecurityPattern(
        name="env_var_access",
        pattern=re.compile(r"os\.environ|os\.getenv\s*\(|dotenv|\.env", re.IGNORECASE),
        risk_level=SecurityRiskLevel.HIGH,
        description="检测到环境变量访问，可能窃取配置密钥",
    ),
    SecurityPattern(
        name="dynamic_import",
        pattern=re.compile(r"__import__\s*\(|importlib\.import_module\s*\(.*\+|exec\s*\(|eval\s*\(", re.IGNORECASE),
        risk_level=SecurityRiskLevel.HIGH,
        description="检测到动态导入或代码执行，可能运行时加载恶意模块",
    ),
]

# medium 级别：允许 Level 1 沙箱
_MEDIUM_PATTERNS: list[SecurityPattern] = [
    SecurityPattern(
        name="subprocess_call",
        pattern=re.compile(r"subprocess\.(call|run|Popen)\s*\(", re.IGNORECASE),
        risk_level=SecurityRiskLevel.MEDIUM,
        description="检测到子进程调用，需确认调用目标是否安全",
    ),
    SecurityPattern(
        name="file_write",
        pattern=re.compile(r"open\s*\(.+['\"]w|\.write\s*\(|shutil\.(copy|move)|os\.rename", re.IGNORECASE),
        risk_level=SecurityRiskLevel.MEDIUM,
        description="检测到文件写入操作，需确认写入目标是否合理",
    ),
    SecurityPattern(
        name="os_module",
        pattern=re.compile(r"os\.remove\s*\(|os\.rmdir\s*\(|os\.kill\s*\(|os\.chmod\s*\(", re.IGNORECASE),
        risk_level=SecurityRiskLevel.MEDIUM,
        description="检测到危险 os 模块操作，需确认用途",
    ),
]

# 合并所有模式
ALL_SECURITY_PATTERNS: list[SecurityPattern] = _CRITICAL_PATTERNS + _HIGH_PATTERNS + _MEDIUM_PATTERNS


def _scan_file(file_path: str | Path) -> list[SecurityMatch]:
    """扫描单个脚本文件

    Args:
        file_path: 脚本文件路径

    Returns:
        安全检测匹配项列表
    """
    matches: list[SecurityMatch] = []
    path = Path(file_path)

    try:
        file_size = path.stat().st_size
        if file_size > _SCRIPT_MAX_SIZE:
            logger.warning("脚本文件过大，跳过扫描: %s (%d bytes)", path, file_size)
            return matches

        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("脚本文件读取失败: %s - %s", path, e)
        return matches

    lines = content.splitlines()
    for pattern in ALL_SECURITY_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            found = pattern.pattern.search(line)
            if found:
                matches.append(SecurityMatch(
                    pattern_name=pattern.name,
                    risk_level=pattern.risk_level,
                    description=pattern.description,
                    matched_text=found.group(),
                    line_number=line_no,
                    file_path=str(path),
                ))

    return matches


def scan_skill_scripts(skill_dir: str, skill_name: str, source: str) -> SecurityScanResult:
    """扫描 Skill 目录下的所有脚本文件

    扫描范围包括 scripts/ 子目录下的 .py、.sh、.js 文件，
    以及 Skill 根目录下的可执行脚本。

    Args:
        skill_dir: Skill 目录绝对路径
        skill_name: Skill 名称
        source: Skill 来源标识

    Returns:
        SecurityScanResult 安全扫描结果
    """
    result = SecurityScanResult(
        skill_name=skill_name,
        source=source,
    )

    skill_path = Path(skill_dir)
    if not skill_path.is_dir():
        result.errors.append(f"Skill 目录不存在: {skill_dir}")
        return result

    # 收集待扫描的脚本文件
    script_files: list[Path] = []
    scripts_dir = skill_path / "scripts"
    if scripts_dir.is_dir():
        for ext in ("*.py", "*.sh", "*.js"):
            script_files.extend(scripts_dir.rglob(ext))

    # 也扫描根目录下的脚本文件（部分 skill 的脚本直接放在根目录）
    for ext in ("*.py", "*.sh", "*.js"):
        for f in skill_path.glob(ext):
            if f.parent == skill_path:
                script_files.append(f)

    if not script_files:
        # 无脚本文件，纯指令型 Skill，安全
        return result

    # 逐文件扫描
    all_matches: list[SecurityMatch] = []
    total_size = 0
    for script_file in script_files:
        try:
            total_size += script_file.stat().st_size
            file_matches = _scan_file(script_file)
            all_matches.extend(file_matches)
            result.scanned_files += 1
        except Exception as e:
            result.errors.append(f"扫描文件失败: {script_file} - {e}")

    result.matches = all_matches
    result.total_size = total_size

    # 计算综合风险等级和处置动作
    if not all_matches:
        result.risk_level = SecurityRiskLevel.LOW
        result.action = SecurityAction.ALLOW
        return result

    # 取最高风险等级
    risk_order = {
        SecurityRiskLevel.CRITICAL: 4,
        SecurityRiskLevel.HIGH: 3,
        SecurityRiskLevel.MEDIUM: 2,
        SecurityRiskLevel.LOW: 1,
    }
    max_risk = max(all_matches, key=lambda m: risk_order[m.risk_level])
    result.risk_level = max_risk.risk_level

    # 根据来源和风险等级决定处置动作
    if result.risk_level == SecurityRiskLevel.CRITICAL:
        result.action = SecurityAction.BLOCK
    elif result.risk_level == SecurityRiskLevel.HIGH:
        # 已验证来源（anthropic）允许 Level 1 沙箱，其他来源 Level 2
        if source == "anthropic":
            result.action = SecurityAction.SANDBOX_L1
        else:
            result.action = SecurityAction.SANDBOX_L2
    elif result.risk_level == SecurityRiskLevel.MEDIUM:
        result.action = SecurityAction.SANDBOX_L1
    else:
        result.action = SecurityAction.ALLOW

    # 记录审计日志
    logger.info(
        "Skill 安全扫描: %s (source=%s, risk=%s, action=%s, matches=%d, files=%d)",
        skill_name, source, result.risk_level.value, result.action.value,
        len(all_matches), result.scanned_files,
    )

    return result


def scan_skill_md_content(content: str, skill_name: str) -> SecurityScanResult:
    """扫描 SKILL.md 内容中的安全风险

    对 SKILL.md 的正文部分进行安全扫描，检测指令注入等风险。
    与 skill_adapter.py 中的 _detect_prompt_injection 互补，
    本模块侧重于检测脚本执行相关的安全风险。

    Args:
        content: SKILL.md 完整内容
        skill_name: Skill 名称

    Returns:
        SecurityScanResult 安全扫描结果
    """
    result = SecurityScanResult(
        skill_name=skill_name,
        source="unknown",
        scanned_files=1,
        total_size=len(content.encode("utf-8")),
    )

    lines = content.splitlines()
    for pattern in _CRITICAL_PATTERNS + _HIGH_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            found = pattern.pattern.search(line)
            if found:
                result.matches.append(SecurityMatch(
                    pattern_name=pattern.name,
                    risk_level=pattern.risk_level,
                    description=pattern.description,
                    matched_text=found.group(),
                    line_number=line_no,
                    file_path="SKILL.md",
                ))

    if result.matches:
        max_risk = max(result.matches, key=lambda m: {
            SecurityRiskLevel.CRITICAL: 4,
            SecurityRiskLevel.HIGH: 3,
            SecurityRiskLevel.MEDIUM: 2,
            SecurityRiskLevel.LOW: 1,
        }[m.risk_level])
        result.risk_level = max_risk.risk_level
        if result.risk_level == SecurityRiskLevel.CRITICAL:
            result.action = SecurityAction.BLOCK
        else:
            result.action = SecurityAction.SANDBOX_L2

    return result
