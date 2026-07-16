"""Skill 适配核心模块

实现 SKILL.md 格式的技能导入、解析、绑定和运行时管理，
使 Agent 行为可通过外部 Skill 增强。

核心类：
  - SkillManifest: SKILL.md 的 YAML Front Matter 模型
  - SkillDocument: SKILL.md 完整文档（manifest + instruction）
  - SkillPackManifest: skill.yaml 结构化元数据模型
  - SkillDependency: 技能依赖声明模型
  - SkillTestCase: 技能测试用例模型
  - SkillRegistry: Skills 仓库管理（加载/搜索/绑定/启禁/导入导出/版本/市场）

SkillPack 格式（技能包目录结构）：
  skills/
    email-advanced/
      SKILL.md              # 技能描述和指令（保留兼容）
      skill.yaml            # 结构化元数据（新增）
      tools.yaml            # 工具绑定配置（新增）
      prompts/              # Prompt 模板（新增）
        system_prompt.tmpl
        few_shot_examples.yaml
      tests/                # 测试用例（新增）
        test_cases.yaml
      dependencies.yaml     # 依赖声明（新增）

Prompt 注入检测：
  - 第一层：基于规则的正则匹配（7 种模式）
  - 第二层：LLM 辅助判断（可选，规则检测 risk_level >= medium 时）

风险等级：
  - high（>=2 个匹配）：拒绝
  - medium（1 个匹配）：标记 review-required
  - low（0 个匹配）：放行
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "skills")

# 外部 Skills 搜索目录（Claude/OpenClaw 等）
# 额外扫描目录（builtin 和外部 skills 不在 skills/ 一级目录下，需单独扫描）
_EXTRA_SCAN_DIRS = [
    os.path.join(SKILLS_DIR, "builtin"),
    os.path.join(SKILLS_DIR, "external", "anthropic"),
    os.path.join(SKILLS_DIR, "external", "openclaw"),
]

# Skill 来源枚举
SKILL_SOURCE_BUILTIN = "builtin"
SKILL_SOURCE_LOCAL = "local"
SKILL_SOURCE_ANTHROPIC = "anthropic"
SKILL_SOURCE_OPENCLAW = "openclaw"

# 来源目录到来源标识的映射
_SOURCE_DIR_MAP: dict[str, str] = {
    os.path.join(SKILLS_DIR, "builtin"): SKILL_SOURCE_BUILTIN,
    os.path.join(SKILLS_DIR, "external", "anthropic"): SKILL_SOURCE_ANTHROPIC,
    os.path.join(SKILLS_DIR, "external", "openclaw"): SKILL_SOURCE_OPENCLAW,
}

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

SKILL_MAX_SIZE = 64 * 1024

# Redis Key 前缀
_SKILL_VERSION_PREFIX = "skill:"
_SKILL_MARKET_PREFIX = "skill_market:"
_SKILL_MARKET_SET_KEY = "skill_market:all"
_SKILL_MARKET_CATEGORY_PREFIX = "skill_market:category:"
_SKILL_MARKET_RATINGS_PREFIX = "skill_market:ratings:"
_SKILL_TEST_PREFIX = "skill_test:"
_SKILL_BINDING_PREFIX = "skill_binding:"
_SKILL_BUILTIN_BINDING_PREFIX = "skill_builtin_binding:"


class SkillParseError(Exception):
    """SKILL.md 解析错误"""

    def __init__(self, file_name: str, line: int | None = None, reason: str = ""):
        self.file_name = file_name
        self.line = line
        self.reason = reason
        msg = f"SKILL.md 解析失败: {file_name}"
        if line:
            msg += f" (行 {line})"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class SkillValidationError(Exception):
    """Skill 校验错误"""

    def __init__(self, skill_name: str, reason: str):
        self.skill_name = skill_name
        self.reason = reason
        super().__init__(f"Skill 校验失败: {skill_name}: {reason}")


class SkillDependencyError(Exception):
    """Skill 依赖不满足错误"""

    def __init__(self, skill_name: str, missing_required: list[str], missing_optional: list[str] | None = None):
        self.skill_name = skill_name
        self.missing_required = missing_required
        self.missing_optional = missing_optional or []
        parts = [f"Skill 依赖不满足: {skill_name}"]
        if missing_required:
            parts.append(f"缺少必需依赖: {', '.join(missing_required)}")
        if self.missing_optional:
            parts.append(f"缺少可选依赖: {', '.join(self.missing_optional)}")
        super().__init__("; ".join(parts))


PROMPT_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|above|all)\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|previous)", re.IGNORECASE),
    re.compile(r"disregard\s+(your|previous|above)", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"override\s+(previous|default|safety)", re.IGNORECASE),
]

# 语义重叠判定阈值：两个 Skill 声明的工具交集占比超过此值时判定为语义重叠
_TOOL_OVERLAP_THRESHOLD = 0.5


class SkillConflictItem(BaseModel):
    """Skill 冲突项"""

    conflict_type: str = Field(description="冲突类型: name_exact / name_normalized / semantic_overlap")
    builtin_skill_id: str = Field(description="内置技能 ID")
    builtin_skill_name: str = Field(description="内置技能名称")
    skill_name: str = Field(description="SKILL.md 技能名称")
    detail: str = Field(default="", description="冲突详情")


def _detect_skill_conflict(skill_name: str, suggested_tools: list[str]) -> list[SkillConflictItem]:
    """检测 SKILL.md 技能与内置技能的名称/语义冲突

    检测两层冲突：
      1. 精确名称冲突：SKILL.md 名称规范化后与内置技能 ID 相同
      2. 语义重叠：SKILL.md 声明的工具与内置技能的工具交集占比超过阈值

    Args:
        skill_name: SKILL.md 技能名称（已规范化）
        suggested_tools: SKILL.md 声明的工具列表

    Returns:
        冲突项列表，无冲突时返回空列表
    """
    conflicts: list[SkillConflictItem] = []

    try:
        from agent.agents.skill_defs import BUILTIN_SKILLS
    except Exception:
        return conflicts

    # 规范化名称：连字符转下划线
    normalized_name = skill_name.replace("-", "_")

    for builtin_id, builtin_config in BUILTIN_SKILLS.items():
        # 第一层：精确名称冲突
        if normalized_name == builtin_id:
            conflicts.append(SkillConflictItem(
                conflict_type="name_exact",
                builtin_skill_id=builtin_id,
                builtin_skill_name=builtin_config.name,
                skill_name=skill_name,
                detail=f"SKILL.md 名称 '{skill_name}' 规范化后与内置技能 ID '{builtin_id}' 相同",
            ))
            continue

        # 第二层：语义重叠检测
        if suggested_tools and builtin_config.required_tools:
            builtin_tools = set(builtin_config.required_tools)
            skill_tools = set(suggested_tools)
            if not skill_tools:
                continue
            overlap = skill_tools & builtin_tools
            overlap_ratio = len(overlap) / len(skill_tools)
            if overlap_ratio >= _TOOL_OVERLAP_THRESHOLD:
                conflicts.append(SkillConflictItem(
                    conflict_type="semantic_overlap",
                    builtin_skill_id=builtin_id,
                    builtin_skill_name=builtin_config.name,
                    skill_name=skill_name,
                    detail=f"工具重叠率 {overlap_ratio:.0%}，重叠工具: {', '.join(sorted(overlap))}",
                ))

    return conflicts


# ==================== 指令矛盾检测 ====================

# 矛盾关键词对：(模式A, 模式B, 严重等级)
# 模式A 和 模式B 分别匹配不同 Skill 的指令时，判定为矛盾
CONTRADICTION_PATTERNS: list[tuple[re.Pattern, re.Pattern, str]] = [
    (
        re.compile(r"必须确认|需要确认|务必确认|确认后", re.IGNORECASE),
        re.compile(r"无需确认|直接发送|跳过确认|自动发送", re.IGNORECASE),
        "high",
    ),
    (
        re.compile(r"禁止|不允许|不可|不得", re.IGNORECASE),
        re.compile(r"可以|允许|直接|随意", re.IGNORECASE),
        "high",
    ),
    (
        re.compile(r"必须脱敏|需脱敏|脱敏后|敏感信息.*隐藏", re.IGNORECASE),
        re.compile(r"原文|原始数据|完整数据|完整展示", re.IGNORECASE),
        "high",
    ),
    (
        re.compile(r"严格|严谨|精确|务必", re.IGNORECASE),
        re.compile(r"宽松|灵活|大致|随意", re.IGNORECASE),
        "medium",
    ),
]


class InstructionConflict(BaseModel):
    """指令矛盾项"""

    skill_a: str = Field(description="Skill A 名称")
    skill_b: str = Field(description="Skill B 名称")
    pattern_a_text: str = Field(description="Skill A 中的矛盾文本")
    pattern_b_text: str = Field(description="Skill B 中的矛盾文本")
    severity: str = Field(description="严重等级: high / medium / low")


def _detect_instruction_conflicts(skills: list["SkillDocument"]) -> list[InstructionConflict]:
    """检测多个 Skill 之间的指令矛盾

    对每对 Skill 的指令文本，检查是否同时匹配矛盾关键词对的两端。

    Args:
        skills: Skill 列表

    Returns:
        矛盾项列表，无矛盾时返回空列表
    """
    if len(skills) < 2:
        return []

    conflicts: list[InstructionConflict] = []

    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            skill_a = skills[i]
            skill_b = skills[j]
            instruction_a = skill_a.instruction or ""
            instruction_b = skill_b.instruction or ""

            for pattern_a, pattern_b, severity in CONTRADICTION_PATTERNS:
                # 正向匹配：A 匹配模式A，B 匹配模式B
                match_a = pattern_a.search(instruction_a)
                match_b = pattern_b.search(instruction_b)
                name_a = skill_a.manifest.name
                name_b = skill_b.manifest.name

                if not (match_a and match_b):
                    # 反向匹配：B 匹配模式A，A 匹配模式B
                    match_a = pattern_a.search(instruction_b)
                    match_b = pattern_b.search(instruction_a)
                    if match_a and match_b:
                        name_a, name_b = name_b, name_a
                    else:
                        continue

                conflicts.append(InstructionConflict(
                    skill_a=name_a,
                    skill_b=name_b,
                    pattern_a_text=match_a.group(),
                    pattern_b_text=match_b.group(),
                    severity=severity,
                ))

    return conflicts


class SkillManifest(BaseModel):
    """SKILL.md 的 YAML Front Matter 模型"""

    name: str = Field(min_length=1, max_length=64, description="Skill 名称")
    description: str = Field(default="", max_length=2048, description="Skill 描述")
    version: str = Field(default="1.0.0", description="版本号")
    author: str = Field(default="", description="作者")
    category: str = Field(default="custom", description="分类")
    tags: list[str] = Field(default_factory=list, description="标签")
    priority: int = Field(default=5, ge=1, le=10, description="优先级")
    review_required: bool = Field(default=False, description="是否需要审核")
    collaboration_mode: str = Field(default="direct", description="协作模式")
    suggested_tools: list[str] = Field(default_factory=list, description="建议使用的工具")

    created_at: str = Field(default="", description="创建时间")
    updated_at: str = Field(default="", description="更新时间")
    enabled: bool = Field(default=True, description="是否启用")

    # 来源信息（由系统自动填充，不由 SKILL.md 声明）
    source: str = Field(default=SKILL_SOURCE_LOCAL, description="Skill 来源: builtin/local/anthropic/openclaw")
    skill_dir: str = Field(default="", description="Skill 目录的绝对路径，用于定位 scripts 等资源")


class SkillDocument(BaseModel):
    """SKILL.md 完整文档"""

    manifest: SkillManifest
    instruction: str = Field(default="", description="Skill 指令内容")

    @property
    def name(self) -> str:
        return self.manifest.name


# ==================== SkillPack 格式模型 ====================


class SkillDependency(BaseModel):
    """技能依赖声明"""

    skills: list[dict[str, Any]] = Field(default_factory=list, description="依赖的技能列表")
    tools: list[dict[str, Any]] = Field(default_factory=list, description="依赖的工具列表")
    mcp_servers: list[str] = Field(default_factory=list, description="依赖的 MCP Server")


class SkillPackEntry(BaseModel):
    """SkillPack 入口配置"""

    instruction_file: str = Field(default="SKILL.md", description="指令文件路径")
    system_prompt_template: str = Field(default="", description="系统 Prompt 模板路径")
    few_shot_file: str = Field(default="", description="Few-shot 示例文件路径")


class SkillPackCompatibility(BaseModel):
    """SkillPack 版本兼容性"""

    min_platform_version: str = Field(default="0.1.0", description="最低平台版本")
    max_platform_version: str = Field(default="", description="最高平台版本（空表示不限）")


class SkillPackManifest(BaseModel):
    """skill.yaml 结构化元数据模型

    SkillPack 格式的核心配置文件，包含技能的完整元信息、
    依赖声明、版本兼容性和入口配置。
    """

    name: str = Field(min_length=1, max_length=64, description="技能名称")
    version: str = Field(default="1.0.0", description="版本号（语义化版本）")
    author: str = Field(default="", description="作者")
    category: str = Field(default="custom", description="分类")
    tags: list[str] = Field(default_factory=list, description="标签")
    priority: int = Field(default=5, ge=1, le=10, description="优先级")
    description: str = Field(default="", max_length=1024, description="详细描述")

    dependencies: SkillDependency = Field(default_factory=SkillDependency, description="依赖声明")
    compatibility: SkillPackCompatibility = Field(default_factory=SkillPackCompatibility, description="版本兼容性")
    entry: SkillPackEntry = Field(default_factory=SkillPackEntry, description="入口配置")


class ToolBinding(BaseModel):
    """工具绑定配置"""

    tool_name: str = Field(description="工具名称")
    required: bool = Field(default=True, description="是否必需")
    config: dict[str, Any] = Field(default_factory=dict, description="工具配置")


class ToolsConfig(BaseModel):
    """tools.yaml 工具绑定配置"""

    tools: list[ToolBinding] = Field(default_factory=list, description="工具绑定列表")


class SkillTestCase(BaseModel):
    """技能测试用例"""

    name: str = Field(description="测试用例名称")
    input: str = Field(description="模拟用户输入")
    expected_intent: str = Field(default="", description="期望的意图识别结果")
    expected_tools: list[str] = Field(default_factory=list, description="期望调用的工具列表")
    expected_response_contains: list[str] = Field(default_factory=list, description="期望响应中包含的关键词")


class SkillTestSuite(BaseModel):
    """技能测试套件"""

    test_cases: list[SkillTestCase] = Field(default_factory=list, description="测试用例列表")


class SkillTestResult(BaseModel):
    """技能测试结果"""

    test_name: str = Field(description="测试用例名称")
    passed: bool = Field(description="是否通过")
    actual_intent: str = Field(default="", description="实际意图")
    actual_tools: list[str] = Field(default_factory=list, description="实际调用的工具")
    error_message: str = Field(default="", description="错误信息")
    execution_time_ms: float = Field(default=0.0, description="执行耗时(毫秒)")


class SkillTestReport(BaseModel):
    """技能测试报告"""

    skill_name: str = Field(description="技能名称")
    version: str = Field(default="", description="版本号")
    total: int = Field(default=0, description="总用例数")
    passed: int = Field(default=0, description="通过数")
    failed: int = Field(default=0, description="失败数")
    results: list[SkillTestResult] = Field(default_factory=list, description="测试结果列表")
    executed_at: str = Field(default="", description="执行时间")


class MarketplaceEntry(BaseModel):
    """技能市场条目"""

    skill_name: str = Field(description="技能名称")
    version: str = Field(default="", description="当前版本")
    description: str = Field(default="", description="描述")
    author: str = Field(default="", description="作者")
    category: str = Field(default="general", description="分类")
    tags: list[str] = Field(default_factory=list, description="标签")
    downloads: int = Field(default=0, description="下载量")
    rating: float = Field(default=0.0, ge=0.0, le=5.0, description="评分")
    rating_count: int = Field(default=0, description="评分人数")
    published_at: float = Field(default=0, description="发布时间戳")


def _normalize_yaml_keys(raw_meta: dict[str, Any]) -> dict[str, Any]:
    """将 YAML 中的连字符键名转换为下划线键名

    YAML Front Matter 中常用连字符命名（如 suggested-tools），
    但 Python 模型字段使用下划线命名（如 suggested_tools）。

    Args:
        raw_meta: YAML 解析后的原始字典

    Returns:
        键名规范化后的字典
    """
    key_mapping = {
        "review-required": "review_required",
        "collaboration-mode": "collaboration_mode",
        "suggested-tools": "suggested_tools",
    }
    normalized = {}
    for key, value in raw_meta.items():
        normalized_key = key_mapping.get(key, key)
        normalized[normalized_key] = value
    return normalized


def _normalize_skill_name(name: str) -> str:
    """规范化 Skill 名称：小写+连字符

    Args:
        name: 原始名称

    Returns:
        规范化后的名称
    """
    normalized = name.strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    normalized = normalized.strip("-")
    return normalized


def _parse_skill_md(file_path: str) -> SkillDocument:
    """解析 SKILL.md 文件

    解析 YAML Front Matter 和 Markdown 正文。

    Args:
        file_path: SKILL.md 文件路径

    Returns:
        SkillDocument 实例

    Raises:
        SkillParseError: 解析失败
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise SkillParseError(os.path.basename(file_path), reason=str(e))

    if not content.strip():
        raise SkillParseError(os.path.basename(file_path), reason="文件内容为空")

    front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not front_matter_match:
        raise SkillParseError(os.path.basename(file_path), reason="缺少 YAML Front Matter（--- 分隔符）")

    yaml_str = front_matter_match.group(1)
    instruction = front_matter_match.group(2).strip()

    try:
        raw_meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        line = getattr(e, "problem_mark", None)
        line_no = line.line if line else None
        raise SkillParseError(os.path.basename(file_path), line=line_no, reason=f"YAML 解析错误: {e}")

    if not isinstance(raw_meta, dict):
        raise SkillParseError(os.path.basename(file_path), reason="YAML Front Matter 必须是键值对格式")

    raw_meta = _normalize_yaml_keys(raw_meta)

    if "name" not in raw_meta:
        raise SkillParseError(os.path.basename(file_path), reason="缺少必填字段: name")

    if "description" not in raw_meta:
        raise SkillParseError(os.path.basename(file_path), reason="缺少必填字段: description")

    raw_meta["name"] = _normalize_skill_name(str(raw_meta["name"]))

    if not SKILL_NAME_PATTERN.match(raw_meta["name"]):
        raise SkillParseError(
            os.path.basename(file_path),
            reason=f"Skill 名称不规范: {raw_meta['name']}，仅允许小写字母、数字和连字符",
        )

    try:
        manifest = SkillManifest(**raw_meta)
    except Exception as e:
        raise SkillParseError(os.path.basename(file_path), reason=f"字段校验失败: {e}")

    return SkillDocument(manifest=manifest, instruction=instruction)


