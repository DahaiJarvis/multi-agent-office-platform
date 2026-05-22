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

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "skills")

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

SKILL_MAX_SIZE = 64 * 1024

# Redis Key 前缀
_SKILL_VERSION_PREFIX = "skill:"
_SKILL_MARKET_PREFIX = "skill_market:"
_SKILL_MARKET_SET_KEY = "skill_market:all"
_SKILL_MARKET_CATEGORY_PREFIX = "skill_market:category:"
_SKILL_MARKET_RATINGS_PREFIX = "skill_market:ratings:"
_SKILL_TEST_PREFIX = "skill_test:"


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


PROMPT_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|above|all)\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|previous)", re.IGNORECASE),
    re.compile(r"disregard\s+(your|previous|above)", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"override\s+(previous|default|safety)", re.IGNORECASE),
]


class SkillManifest(BaseModel):
    """SKILL.md 的 YAML Front Matter 模型"""

    name: str = Field(min_length=1, max_length=64, description="Skill 名称")
    description: str = Field(default="", max_length=512, description="Skill 描述")
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
        self._agent_bindings: dict[str, list[str]] = {}
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

    def load_all(self) -> dict[str, SkillDocument]:
        """加载 skills/ 目录下所有 SKILL.md 和 SkillPack

        同时加载 SkillPack 格式的 skill.yaml、dependencies.yaml、
        tools.yaml、tests/test_cases.yaml 等扩展文件。

        Returns:
            加载的 Skill 文档字典
        """
        if self._loaded:
            return self._skills

        skills_dir = Path(self._skills_dir)
        if not skills_dir.is_dir():
            logger.warning("Skills 目录不存在: %s", self._skills_dir)
            self._loaded = True
            return self._skills

        for skill_dir in skills_dir.iterdir():
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

                logger.info("加载 Skill: %s (category=%s, version=%s)", name, doc.manifest.category, doc.manifest.version)
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

    def get_agent_prompt_extensions(self, agent_name: str) -> str:
        """获取 Agent 绑定的 Skill 指令拼接文本

        将所有绑定的 Skill 指令拼接为一段文本，用于追加到 Agent 的 System Prompt。

        Args:
            agent_name: Agent 名称

        Returns:
            Skill 指令拼接文本，无绑定时返回空字符串
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

        parts = []
        for doc in skills:
            instruction = sanitize_prompt(doc.instruction)
            if instruction:
                parts.append(f"## Skill: {doc.manifest.name}\n\n{instruction}")

        return "\n\n".join(parts) if parts else ""

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

    def save_skill(self, skill_name: str, content: str) -> SkillDocument:
        """保存 SKILL.md（新建或更新）

        执行 Prompt 注入检测，高风险时拒绝保存。

        Args:
            skill_name: Skill 名称
            content: SKILL.md 完整内容

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

        self._skills[skill_name] = doc

        # 重新加载 SkillPack 扩展数据
        self._load_skill_pack(skill_name)

        logger.info("保存 Skill: %s (version=%s)", skill_name, doc.manifest.version)
        return doc

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
        skill_md = Path(self._skills_dir) / skill_name / "SKILL.md"
        if not skill_md.is_file():
            return None
        try:
            return skill_md.read_text(encoding="utf-8")
        except OSError:
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
            from agent.core.redis_manager import get_redis_client
            return await get_redis_client()
        except Exception as e:
            logger.debug("Redis 获取失败: %s", e)
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

            version_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:{version}:manifest"
            versions_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:versions"
            latest_key = f"{_SKILL_VERSION_PREFIX}{skill_name}:latest"

            pipe = redis.pipeline()
            pipe.set(version_key, json.dumps(version_data, ensure_ascii=False), ex=86400 * 90)
            pipe.zadd(versions_key, {version: time.time()})
            pipe.set(latest_key, version, ex=86400 * 90)
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

            await redis.set(active_key, version, ex=86400 * 90)
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
                pipe.set(market_key, entry.model_dump_json(), ex=86400 * 90)
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

    async def install_from_marketplace(self, skill_name: str, target_version: str = "") -> SkillDocument | None:
        """从市场安装技能

        增加下载量计数，将技能注册到本地仓库。

        Args:
            skill_name: Skill 名称
            target_version: 目标版本号（空表示最新版本）

        Returns:
            SkillDocument 实例，失败时返回 None
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
                await redis.set(market_key, entry.model_dump_json(), ex=86400 * 90)
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
                    doc = self.save_skill(skill_name=skill_name, content=version_content)
                    logger.info("从市场安装技能成功: %s (version=%s)", skill_name, version_to_install)
                    return doc
                else:
                    logger.warning("市场安装失败: Skill %s@%s 版本内容不存在", skill_name, version_to_install)
            except Exception as e:
                logger.error("从市场安装技能异常: %s - %s", skill_name, e)

        logger.warning("从市场安装技能失败: %s (version=%s)", skill_name, target_version or entry.version)
        return None

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
                await redis.set(market_key, entry.model_dump_json(), ex=86400 * 90)

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
            from agent.core.session_manager import get_session_manager

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
                    from agent.core.session_manager import get_session_manager
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
            await redis.set(
                test_key,
                report.model_dump_json(),
                ex=86400 * 30,
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
