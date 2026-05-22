"""Skill 适配核心模块

实现 SKILL.md 格式的技能导入、解析、绑定和运行时管理，
使 Agent 行为可通过外部 Skill 增强。

核心类：
  - SkillManifest: SKILL.md 的 YAML Front Matter 模型
  - SkillDocument: SKILL.md 完整文档（manifest + instruction）
  - SkillRegistry: Skills 仓库管理（加载/搜索/绑定/启禁/导入导出）

Prompt 注入检测：
  - 第一层：基于规则的正则匹配（7 种模式）
  - 第二层：LLM 辅助判断（可选，规则检测 risk_level >= medium 时）

风险等级：
  - high（>=2 个匹配）：拒绝
  - medium（1 个匹配）：标记 review-required
  - low（0 个匹配）：放行
"""

import logging
import os
import re
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


class SkillRegistry:
    """Skills 仓库管理

    提供 SKILL.md 的加载、搜索、绑定、启禁、导入导出等管理能力。
    单例模式，通过 get_instance() 获取全局实例。
    """

    _instance: "SkillRegistry | None" = None

    def __init__(self, skills_dir: str | None = None) -> None:
        self._skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, SkillDocument] = {}
        self._agent_bindings: dict[str, list[str]] = {}
        self._active_skills: dict[str, set[str]] = {}
        self._loaded: bool = False

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
        """加载 skills/ 目录下所有 SKILL.md

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
                logger.info("加载 Skill: %s (category=%s, version=%s)", name, doc.manifest.category, doc.manifest.version)
            except SkillParseError as e:
                logger.warning("Skill 加载失败: %s", e)
            except Exception as e:
                logger.warning("Skill 加载异常: %s - %s", skill_dir.name, e)

        self._loaded = True
        logger.info("Skills 加载完成: 共 %d 个", len(self._skills))
        return self._skills

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