def _detect_prompt_injection(content: str) -> dict[str, Any]:
    """检测 Prompt 注入

    基于规则的正则匹配，检测 SKILL.md 内容中的潜在注入模式。

    Args:
        content: 待检测内容

    Returns:
        包含 risk_level、matches、match_count 的字典
    """
    matches: list[dict[str, str]] = []
    for pattern in PROMPT_INJECTION_PATTERNS:
        found = pattern.search(content)
        if found:
            matches.append({
                "pattern": pattern.pattern,
                "matched": found.group(),
            })

    match_count = len(matches)
    if match_count >= 2:
        risk_level = "high"
    elif match_count == 1:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level,
        "matches": matches,
        "match_count": match_count,
    }


def sanitize_prompt(content: str) -> str:
    """过滤 Skill 指令中的潜在注入内容

    对检测到的注入模式进行替换处理。

    Args:
        content: 原始指令内容

    Returns:
        过滤后的指令内容
    """
    sanitized = content
    for pattern in PROMPT_INJECTION_PATTERNS:
        sanitized = pattern.sub("[已过滤]", sanitized)
    return sanitized


# ==================== SkillPack 解析 ====================


def _parse_skill_yaml(skill_dir: Path) -> SkillPackManifest | None:
    """解析 skill.yaml 结构化元数据

    Args:
        skill_dir: 技能包目录路径

    Returns:
        SkillPackManifest 实例，文件不存在时返回 None
    """
    skill_yaml_path = skill_dir / "skill.yaml"
    if not skill_yaml_path.is_file():
        return None

    try:
        with open(str(skill_yaml_path), "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            logger.warning("skill.yaml 格式错误: %s，必须是键值对", skill_dir.name)
            return None

        raw["name"] = _normalize_skill_name(str(raw.get("name", skill_dir.name)))

        return SkillPackManifest(**raw)
    except Exception as e:
        logger.warning("skill.yaml 解析失败: %s - %s", skill_dir.name, e)
        return None


def _parse_dependencies_yaml(skill_dir: Path) -> SkillDependency:
    """解析 dependencies.yaml 依赖声明

    Args:
        skill_dir: 技能包目录路径

    Returns:
        SkillDependency 实例，文件不存在时返回空依赖
    """
    dep_path = skill_dir / "dependencies.yaml"
    if not dep_path.is_file():
        return SkillDependency()

    try:
        with open(str(dep_path), "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            return SkillDependency()

        return SkillDependency(**raw)
    except Exception as e:
        logger.warning("dependencies.yaml 解析失败: %s - %s", skill_dir.name, e)
        return SkillDependency()


def _parse_tools_yaml(skill_dir: Path) -> ToolsConfig:
    """解析 tools.yaml 工具绑定配置

    Args:
        skill_dir: 技能包目录路径

    Returns:
        ToolsConfig 实例，文件不存在时返回空配置
    """
    tools_path = skill_dir / "tools.yaml"
    if not tools_path.is_file():
        return ToolsConfig()

    try:
        with open(str(tools_path), "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            return ToolsConfig()

        return ToolsConfig(**raw)
    except Exception as e:
        logger.warning("tools.yaml 解析失败: %s - %s", skill_dir.name, e)
        return ToolsConfig()


def _parse_test_cases_yaml(skill_dir: Path) -> SkillTestSuite:
    """解析 tests/test_cases.yaml 测试用例

    Args:
        skill_dir: 技能包目录路径

    Returns:
        SkillTestSuite 实例，文件不存在时返回空测试套件
    """
    test_path = skill_dir / "tests" / "test_cases.yaml"
    if not test_path.is_file():
        return SkillTestSuite()

    try:
        with open(str(test_path), "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            return SkillTestSuite()

        return SkillTestSuite(**raw)
    except Exception as e:
        logger.warning("test_cases.yaml 解析失败: %s - %s", skill_dir.name, e)
        return SkillTestSuite()


def _read_prompt_template(skill_dir: Path, template_path: str) -> str:
    """读取 Prompt 模板文件

    Args:
        skill_dir: 技能包目录路径
        template_path: 模板文件相对路径

    Returns:
        模板内容，文件不存在时返回空字符串
    """
    if not template_path:
        return ""

    full_path = skill_dir / template_path
    if not full_path.is_file():
        return ""

    try:
        return full_path.read_text(encoding="utf-8")
    except OSError:
        return ""


class SkillRegistry:
    """Skills 仓库管理

    提供 SKILL.md 的加载、搜索、绑定、启禁、导入导出等管理能力。
    支持 SkillPack 格式（skill.yaml + dependencies.yaml + tests）。
    支持版本管理、市场持久化（Redis）和技能测试框架。
    单例模式，通过 get_instance() 获取全局实例。
    """

    _instance: "SkillRegistry | None" = None

    def __init__(self, skills_dir: str | None = None) -> None:
        self._skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, SkillDocument] = {}
        # Skill 名称到目录绝对路径的映射，用于定位 scripts 等资源
        self._skill_dir_map: dict[str, str] = {}
        self._agent_bindings: dict[str, list[str]] = {}
        # 内置技能绑定：从 AGENT_SKILL_BINDINGS 初始化，统一由 SkillRegistry 管理
        self._builtin_bindings: dict[str, list[str]] = {}
        self._init_builtin_bindings()
        self._active_skills: dict[str, set[str]] = {}
        self._loaded: bool = False
        # SkillPack 扩展数据
        self._pack_manifests: dict[str, SkillPackManifest] = {}
        self._pack_dependencies: dict[str, SkillDependency] = {}
        self._pack_tools: dict[str, ToolsConfig] = {}
        self._pack_test_suites: dict[str, SkillTestSuite] = {}
        self._pack_system_prompts: dict[str, str] = {}
        self._pack_few_shots: dict[str, str] = {}
        # 版本管理
        self._active_versions: dict[str, str] = {}
        # 市场数据（内存缓存）
        self._marketplace: dict[str, MarketplaceEntry] = {}
        self._marketplace_loaded_at: float = 0.0

    @classmethod
    def get_instance(cls) -> "SkillRegistry":
        """获取全局单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）"""
        cls._instance = None

    def _init_builtin_bindings(self) -> None:
        """从 AGENT_SKILL_BINDINGS 初始化内置技能绑定

        将 domain.py 中硬编码的内置技能绑定关系同步到 SkillRegistry，
        使 SkillRegistry 成为技能绑定的统一管理入口。
        """
        try:
            from agent.agents.skill_defs import AGENT_SKILL_BINDINGS
            for agent_name, skill_ids in AGENT_SKILL_BINDINGS.items():
                self._builtin_bindings[agent_name] = list(skill_ids)
            logger.info("内置技能绑定初始化完成: %d 个 Agent", len(self._builtin_bindings))
        except Exception as e:
            logger.warning("内置技能绑定初始化失败: %s", e)

    def _schedule_persist_binding(self, agent_name: str) -> None:
        """调度绑定关系持久化（发后即忘）

        Args:
            agent_name: Agent 名称
        """
        from agent.core.infrastructure.async_utils import schedule_async_task
        schedule_async_task(self._persist_binding(agent_name), task_name=f"技能绑定持久化({agent_name})")

    async def _persist_binding(self, agent_name: str) -> None:
        """将 Agent 的技能绑定关系持久化到 Redis

        Args:
            agent_name: Agent 名称
        """
        try:
            from agent.core.infrastructure.redis_manager import get_redis_client
            redis = await get_redis_client()
            if redis is None:
                return
            import json
            from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
            ttl = get_persist_ttl_seconds()
            # 持久化 SKILL.md 绑定
            skillmd_bindings = self._agent_bindings.get(agent_name, [])
            if skillmd_bindings:
                key = f"{_SKILL_BINDING_PREFIX}{agent_name}"
                await redis.set(key, json.dumps(skillmd_bindings), ex=ttl)
            else:
                await redis.delete(f"{_SKILL_BINDING_PREFIX}{agent_name}")
            # 持久化内置技能绑定
            builtin_bindings = self._builtin_bindings.get(agent_name, [])
            if builtin_bindings:
                key = f"{_SKILL_BUILTIN_BINDING_PREFIX}{agent_name}"
                await redis.set(key, json.dumps(builtin_bindings), ex=ttl)
            else:
                await redis.delete(f"{_SKILL_BUILTIN_BINDING_PREFIX}{agent_name}")
        except Exception as e:
            logger.warning("绑定关系持久化失败: %s", e)

    async def restore_bindings_from_redis(self) -> int:
        """从 Redis 恢复技能绑定关系

        启动时调用，将 Redis 中持久化的绑定关系加载到内存。
        仅恢复内存中不存在的绑定（避免覆盖运行时修改）。

        Returns:
            恢复的绑定数量
        """
        try:
            import json
            from agent.core.infrastructure.redis_manager import get_redis_client
            redis = await get_redis_client()
            if redis is None:
                return 0

            restored = 0

            # 恢复 SKILL.md 绑定
            async for key in redis.scan_iter(match=f"{_SKILL_BINDING_PREFIX}*"):
                try:
                    agent_name = key.replace(_SKILL_BINDING_PREFIX, "")
                    raw = await redis.get(key)
                    if raw:
                        bindings = json.loads(raw)
                        if agent_name not in self._agent_bindings:
                            self._agent_bindings[agent_name] = bindings
                            restored += 1
                except Exception as e:
                    logger.warning("恢复 SKILL.md 绑定失败: key=%s error=%s", key, e)

            # 恢复内置技能绑定
            async for key in redis.scan_iter(match=f"{_SKILL_BUILTIN_BINDING_PREFIX}*"):
                try:
                    agent_name = key.replace(_SKILL_BUILTIN_BINDING_PREFIX, "")
                    raw = await redis.get(key)
                    if raw:
                        bindings = json.loads(raw)
                        if agent_name not in self._builtin_bindings:
                            self._builtin_bindings[agent_name] = bindings
                            restored += 1
                except Exception as e:
                    logger.warning("恢复内置技能绑定失败: key=%s error=%s", key, e)

            if restored > 0:
                logger.info("从 Redis 恢复了 %d 个技能绑定关系", restored)
            return restored
        except Exception as e:
            logger.warning("Redis 绑定恢复失败: %s", e)
            return 0

    async def check_builtin_skills_tool_availability(self) -> list[dict]:
        """检测内置技能的 required_tools 与实际可用工具的匹配情况

        遍历所有 BUILTIN_SKILLS，检查其 required_tools 是否在
        MCP 注册表或原生工具注册表中存在。不存在的工具标记为不可用。

        Returns:
            检测结果列表，每项包含 skill_id、skill_name、
            missing_tools（缺失的工具列表）和 available_tools（可用的工具列表）
        """
        results: list[dict] = []

        try:
            from agent.agents.skill_defs import BUILTIN_SKILLS
        except Exception:
            return results

        for skill_id, skill_config in BUILTIN_SKILLS.items():
            missing: list[str] = []
            available: list[str] = []

            for tool_name in skill_config.required_tools:
                if await self._is_tool_available_check(tool_name):
                    available.append(tool_name)
                else:
                    missing.append(tool_name)

            if missing:
                results.append({
                    "skill_id": skill_id,
                    "skill_name": skill_config.name,
                    "missing_tools": missing,
                    "available_tools": available,
                })
                logger.warning(
                    "内置技能 '%s'(%s) 有 %d 个工具不可用: %s",
                    skill_config.name, skill_id, len(missing), ", ".join(missing),
                )

        return results

    async def _is_tool_available_check(self, tool_name: str) -> bool:
        """检查单个工具是否在运行时可用

        依次检查原生工具注册表和 MCP 工具缓存。
        委托给同步方法 _is_tool_available，保持接口兼容。

        Args:
            tool_name: 工具名称

        Returns:
            工具是否可用
        """
        return self._is_tool_available(tool_name)

    def load_all(self) -> dict[str, SkillDocument]:
        """加载 skills/ 目录及外部目录下所有 SKILL.md 和 SkillPack

        扫描顺序：
          1. skills/ 一级子目录（现有内置和本地 skills）
          2. skills/builtin/ 一级子目录（内置 skills，与步骤1不重复因为 builtin 在 skills/ 下）
          3. skills/external/anthropic/ 一级子目录（Claude 官方 skills）
          4. skills/external/openclaw/ 一级子目录（OpenClaw 精选 skills）

        同名 Skill 以先加载者优先，后续同名 Skill 跳过并记录警告。

        同时加载 SkillPack 格式的 skill.yaml、dependencies.yaml、
        tools.yaml、tests/test_cases.yaml 等扩展文件。

        Returns:
            加载的 Skill 文档字典
        """
        if self._loaded:
            return self._skills

        # 构建扫描目录列表：主目录 + 外部目录
        scan_dirs: list[tuple[Path, str]] = []
        skills_dir = Path(self._skills_dir)
        if skills_dir.is_dir():
            scan_dirs.append((skills_dir, SKILL_SOURCE_LOCAL))
        for ext_dir_str in _EXTRA_SCAN_DIRS:
            ext_dir = Path(ext_dir_str)
            if ext_dir.is_dir():
                source = _SOURCE_DIR_MAP.get(ext_dir_str, "unknown")
                scan_dirs.append((ext_dir, source))

        if not scan_dirs:
            logger.warning("Skills 目录不存在: %s", self._skills_dir)
            self._loaded = True
            return self._skills

        for scan_dir, source in scan_dirs:
            for skill_dir in scan_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue

                try:
                    file_size = skill_md.stat().st_size
                    if file_size > SKILL_MAX_SIZE:
                        logger.warning("SKILL.md 文件过大，跳过: %s (%d bytes)", skill_md, file_size)
                        continue

                    doc = _parse_skill_md(str(skill_md))
                    name = doc.name

                    # 同名 Skill 以先加载者优先
                    if name in self._skills:
                        logger.warning(
                            "Skill 名称冲突，跳过: %s (来源=%s，已被 %s 占用)",
                            name, source, self._skills[name].manifest.source,
                        )
                        continue

                    # 填充来源信息
                    doc.manifest.source = source
                    doc.manifest.skill_dir = str(skill_dir.resolve())
                    self._skill_dir_map[name] = str(skill_dir.resolve())

                    self._skills[name] = doc

                    # 加载 SkillPack 扩展文件
                    pack_manifest = _parse_skill_yaml(skill_dir)
                    if pack_manifest:
                        self._pack_manifests[name] = pack_manifest
                        # 用 skill.yaml 的版本号覆盖 SKILL.md 的版本号
                        doc.manifest.version = pack_manifest.version
                        # 加载依赖声明
                        self._pack_dependencies[name] = _parse_dependencies_yaml(skill_dir)
                        # 加载工具绑定
                        self._pack_tools[name] = _parse_tools_yaml(skill_dir)
                        # 加载测试用例
                        self._pack_test_suites[name] = _parse_test_cases_yaml(skill_dir)
                        # 加载 Prompt 模板
                        if pack_manifest.entry.system_prompt_template:
                            self._pack_system_prompts[name] = _read_prompt_template(
                                skill_dir, pack_manifest.entry.system_prompt_template
                            )
                        if pack_manifest.entry.few_shot_file:
                            self._pack_few_shots[name] = _read_prompt_template(
                                skill_dir, pack_manifest.entry.few_shot_file
                            )
                        logger.info(
                            "加载 SkillPack: %s (version=%s, deps=%d, tools=%d, tests=%d)",
                            name, pack_manifest.version,
                            len(self._pack_dependencies[name].skills),
                            len(self._pack_tools[name].tools),
                            len(self._pack_test_suites[name].test_cases),
                        )

                    logger.info(
                        "加载 Skill: %s (category=%s, version=%s, source=%s)",
                        name, doc.manifest.category, doc.manifest.version, source,
                    )
                except SkillParseError as e:
                    logger.warning("Skill 加载失败: %s", e)
                except Exception as e:
                    logger.warning("Skill 加载异常: %s - %s", skill_dir.name, e)

        self._loaded = True
        logger.info("Skills 加载完成: 共 %d 个", len(self._skills))
        return self._skills

    def _load_skill_pack(self, skill_name: str) -> None:
        """重新加载指定技能的 SkillPack 扩展数据

        在技能保存或更新后调用，确保 SkillPack 元数据与磁盘文件同步。

        Args:
            skill_name: Skill 名称
        """
        # 优先从目录映射获取路径，回退到主目录拼接
        skill_dir_str = self._skill_dir_map.get(skill_name)
        if skill_dir_str:
            skill_dir = Path(skill_dir_str)
        else:
            skill_dir = Path(self._skills_dir) / skill_name
        if not skill_dir.is_dir():
            return

        pack_manifest = _parse_skill_yaml(skill_dir)
        if pack_manifest:
            self._pack_manifests[skill_name] = pack_manifest
            doc = self._skills.get(skill_name)
            if doc:
                doc.manifest.version = pack_manifest.version
            self._pack_dependencies[skill_name] = _parse_dependencies_yaml(skill_dir)
            self._pack_tools[skill_name] = _parse_tools_yaml(skill_dir)
            self._pack_test_suites[skill_name] = _parse_test_cases_yaml(skill_dir)
            if pack_manifest.entry.system_prompt_template:
                self._pack_system_prompts[skill_name] = _read_prompt_template(
                    skill_dir, pack_manifest.entry.system_prompt_template
                )
            if pack_manifest.entry.few_shot_file:
                self._pack_few_shots[skill_name] = _read_prompt_template(
                    skill_dir, pack_manifest.entry.few_shot_file
                )
        else:
            self._pack_manifests.pop(skill_name, None)
            self._pack_dependencies.pop(skill_name, None)
            self._pack_tools.pop(skill_name, None)
            self._pack_test_suites.pop(skill_name, None)
            self._pack_system_prompts.pop(skill_name, None)
            self._pack_few_shots.pop(skill_name, None)

    def get(self, skill_name: str) -> SkillDocument | None:
        """获取指定 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            SkillDocument 实例，未找到时返回 None
        """
        if not self._loaded:
            self.load_all()
        return self._skills.get(_normalize_skill_name(skill_name))

    def search(self, keyword: str) -> list[SkillDocument]:
        """按关键词搜索 Skill

        匹配 name、description、tags 字段。

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的 Skill 文档列表
        """
        if not self._loaded:
            self.load_all()

        keyword_lower = keyword.lower()
        results = []
        for doc in self._skills.values():
            if not doc.manifest.enabled:
                continue
            searchable = " ".join([
                doc.manifest.name,
                doc.manifest.description,
                " ".join(doc.manifest.tags),
                doc.manifest.category,
            ]).lower()
            if keyword_lower in searchable:
                results.append(doc)
        results.sort(key=lambda d: d.manifest.priority, reverse=True)
        return results

    def list_skills(self, enabled_only: bool = True) -> list[SkillDocument]:
        """列出所有 Skill

        Args:
            enabled_only: 是否仅列出已启用的 Skill

        Returns:
            Skill 文档列表
        """
        if not self._loaded:
            self.load_all()

        results = []
        for doc in self._skills.values():
            if enabled_only and not doc.manifest.enabled:
                continue
            results.append(doc)
        results.sort(key=lambda d: d.manifest.priority, reverse=True)
        return results

    def bind_to_agent(self, skill_name: str, agent_name: str) -> bool:
        """绑定 Skill 到 Agent

        Args:
            skill_name: Skill 名称
            agent_name: Agent 名称

        Returns:
            是否绑定成功
        """
        skill_name = _normalize_skill_name(skill_name)
        doc = self.get(skill_name)
        if doc is None:
            logger.warning("绑定失败: Skill %s 不存在", skill_name)
            return False

        if agent_name not in self._agent_bindings:
            self._agent_bindings[agent_name] = []
        if skill_name not in self._agent_bindings[agent_name]:
            self._agent_bindings[agent_name].append(skill_name)
            logger.info("绑定 Skill %s 到 Agent %s", skill_name, agent_name)
            # 持久化绑定关系到 Redis
            self._schedule_persist_binding(agent_name)
        return True

    def unbind_from_agent(self, skill_name: str, agent_name: str) -> bool:
        """解除 Skill 与 Agent 的绑定

        Args:
            skill_name: Skill 名称
            agent_name: Agent 名称

        Returns:
            是否解除成功
        """
        skill_name = _normalize_skill_name(skill_name)
        if agent_name in self._agent_bindings:
            bindings = self._agent_bindings[agent_name]
            if skill_name in bindings:
                bindings.remove(skill_name)
                logger.info("解除 Agent %s 的 Skill 绑定: %s", agent_name, skill_name)
                # 持久化绑定关系到 Redis
                self._schedule_persist_binding(agent_name)
                return True
        logger.warning("解除绑定失败: Agent %s 未绑定 Skill %s", agent_name, skill_name)
        return False

    def get_agent_skills(self, agent_name: str) -> list[SkillDocument]:
        """获取 Agent 绑定的所有已启用 Skill

        Args:
            agent_name: Agent 名称

        Returns:
            Skill 文档列表，按优先级降序排列
        """
        if not self._loaded:
            self.load_all()

        skill_names = self._agent_bindings.get(agent_name, [])
        skills = []
        for name in skill_names:
            doc = self._skills.get(name)
            if doc and doc.manifest.enabled:
                skills.append(doc)
        skills.sort(key=lambda d: d.manifest.priority, reverse=True)
        return skills

    async def get_agent_prompt_extensions(self, agent_name: str) -> str:
        """获取 Agent 绑定的全部技能指令拼接文本

        统一处理两类技能：
          1. 内置技能（BUILTIN_SKILLS）：从 _builtin_bindings 获取绑定关系，
             注入 prompt_extension 提示词片段
          2. SKILL.md 技能：从 _agent_bindings 获取绑定关系，
             注入 instruction 指令文本

        同时检查 SKILL.md 声明的工具是否在运行时可用，不可用时追加提示。
        检测与内置技能的冲突，冲突时在指令前加注释说明。

        Args:
            agent_name: Agent 名称

        Returns:
            技能指令拼接文本，无绑定时返回空字符串
        """
        parts: list[str] = []

        # 第一部分：内置技能的 prompt_extension
        builtin_parts = self._build_builtin_prompt_extensions(agent_name)
        if builtin_parts:
            parts.append(builtin_parts)

        # 第二部分：SKILL.md 技能的 instruction
        skillmd_parts = await self._build_skillmd_prompt_extensions(agent_name)
        if skillmd_parts:
            parts.append(skillmd_parts)

        return "\n\n".join(parts) if parts else ""

    def _build_builtin_prompt_extensions(self, agent_name: str) -> str:
        """构建内置技能的 prompt_extension 拼接文本

        从 _builtin_bindings 获取该 Agent 绑定的内置技能 ID，
        优先从已加载的 SKILL.md 获取指令文本，回退到 BUILTIN_SKILLS 的 prompt_extension。

        Args:
            agent_name: Agent 名称

        Returns:
            内置技能提示词文本，无绑定时返回空字符串
        """
        builtin_ids = self._builtin_bindings.get(agent_name, [])
        if not builtin_ids:
            return ""

        # 内置技能 ID 到 SKILL.md 名称的映射
        id_to_md_name = {
            "email_send": "email-send",
            "email_search": "email-search",
            "approval_process": "approval-process",
            "calendar_manage": "calendar-manage",
            "crm_query": "crm-query",
            "knowledge_search": "knowledge-search",
            "finance_query": "finance-query",
            "hr_query": "hr-query",
        }

        builtin_parts: list[str] = []
        for skill_id in builtin_ids:
            # 优先从 SKILL.md 获取指令
            md_name = id_to_md_name.get(skill_id, skill_id)
            md_doc = self._skills.get(md_name)
            if md_doc and md_doc.instruction:
                builtin_parts.append(f"## 内置技能: {md_doc.manifest.name}\n\n{md_doc.instruction}")
                continue

            # 回退到 BUILTIN_SKILLS 的 prompt_extension
            try:
                from agent.agents.skill_defs import BUILTIN_SKILLS
                skill = BUILTIN_SKILLS.get(skill_id)
                if skill and skill.prompt_extension:
                    builtin_parts.append(f"- {skill.name}: {skill.prompt_extension}")
            except Exception:
                pass

        if not builtin_parts:
            return ""

        return "## 内置技能提示\n\n" + "\n\n".join(builtin_parts)

    async def _build_skillmd_prompt_extensions(self, agent_name: str) -> str:
        """构建 SKILL.md 技能的 instruction 拼接文本

        从 _agent_bindings 获取该 Agent 绑定的 SKILL.md 技能名称，
        加载对应的指令文本，同时进行冲突检测和工具可用性校验。

        Args:
            agent_name: Agent 名称

        Returns:
            SKILL.md 技能指令文本，无绑定时返回空字符串
        """
        skills = self.get_agent_skills(agent_name)
        if not skills:
            return ""

        # 记录技能使用业务指标
        for doc in skills:
            try:
                from observability.metrics import record_skill_usage
                record_skill_usage(doc.manifest.name, agent_name)
            except Exception:
                pass

        # 获取该 Agent 绑定的内置技能 ID 集合，用于冲突提示
        agent_builtin_ids = set(self._builtin_bindings.get(agent_name, []))

        parts = []
        for doc in skills:
            instruction = sanitize_prompt(doc.instruction)
            if instruction:
                skill_section = f"## Skill: {doc.manifest.name}\n\n"

                # 检测与该 Agent 内置技能的冲突
                suggested_tools = doc.manifest.suggested_tools or []
                conflicts = _detect_skill_conflict(doc.manifest.name, suggested_tools)
                relevant_conflicts = [c for c in conflicts if c.builtin_skill_id in agent_builtin_ids]
                if relevant_conflicts:
                    conflict_names = ", ".join(
                        f"{c.builtin_skill_name}({c.builtin_skill_id})" for c in relevant_conflicts
                    )
                    skill_section += (
                        f"[注意] 此技能与内置技能 {conflict_names} 存在功能重叠，"
                        f"以下指令作为补充，当内置技能指令与以下指令矛盾时，以优先级更高的为准。\n\n"
                    )

                skill_section += instruction
                parts.append(skill_section)

        # 运行时工具可用性校验
        unavailable_hint = await self._build_unavailable_tools_hint(agent_name, skills)
        if unavailable_hint:
            parts.append(unavailable_hint)

        # 指令矛盾检测
        contradiction_hint = self._build_contradiction_hint(skills)
        if contradiction_hint:
            parts.append(contradiction_hint)

        return "\n\n".join(parts) if parts else ""

    def _get_agent_builtin_skill_ids(self, agent_name: str) -> set[str]:
        """获取 Agent 绑定的内置技能 ID 集合

        优先从 _builtin_bindings 获取，回退到 AGENT_SKILL_BINDINGS。

        Args:
            agent_name: Agent 名称

        Returns:
            内置技能 ID 集合
        """
        ids = self._builtin_bindings.get(agent_name, [])
        if ids:
            return set(ids)
        try:
            from agent.agents.skill_defs import AGENT_SKILL_BINDINGS
            return set(AGENT_SKILL_BINDINGS.get(agent_name, []))
        except Exception:
            return set()

    @staticmethod
    def _build_contradiction_hint(skills: list[SkillDocument]) -> str:
        """构建指令矛盾提示文本

        检测多个 Skill 之间的指令矛盾，根据严重等级生成不同级别的提示。

        Args:
            skills: Skill 列表

        Returns:
            矛盾提示文本，无矛盾时返回空字符串
        """
        try:
            conflicts = _detect_instruction_conflicts(skills)
        except Exception as e:
            logger.debug("指令矛盾检测异常（非致命）: %s", e)
            return ""

        if not conflicts:
            return ""

        # 按严重等级分组
        high_conflicts = [c for c in conflicts if c.severity == "high"]
        medium_conflicts = [c for c in conflicts if c.severity == "medium"]

        if not high_conflicts and not medium_conflicts:
            # 只有 low 级别，仅日志 WARNING，不修改 Prompt
            for c in conflicts:
                logger.warning(
                    "Skill 指令低级矛盾: %s(%s) vs %s(%s)",
                    c.skill_a, c.pattern_a_text, c.skill_b, c.pattern_b_text,
                )
            return ""

        lines = ["[指令冲突提示]"]
        for c in high_conflicts:
            lines.append(
                f"Skill \"{c.skill_a}\" 指示\"{c.pattern_a_text}\"，"
                f"但 Skill \"{c.skill_b}\" 指示\"{c.pattern_b_text}\"。"
            )

        if medium_conflicts:
            lines.append("以下为次要冲突：")
            for c in medium_conflicts:
                lines.append(
                    f"- \"{c.skill_a}\"({c.pattern_a_text}) vs \"{c.skill_b}\"({c.pattern_b_text})"
                )

        lines.append("以优先级更高的 Skill 为准。")
        return "\n".join(lines)

    async def _build_unavailable_tools_hint(self, agent_name: str, skills: list[SkillDocument]) -> str:
        """构建不可用工具提示文本

        对比 Skill 声明的 suggested_tools 与运行时实际可用工具，
        不可用时生成提示让 LLM 知道边界。

        Args:
            agent_name: Agent 名称
            skills: Agent 绑定的 Skill 列表

        Returns:
            不可用工具提示文本，全部可用时返回空字符串
        """
        # 收集所有 Skill 声明的工具
        declared_tools: set[str] = set()
        for doc in skills:
            pack = self._skill_packs.get(doc.manifest.name)
            if pack and pack.dependencies and pack.dependencies.tools:
                for dep_info in pack.dependencies.tools:
                    tool_name = dep_info.get("name", "")
                    if tool_name:
                        declared_tools.add(tool_name)
            # 也从 manifest 的 suggested_tools 收集
            if doc.manifest.suggested_tools:
                for t in doc.manifest.suggested_tools:
                    if t:
                        declared_tools.add(t)

        if not declared_tools:
            return ""

        # 获取运行时实际可用的工具名称
        try:
            from agent.tools.loader import get_available_tool_names
            available_tools = await get_available_tool_names(agent_name)
        except Exception as e:
            logger.warning("获取可用工具名称失败: %s", e)
            return ""

        # 找出不可用工具
        unavailable = declared_tools - available_tools
        if not unavailable:
            return ""

        unavailable_list = "\n".join(f"- {t}" for t in sorted(unavailable))
        return (
            "[工具可用性提示]\n"
            "以下工具当前不可用，请勿尝试调用：\n"
            f"{unavailable_list}\n\n"
            "你可以使用其他可用工具完成相关任务，或告知用户该功能暂不可用。"
        )

    def enable(self, skill_name: str) -> bool:
        """启用 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否操作成功
        """
        skill_name = _normalize_skill_name(skill_name)
        doc = self.get(skill_name)
        if doc is None:
            logger.warning("启用失败: Skill %s 不存在", skill_name)
            return False
        doc.manifest.enabled = True
        logger.info("启用 Skill: %s", skill_name)
        return True

    def disable(self, skill_name: str) -> bool:
        """禁用 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否操作成功
        """
        skill_name = _normalize_skill_name(skill_name)
        doc = self.get(skill_name)
        if doc is None:
            logger.warning("禁用失败: Skill %s 不存在", skill_name)
            return False
        doc.manifest.enabled = False
        logger.info("禁用 Skill: %s", skill_name)
        return True

    def save_skill(self, skill_name: str, content: str, check_dependencies: bool = True) -> SkillDocument:
        """保存 SKILL.md（新建或更新）

        执行 Prompt 注入检测，高风险时拒绝保存。
        本地保存时检查依赖，依赖不满足时标记 review_required 而非拒绝。

        Args:
            skill_name: Skill 名称
            content: SKILL.md 完整内容
            check_dependencies: 是否检查依赖（市场安装时由调用方单独检查）

        Returns:
            SkillDocument 实例

        Raises:
            SkillValidationError: 校验失败（注入检测高风险）
            SkillParseError: 解析失败
        """
        skill_name = _normalize_skill_name(skill_name)

        if len(content) > SKILL_MAX_SIZE:
            raise SkillValidationError(skill_name, f"SKILL.md 文件大小超过限制 ({SKILL_MAX_SIZE} bytes)")

        injection_result = _detect_prompt_injection(content)
        if injection_result["risk_level"] == "high":
            raise SkillValidationError(
                skill_name,
                f"检测到高风险 Prompt 注入模式 ({injection_result['match_count']} 个匹配)，拒绝保存",
            )

        doc = _parse_skill_md_from_content(content, skill_name)

        if injection_result["risk_level"] == "medium":
            doc.manifest.review_required = True
            logger.warning("Skill %s 检测到中等风险注入模式，已标记 review-required", skill_name)

        skill_dir = Path(self._skills_dir) / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_md_path = skill_dir / "SKILL.md"
        with open(str(skill_md_path), "w", encoding="utf-8") as f:
            f.write(content)

        now = datetime.now().isoformat()
        if not doc.manifest.created_at:
            doc.manifest.created_at = now
        doc.manifest.updated_at = now

        # 更新来源信息
        doc.manifest.source = SKILL_SOURCE_LOCAL
        doc.manifest.skill_dir = str(skill_dir.resolve())
        self._skill_dir_map[skill_name] = str(skill_dir.resolve())

        self._skills[skill_name] = doc

        # 重新加载 SkillPack 扩展数据
        self._load_skill_pack(skill_name)

        # 本地保存时检查依赖，依赖不满足时标记 review_required（软提示，不阻断开发流程）
        if check_dependencies:
            self._check_dependencies_on_save(skill_name, doc)

        # 检测与内置技能的名称/语义冲突
        self._check_skill_conflict_on_save(skill_name, doc)

        logger.info("保存 Skill: %s (version=%s)", skill_name, doc.manifest.version)
        return doc

    def _check_dependencies_on_save(self, skill_name: str, doc: SkillDocument) -> None:
        """本地保存时检查依赖，依赖不满足时标记 review_required

        与市场安装的硬拦截不同，本地保存采用软提示策略，
        不阻断开发流程，但标记需要审核。

        Args:
            skill_name: Skill 名称
            doc: SkillDocument 实例
        """
        try:
            dependency = self.get_pack_dependencies(skill_name)
            if not dependency.tools and not dependency.mcp_servers and not dependency.skills:
                return

            missing: list[str] = []

            for dep_info in dependency.skills:
                dep_name = dep_info.get("name", "")
                required = dep_info.get("required", True)
                if required and self.get(dep_name) is None:
                    missing.append(f"技能:{dep_name}")

            for dep_info in dependency.tools:
                tool_name = dep_info.get("name", "")
                required = dep_info.get("required", True)
                if required and not self._is_tool_available(tool_name):
                    missing.append(f"工具:{tool_name}")

            for server_name in dependency.mcp_servers:
                if not self._is_mcp_server_available(server_name):
                    missing.append(f"MCP服务:{server_name}")

            if missing:
                doc.manifest.review_required = True
                logger.warning(
                    "Skill %s 依赖不满足，已标记 review-required: %s",
                    skill_name, ", ".join(missing),
                )
        except Exception as e:
            logger.debug("Skill %s 依赖检查异常（非致命）: %s", skill_name, e)

    def _check_skill_conflict_on_save(self, skill_name: str, doc: SkillDocument) -> None:
        """保存时检测与内置技能/MCP工具的名称/语义冲突

        检测到冲突时标记 review_required 并记录日志，不阻断保存流程。
        检测三层冲突：
          1. 与内置技能的名称/语义冲突（已有逻辑）
          2. 与 MCP 工具名的冲突（SKILL.md 名称与 MCP 工具同名会导致混淆）
          3. 与原生工具名的冲突（SKILL.md 名称规范化后与 native_ 工具同名）

        Args:
            skill_name: Skill 名称
            doc: SkillDocument 实例
        """
        try:
            suggested_tools = doc.manifest.suggested_tools or []
            conflicts = _detect_skill_conflict(skill_name, suggested_tools)
            if conflicts:
                doc.manifest.review_required = True
                for c in conflicts:
                    logger.warning(
                        "Skill %s 与内置技能 %s(%s) 存在冲突 [%s]: %s",
                        skill_name, c.builtin_skill_id, c.builtin_skill_name,
                        c.conflict_type, c.detail,
                    )

            # 检测与 MCP 工具名的冲突
            self._check_mcp_tool_name_conflict(skill_name, doc)

            # 检测与原生工具名的冲突
            self._check_native_tool_name_conflict(skill_name, doc)

        except Exception as e:
            logger.debug("Skill %s 冲突检测异常（非致命）: %s", skill_name, e)

    def _check_mcp_tool_name_conflict(self, skill_name: str, doc: SkillDocument) -> None:
        """检测 SKILL.md 名称与 MCP 工具名冲突

        SKILL.md 名称规范化后（连字符转下划线）如果与某个 MCP 工具名相同，
        在 Prompt 中可能造成混淆：Agent 分不清"技能名"和"工具名"。
        使用 MCP 工具反向索引进行 O(1) 查找，避免全量遍历。

        Args:
            skill_name: Skill 名称
            doc: SkillDocument 实例
        """
        try:
            from agent.core.mcp.mcp_integration import get_tool_server_name
            normalized = skill_name.replace("-", "_")
            server_name = get_tool_server_name(normalized)
            if server_name:
                doc.manifest.review_required = True
                logger.warning(
                    "Skill %s 规范化名称 '%s' 与 MCP 工具同名(服务: %s)，"
                    "可能导致 Prompt 中技能名与工具名混淆",
                    skill_name, normalized, server_name,
                )
        except Exception as e:
            logger.debug("MCP 工具名冲突检测异常: %s", e)

    def _check_native_tool_name_conflict(self, skill_name: str, doc: SkillDocument) -> None:
        """检测 SKILL.md 名称与原生工具名冲突

        SKILL.md 名称规范化后如果与 native_ 前缀工具同名（去掉前缀后），
        可能造成命名空间混淆。

        Args:
            skill_name: Skill 名称
            doc: SkillDocument 实例
        """
        try:
            from agent.tools.registry import get_native_tool_registry
            registry = get_native_tool_registry()
            normalized = skill_name.replace("-", "_")
            for meta in registry.list_tools():
                bare_name = meta.name.replace("native_", "", 1)
                if bare_name == normalized:
                    doc.manifest.review_required = True
                    logger.warning(
                        "Skill %s 规范化名称 '%s' 与原生工具 '%s' 去前缀后同名，"
                        "可能导致命名空间混淆",
                        skill_name, normalized, meta.name,
                    )
                    return
        except Exception as e:
            logger.debug("原生工具名冲突检测异常（非致命）: %s", e)

    @staticmethod
    def _is_tool_available(tool_name: str) -> bool:
        """检查工具是否在原生工具注册表或 MCP 工具缓存中可用

        Args:
            tool_name: 工具名称

        Returns:
            是否可用
        """
        try:
            from agent.tools.registry import get_native_tool_registry
            tool_reg = get_native_tool_registry()
            if tool_reg and tool_reg.get(tool_name):
                return True
        except Exception:
            pass

        try:
            from agent.core.mcp.mcp_integration import get_tool_server_name
            if get_tool_server_name(tool_name) is not None:
                return True
        except Exception:
            pass

        return False

    @staticmethod
    def _is_mcp_server_available(server_name: str) -> bool:
        """检查 MCP Server 是否在注册表中可用

        Args:
            server_name: MCP Server 名称

        Returns:
            是否可用
        """
        try:
            from agent.core.mcp.mcp_integration import MCP_SERVER_REGISTRY
            return server_name in MCP_SERVER_REGISTRY
        except Exception:
            return False

    def delete(self, skill_name: str) -> bool:
        """删除 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            是否删除成功
        """
        skill_name = _normalize_skill_name(skill_name)
        if skill_name not in self._skills:
            logger.warning("删除失败: Skill %s 不存在", skill_name)
            return False

        skill_dir = Path(self._skills_dir) / skill_name
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            skill_md.unlink()

        del self._skills[skill_name]

        # 清理 SkillPack 扩展数据
        self._pack_manifests.pop(skill_name, None)
        self._pack_dependencies.pop(skill_name, None)
        self._pack_tools.pop(skill_name, None)
        self._pack_test_suites.pop(skill_name, None)
        self._pack_system_prompts.pop(skill_name, None)
        self._pack_few_shots.pop(skill_name, None)
        self._active_versions.pop(skill_name, None)
        self._marketplace.pop(skill_name, None)

        for agent_name in list(self._agent_bindings.keys()):
            bindings = self._agent_bindings[agent_name]
            if skill_name in bindings:
                bindings.remove(skill_name)

        for session_id in list(self._active_skills.keys()):
            self._active_skills[session_id].discard(skill_name)

        logger.info("删除 Skill: %s", skill_name)
        return True

    def export_agent_as_skill(self, agent_name: str) -> str | None:
        """导出 Agent 为 SKILL.md 格式

        Args:
            agent_name: Agent 名称

        Returns:
            SKILL.md 内容字符串，Agent 不存在时返回 None
        """
        from agent.agents.domain import AGENT_PROMPTS, get_agent_skills

        prompt = AGENT_PROMPTS.get(agent_name)
        if prompt is None:
            logger.warning("导出失败: Agent %s 不存在", agent_name)
            return None

        skills = get_agent_skills(agent_name)
        tool_names = []
        for skill in skills:
            tool_names.extend(skill.required_tools)
        tool_names = list(dict.fromkeys(tool_names))

        description = prompt[:200].replace("\n", " ").strip()
        if len(prompt) > 200:
            description += "..."

        content = (
            f"---\n"
            f"name: {agent_name.lower().replace('agent', '').strip() or agent_name.lower()}\n"
            f"description: \"{description}\"\n"
            f"version: \"1.0.0\"\n"
            f"author: system\n"
            f"category: exported\n"
            f"tags: [exported, {agent_name.lower()}]\n"
            f"priority: 5\n"
            f"review-required: false\n"
            f"collaboration-mode: direct\n"
            f"suggested-tools: {tool_names}\n"
            f"---\n\n"
            f"# {agent_name} Skill\n\n"
            f"{prompt}\n"
        )
        return content

    def activate_skill(self, session_id: str, skill_name: str) -> bool:
        """标记 Skill 为当前会话已激活

        Args:
            session_id: 会话 ID
            skill_name: Skill 名称

        Returns:
            是否激活成功
        """
        skill_name = _normalize_skill_name(skill_name)
        doc = self.get(skill_name)
        if doc is None:
            logger.warning("激活失败: Skill %s 不存在", skill_name)
            return False

        if session_id not in self._active_skills:
            self._active_skills[session_id] = set()
        self._active_skills[session_id].add(skill_name)
        logger.info("会话 %s 激活 Skill: %s", session_id, skill_name)
        return True

    def deactivate_skill(self, session_id: str, skill_name: str) -> bool:
        """标记 Skill 为当前会话已卸载

        Args:
            session_id: 会话 ID
            skill_name: Skill 名称

        Returns:
            是否卸载成功
        """
        skill_name = _normalize_skill_name(skill_name)
        if session_id in self._active_skills:
            if skill_name in self._active_skills[session_id]:
                self._active_skills[session_id].discard(skill_name)
                logger.info("会话 %s 卸载 Skill: %s", session_id, skill_name)
                return True
        logger.warning("卸载失败: 会话 %s 未激活 Skill %s", session_id, skill_name)
        return False

    def get_active_skills(self, session_id: str) -> set[str]:
        """获取当前会话已激活的 Skill 集合

        Args:
            session_id: 会话 ID

        Returns:
            已激活的 Skill 名称集合
        """
        return self._active_skills.get(session_id, set()).copy()

    def get_skill_raw_content(self, skill_name: str) -> str | None:
        """获取 SKILL.md 原始内容

        Args:
            skill_name: Skill 名称

        Returns:
            SKILL.md 原始内容，未找到时返回 None
        """
        skill_name = _normalize_skill_name(skill_name)
        # 优先从目录映射获取路径
        skill_dir_str = self._skill_dir_map.get(skill_name)
        if skill_dir_str:
            skill_md = Path(skill_dir_str) / "SKILL.md"
        else:
            skill_md = Path(self._skills_dir) / skill_name / "SKILL.md"
        if not skill_md.is_file():
            return None
        try:
            return skill_md.read_text(encoding="utf-8")
        except OSError:
            return None

    def get_skill_dir(self, skill_name: str) -> str | None:
        """获取 Skill 目录的绝对路径

        Args:
            skill_name: Skill 名称

        Returns:
            Skill 目录绝对路径，未找到时返回 None
        """
        if not self._loaded:
            self.load_all()
        skill_name = _normalize_skill_name(skill_name)
        return self._skill_dir_map.get(skill_name)

    def get_skill_scripts_dir(self, skill_name: str) -> str | None:
        """获取 Skill 的 scripts 目录路径

        外部 Skills（如 Claude 的 docx/xlsx 等）在 scripts/ 子目录下
        存放可执行的 Python 脚本。

        Args:
            skill_name: Skill 名称

        Returns:
            scripts 目录绝对路径，不存在时返回 None
        """
        skill_dir = self.get_skill_dir(skill_name)
        if not skill_dir:
            return None
        scripts_dir = Path(skill_dir) / "scripts"
        if scripts_dir.is_dir():
            return str(scripts_dir)
        return None

    # ==================== SkillPack 查询 ====================

    def get_pack_manifest(self, skill_name: str) -> SkillPackManifest | None:
        """获取技能的 SkillPack 元数据

        Args:
            skill_name: Skill 名称

        Returns:
            SkillPackManifest 实例，不存在时返回 None
        """
        if not self._loaded:
            self.load_all()
        return self._pack_manifests.get(_normalize_skill_name(skill_name))

    def get_pack_dependencies(self, skill_name: str) -> SkillDependency:
        """获取技能的依赖声明

        Args:
            skill_name: Skill 名称

        Returns:
            SkillDependency 实例，不存在时返回空依赖
        """
        if not self._loaded:
            self.load_all()
        return self._pack_dependencies.get(_normalize_skill_name(skill_name), SkillDependency())

    def get_pack_tools(self, skill_name: str) -> ToolsConfig:
        """获取技能的工具绑定配置

        Args:
            skill_name: Skill 名称

        Returns:
            ToolsConfig 实例，不存在时返回空配置
        """
        if not self._loaded:
            self.load_all()
        return self._pack_tools.get(_normalize_skill_name(skill_name), ToolsConfig())

    def get_pack_test_suite(self, skill_name: str) -> SkillTestSuite:
        """获取技能的测试套件

        Args:
            skill_name: Skill 名称

        Returns:
            SkillTestSuite 实例，不存在时返回空测试套件
        """
        if not self._loaded:
            self.load_all()
        return self._pack_test_suites.get(_normalize_skill_name(skill_name), SkillTestSuite())

    def get_pack_system_prompt(self, skill_name: str) -> str:
        """获取技能的系统 Prompt 模板

        Args:
            skill_name: Skill 名称

        Returns:
            系统 Prompt 模板内容，不存在时返回空字符串
        """
        if not self._loaded:
            self.load_all()
        return self._pack_system_prompts.get(_normalize_skill_name(skill_name), "")

    def get_pack_few_shots(self, skill_name: str) -> str:
        """获取技能的 Few-shot 示例

        Args:
            skill_name: Skill 名称

        Returns:
            Few-shot 示例内容，不存在时返回空字符串
        """
        if not self._loaded:
            self.load_all()
        return self._pack_few_shots.get(_normalize_skill_name(skill_name), "")

    # ==================== 版本管理 ====================

    async def _get_redis(self) -> Any:
        """获取 Redis 客户端"""
        try:
            from agent.core.infrastructure.redis_manager import get_redis_client
            return await get_redis_client()
        except Exception as e:
            logger.warning("Redis 获取失败: %s", e)
            return None

    async def publish_skill_version(self, skill_name: str, version: str, content: str | None = None) -> bool:
        """发布技能新版本

        将技能的当前 skill.yaml 元数据持久化到 Redis，记录版本历史。

        Args:
            skill_name: Skill 名称
            version: 版本号
            content: skill.yaml 内容（可选，不传时使用当前内存中的数据）

        Returns:
            是否发布成功
        """
        skill_name = _normalize_skill_name(skill_name)
        redis = await self._get_redis()
        if redis is None:
            logger.warning("Redis 不可用，版本发布失败: %s", skill_name)
            return False

        pack_manifest = self._pack_manifests.get(skill_name)
        if pack_manifest is None:
            logger.warning("版本发布失败: Skill %s 无 SkillPack 元数据", skill_name)
            return False

        try:
            version_data = pack_manifest.model_dump()
            if content:
                version_data["custom_content"] = content

            from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
            ttl = get_persist_ttl_seconds()
            version_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:{version}:manifest"
            versions_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:versions"
            latest_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:latest"

            pipe = redis.pipeline()
            pipe.set(version_key, json.dumps(version_data, ensure_ascii=False), ex=ttl)
            pipe.zadd(versions_key, {version: time.time()})
            pipe.set(latest_key, version, ex=ttl)
            await pipe.execute()

            # 更新活跃版本
            self._active_versions[skill_name] = version

            logger.info("技能版本发布成功: %s@%s", skill_name, version)
            return True
        except Exception as e:
            logger.error("技能版本发布失败: %s@%s - %s", skill_name, version, e)
            return False

    async def activate_skill_version(self, skill_name: str, version: str) -> bool:
        """激活指定版本

        Args:
            skill_name: Skill 名称
            version: 版本号

        Returns:
            是否激活成功
        """
        skill_name = _normalize_skill_name(skill_name)
        redis = await self._get_redis()
        if redis is None:
            return False

        try:
            version_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:{version}:manifest"
            active_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:active_version"

            manifest_raw = await redis.get(version_key)
            if manifest_raw is None:
                logger.warning("版本不存在: %s@%s", skill_name, version)
                return False

            from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
            await redis.set(active_key, version, ex=get_persist_ttl_seconds())
            self._active_versions[skill_name] = version

            logger.info("技能版本激活成功: %s@%s", skill_name, version)
            return True
        except Exception as e:
            logger.error("技能版本激活失败: %s@%s - %s", skill_name, version, e)
            return False

    async def rollback_skill(self, skill_name: str) -> str | None:
        """回滚到上一版本

        Args:
            skill_name: Skill 名称

        Returns:
            回滚后的版本号，失败时返回 None
        """
        skill_name = _normalize_skill_name(skill_name)
        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            versions_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:versions"
            versions = await redis.zrange(versions_key, 0, -1, withscores=True)

            if len(versions) < 2:
                logger.warning("版本回滚失败: %s 不足2个版本", skill_name)
                return None

            # 取倒数第二个版本
            prev_version = versions[-2][0]
            if isinstance(prev_version, bytes):
                prev_version = prev_version.decode("utf-8")

            success = await self.activate_skill_version(skill_name, prev_version)
            if success:
                logger.info("技能版本回滚成功: %s -> %s", skill_name, prev_version)
                return prev_version

            return None
        except Exception as e:
            logger.error("技能版本回滚失败: %s - %s", skill_name, e)
            return None

    async def list_versions(self, skill_name: str) -> list[dict[str, Any]]:
        """列出技能的所有版本

        Args:
            skill_name: Skill 名称

        Returns:
            版本列表，每个元素包含 version 和 timestamp
        """
        skill_name = _normalize_skill_name(skill_name)
        redis = await self._get_redis()
        if redis is None:
            return []

        try:
            versions_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:versions"
            versions = await redis.zrange(versions_key, 0, -1, withscores=True)

            active_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:active_version"
            active_version = await redis.get(active_key)
            if isinstance(active_version, bytes):
                active_version = active_version.decode("utf-8")

            result = []
            for version, timestamp in reversed(versions):
                if isinstance(version, bytes):
                    version = version.decode("utf-8")
                result.append({
                    "version": version,
                    "timestamp": timestamp,
                    "is_active": version == active_version,
                })

            return result
        except Exception as e:
            logger.error("获取版本列表失败: %s - %s", skill_name, e)
            return []

    async def get_active_version(self, skill_name: str) -> str | None:
        """获取技能的当前激活版本

        Args:
            skill_name: Skill 名称

        Returns:
            激活版本号，不存在时返回 None
        """
        skill_name = _normalize_skill_name(skill_name)

        # 优先从内存缓存获取
        if skill_name in self._active_versions:
            return self._active_versions[skill_name]

        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            active_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:active_version"
            version = await redis.get(active_key)
            if isinstance(version, bytes):
                version = version.decode("utf-8")
            if version:
                self._active_versions[skill_name] = version
            return version
        except Exception:
            return None

    # ==================== 技能市场 ====================

    async def publish_to_marketplace(self, skill_name: str, category: str = "general") -> MarketplaceEntry | None:
        """发布技能到市场

        将技能上架到市场，同时持久化到 Redis。

        Args:
            skill_name: Skill 名称
            category: 市场分类

        Returns:
            MarketplaceEntry 实例，失败时返回 None
        """
        skill_name = _normalize_skill_name(skill_name)
        if not self._loaded:
            self.load_all()

        doc = self._skills.get(skill_name)
        if doc is None:
            logger.warning("市场发布失败: Skill %s 不存在", skill_name)
            return None

        entry = MarketplaceEntry(
            skill_name=skill_name,
            version=doc.manifest.version,
            description=doc.manifest.description,
            author=doc.manifest.author,
            category=category,
            tags=doc.manifest.tags,
            published_at=time.time(),
        )

        self._marketplace[skill_name] = entry

        # 持久化到 Redis
        redis = await self._get_redis()
        if redis is not None:
            try:
                market_key = f"{_SKILL_MARKET_PREFIX}{skill_name}"
                pipe = redis.pipeline()
                from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
                pipe.set(market_key, entry.model_dump_json(), ex=get_persist_ttl_seconds())
                pipe.sadd(_SKILL_MARKET_SET_KEY, skill_name)
                pipe.sadd(f"{_SKILL_MARKET_CATEGORY_PREFIX}{category}", skill_name)
                await pipe.execute()
            except Exception as e:
                logger.debug("市场数据持久化失败: %s - %s", skill_name, e)

        logger.info("技能已发布到市场: %s (category=%s)", skill_name, category)
        return entry

    async def unpublish_from_marketplace(self, skill_name: str) -> bool:
        """从市场下架技能

        Args:
            skill_name: Skill 名称

        Returns:
            是否下架成功
        """
        skill_name = _normalize_skill_name(skill_name)
        entry = self._marketplace.pop(skill_name, None)
        if entry is None:
            return False

        redis = await self._get_redis()
        if redis is not None:
            try:
                pipe = redis.pipeline()
                pipe.delete(f"{_SKILL_MARKET_PREFIX}{skill_name}")
                pipe.srem(_SKILL_MARKET_SET_KEY, skill_name)
                pipe.srem(f"{_SKILL_MARKET_CATEGORY_PREFIX}{entry.category}", skill_name)
                pipe.delete(f"{_SKILL_MARKET_RATINGS_PREFIX}{skill_name}")
                await pipe.execute()
            except Exception as e:
                logger.debug("市场数据删除失败: %s - %s", skill_name, e)

        logger.info("技能已从市场下架: %s", skill_name)
        return True

    async def search_marketplace(
        self,
        keyword: str = "",
        category: str = "",
        sort_by: str = "rating",
    ) -> list[MarketplaceEntry]:
        """搜索技能市场

        支持按关键词和分类搜索，结果按评分或下载量排序。
        优先从 Redis 加载市场数据，Redis 不可用时使用内存缓存。

        Args:
            keyword: 搜索关键词
            category: 分类过滤
            sort_by: 排序方式（rating/downloads/name）

        Returns:
            市场条目列表
        """
        # 尝试从 Redis 加载市场数据
        await self._load_marketplace_from_redis()

        entries = list(self._marketplace.values())

        if category:
            entries = [e for e in entries if e.category == category]

        if keyword:
            kw_lower = keyword.lower()
            entries = [
                e for e in entries
                if kw_lower in e.skill_name.lower()
                or kw_lower in e.description.lower()
                or kw_lower in " ".join(e.tags).lower()
            ]

        if sort_by == "rating":
            entries.sort(key=lambda e: (-e.rating, -e.downloads))
        elif sort_by == "downloads":
            entries.sort(key=lambda e: (-e.downloads, -e.rating))
        elif sort_by == "name":
            entries.sort(key=lambda e: e.skill_name)

        return entries

    async def install_from_marketplace(self, skill_name: str, target_version: str = "", skip_dependency_check: bool = False) -> SkillDocument | None:
        """从市场安装技能

        增加下载量计数，将技能注册到本地仓库。
        安装前会检查依赖是否满足，必需依赖不满足时拒绝安装。

        Args:
            skill_name: Skill 名称
            target_version: 目标版本号（空表示最新版本）
            skip_dependency_check: 跳过依赖检查（仅用于内部降级场景）

        Returns:
            SkillDocument 实例，失败时返回 None

        Raises:
            SkillDependencyError: 必需依赖不满足时抛出
        """
        skill_name = _normalize_skill_name(skill_name)

        # 确保市场数据已加载
        await self._load_marketplace_from_redis()

        entry = self._marketplace.get(skill_name)
        if entry is None:
            logger.warning("市场安装失败: Skill %s 不在市场中", skill_name)
            return None

        # 增加下载量
        entry.downloads += 1

        # 持久化下载量到 Redis
        redis = await self._get_redis()
        if redis is not None:
            try:
                market_key = f"{_SKILL_MARKET_PREFIX}{skill_name}"
                from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
                await redis.set(market_key, entry.model_dump_json(), ex=get_persist_ttl_seconds())
            except Exception as e:
                logger.debug("市场下载量更新失败: %s - %s", skill_name, e)

        # 如果本地已有该技能，增加下载量后直接返回
        if skill_name in self._skills:
            return self._skills[skill_name]

        # 从 Redis 加载技能版本内容并注册到本地仓库
        redis = await self._get_redis()
        if redis is not None:
            try:
                version_to_install = target_version or entry.version
                version_key = f"{_SKILL_VERSION_PREFIX}{skill_name}"
                version_content = await redis.hget(version_key, version_to_install)
                if version_content:
                    # 先保存到本地（依赖检查需要读取 SkillPack 文件）
                    # check_dependencies=False: 市场安装的依赖检查由本方法自行处理
                    doc = self.save_skill(skill_name=skill_name, content=version_content, check_dependencies=False)

                    # 安装前依赖检查
                    if not skip_dependency_check:
                        dep_result = await self._check_dependencies_before_install(skill_name)
                        if not dep_result.resolvable:
                            # 必需依赖不满足，回滚：删除已保存的技能
                            self.delete(skill_name)
                            raise SkillDependencyError(
                                skill_name=skill_name,
                                missing_required=dep_result.missing_required,
                                missing_optional=dep_result.missing_optional,
                            )
                        if dep_result.missing_optional:
                            logger.warning(
                                "Skill %s 缺少可选依赖: %s，将降级运行",
                                skill_name, ", ".join(dep_result.missing_optional),
                            )

                    logger.info("从市场安装技能成功: %s (version=%s)", skill_name, version_to_install)
                    return doc
                else:
                    logger.warning("市场安装失败: Skill %s@%s 版本内容不存在", skill_name, version_to_install)
            except SkillDependencyError:
                raise
            except Exception as e:
                logger.error("从市场安装技能异常: %s - %s", skill_name, e)

        logger.warning("从市场安装技能失败: %s (version=%s)", skill_name, target_version or entry.version)
        return None

    async def _check_dependencies_before_install(self, skill_name: str) -> "DependencyResolutionResult":
        """安装前检查技能依赖是否满足

        复用 skill_resolver 的 resolve_dependencies 进行依赖解析。

        Args:
            skill_name: Skill 名称

        Returns:
            DependencyResolutionResult 依赖解析结果
        """
        try:
            from agent.core.skill.skill_resolver import resolve_dependencies
            return await resolve_dependencies(skill_name)
        except Exception as e:
            logger.warning("Skill %s 依赖检查异常，视为无依赖: %s", skill_name, e)
            from agent.core.skill.skill_resolver import DependencyResolutionResult
            return DependencyResolutionResult(skill_name=skill_name, resolvable=True)

    async def rate_skill(self, skill_name: str, user_id: str, score: float, comment: str = "") -> bool:
        """为技能评分

        使用 Redis Sorted Set 存储评分数据，自动计算平均分。

        Args:
            skill_name: Skill 名称
            user_id: 用户ID
            score: 评分（1.0~5.0）
            comment: 评价内容

        Returns:
            是否评分成功
        """
        skill_name = _normalize_skill_name(skill_name)
        if score < 1.0 or score > 5.0:
            logger.warning("评分超出范围: %s score=%.1f", skill_name, score)
            return False

        redis = await self._get_redis()
        if redis is None:
            return False

        try:
            ratings_key = f"{_SKILL_MARKET_RATINGS_PREFIX}{skill_name}"

            # 先删除该用户之前的评分记录，确保每个用户只能评一次分
            existing_ratings = await redis.zrange(ratings_key, 0, -1)
            for rating_raw in existing_ratings:
                try:
                    data = json.loads(rating_raw)
                    if data.get("user_id") == user_id:
                        await redis.zrem(ratings_key, rating_raw)
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

            rating_data = json.dumps({
                "user_id": user_id,
                "score": score,
                "comment": comment,
                "timestamp": time.time(),
            }, ensure_ascii=False)

            await redis.zadd(ratings_key, {rating_data: time.time()})

            # 计算新的平均分
            all_ratings = await redis.zrange(ratings_key, 0, -1)
            total_score = 0.0
            count = 0
            for rating_raw in all_ratings:
                try:
                    data = json.loads(rating_raw)
                    total_score += data.get("score", 0)
                    count += 1
                except (json.JSONDecodeError, TypeError):
                    continue

            avg_rating = round(total_score / count, 2) if count > 0 else 0

            # 更新市场条目
            entry = self._marketplace.get(skill_name)
            if entry:
                entry.rating = avg_rating
                entry.rating_count = count
                # 持久化更新
                market_key = f"{_SKILL_MARKET_PREFIX}{skill_name}"
                from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
                await redis.set(market_key, entry.model_dump_json(), ex=get_persist_ttl_seconds())

            logger.info("技能评分成功: %s user=%s score=%.1f avg=%.2f", skill_name, user_id, score, avg_rating)
            return True
        except Exception as e:
            logger.error("技能评分失败: %s - %s", skill_name, e)
            return False

    async def _load_marketplace_from_redis(self) -> None:
        """从 Redis 加载市场数据到内存缓存

        使用 60 秒 TTL 缓存，避免频繁请求 Redis。
        """
        now = time.time()
        if now - self._marketplace_loaded_at < 60 and self._marketplace:
            return

        redis = await self._get_redis()
        if redis is None:
            return

        try:
            skill_ids = await redis.smembers(_SKILL_MARKET_SET_KEY)
            if not skill_ids:
                return

            for skill_id in skill_ids:
                if isinstance(skill_id, bytes):
                    skill_id = skill_id.decode("utf-8")

                market_raw = await redis.get(f"{_SKILL_MARKET_PREFIX}{skill_id}")
                if market_raw is None:
                    continue

                try:
                    entry = MarketplaceEntry.model_validate_json(market_raw)
                    self._marketplace[skill_id] = entry
                except Exception:
                    continue

            self._marketplace_loaded_at = now
        except Exception as e:
            logger.debug("市场数据加载失败: %s", e)

    # ==================== 技能测试框架 ====================

    async def run_skill_tests(self, skill_name: str) -> SkillTestReport:
        """运行技能测试用例

        执行技能包中定义的测试用例，验证意图路由、工具调用和输出内容。

        Args:
            skill_name: Skill 名称

        Returns:
            SkillTestReport 测试报告
        """
        skill_name = _normalize_skill_name(skill_name)
        if not self._loaded:
            self.load_all()

        test_suite = self._pack_test_suites.get(skill_name, SkillTestSuite())
        doc = self._skills.get(skill_name)

        report = SkillTestReport(
            skill_name=skill_name,
            version=doc.manifest.version if doc else "",
            executed_at=datetime.now().isoformat(),
        )

        if not test_suite.test_cases:
            logger.info("技能 %s 无测试用例", skill_name)
            return report

        for test_case in test_suite.test_cases:
            result = await self._execute_test_case(skill_name, test_case)
            report.results.append(result)
            report.total += 1
            if result.passed:
                report.passed += 1
            else:
                report.failed += 1

        # 持久化测试结果到 Redis
        await self._persist_test_report(skill_name, report)

        logger.info(
            "技能测试完成: %s total=%d passed=%d failed=%d",
            skill_name, report.total, report.passed, report.failed,
        )
        return report

    async def _execute_test_case(self, skill_name: str, test_case: SkillTestCase) -> SkillTestResult:
        """执行单个测试用例

        模拟用户输入，通过路由引擎验证意图识别和工具调用。

        Args:
            skill_name: Skill 名称
            test_case: 测试用例

        Returns:
            SkillTestResult 测试结果
        """
        start_time = time.time()
        result = SkillTestResult(test_name=test_case.name, passed=False)
        session = None

        try:
            from agent.teams.routing import route_and_execute
            from agent.core.session.session_manager import get_session_manager

            session_mgr = await get_session_manager()
            session = await session_mgr.create_session(
                user_id="skill-test",
                channel="test",
            )

            execution_result = await route_and_execute(
                user_message=test_case.input,
                session_id=session.session_id,
                user_id="skill-test",
            )

            elapsed_ms = (time.time() - start_time) * 1000
            result.execution_time_ms = round(elapsed_ms, 2)

            # 验证意图
            if test_case.expected_intent:
                actual_intent = execution_result.get("intent", "")
                result.actual_intent = actual_intent
                if actual_intent != test_case.expected_intent:
                    result.error_message = f"意图不匹配: 期望={test_case.expected_intent}, 实际={actual_intent}"
                    return result

            # 验证工具调用
            if test_case.expected_tools:
                actual_tools = execution_result.get("tools_used", [])
                result.actual_tools = actual_tools
                for expected_tool in test_case.expected_tools:
                    if expected_tool not in actual_tools:
                        result.error_message = f"缺少工具调用: {expected_tool}"
                        return result

            # 验证响应内容
            if test_case.expected_response_contains:
                response_text = execution_result.get("message", "")
                for keyword in test_case.expected_response_contains:
                    if keyword not in response_text:
                        result.error_message = f"响应缺少关键词: {keyword}"
                        return result

            result.passed = True

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            result.execution_time_ms = round(elapsed_ms, 2)
            result.error_message = str(e)
        finally:
            if session is not None:
                try:
                    from agent.core.session.session_manager import get_session_manager
                    session_mgr = await get_session_manager()
                    await session_mgr.delete_session(session.session_id)
                except Exception:
                    pass

        return result

    async def _persist_test_report(self, skill_name: str, report: SkillTestReport) -> None:
        """持久化测试报告到 Redis

        Args:
            skill_name: Skill 名称
            report: 测试报告
        """
        redis = await self._get_redis()
        if redis is None:
            return

        try:
            version = report.version or "unknown"
            test_key = f"{_SKILL_TEST_PREFIX}{skill_name}:{version}:results"
            from agent.core.infrastructure.async_utils import get_persist_ttl_seconds
            await redis.set(
                test_key,
                report.model_dump_json(),
                ex=get_persist_ttl_seconds(),
            )
        except Exception as e:
            logger.debug("测试报告持久化失败: %s - %s", skill_name, e)


def _parse_skill_md_from_content(content: str, skill_name: str = "unknown") -> SkillDocument:
    """从内容字符串解析 SKILL.md

    Args:
        content: SKILL.md 内容
        skill_name: 用于错误提示的名称

    Returns:
        SkillDocument 实例

    Raises:
        SkillParseError: 解析失败
    """
    if not content.strip():
        raise SkillParseError(skill_name, reason="内容为空")

    front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not front_matter_match:
        raise SkillParseError(skill_name, reason="缺少 YAML Front Matter（--- 分隔符）")

    yaml_str = front_matter_match.group(1)
    instruction = front_matter_match.group(2).strip()

    try:
        raw_meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise SkillParseError(skill_name, reason=f"YAML 解析错误: {e}")

    if not isinstance(raw_meta, dict):
        raise SkillParseError(skill_name, reason="YAML Front Matter 必须是键值对格式")

    raw_meta = _normalize_yaml_keys(raw_meta)

    if "name" not in raw_meta:
        raise SkillParseError(skill_name, reason="缺少必填字段: name")

    if "description" not in raw_meta:
        raise SkillParseError(skill_name, reason="缺少必填字段: description")

    raw_meta["name"] = _normalize_skill_name(str(raw_meta["name"]))

    if not SKILL_NAME_PATTERN.match(raw_meta["name"]):
        raise SkillParseError(
            skill_name,
            reason=f"Skill 名称不规范: {raw_meta['name']}，仅允许小写字母、数字和连字符",
        )

    try:
        manifest = SkillManifest(**raw_meta)
    except Exception as e:
        raise SkillParseError(skill_name, reason=f"字段校验失败: {e}")

    return SkillDocument(manifest=manifest, instruction=instruction)
