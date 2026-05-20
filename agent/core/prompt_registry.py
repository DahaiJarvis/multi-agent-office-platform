"""Agent System Prompt 注册中心

将 Agent 的 System Prompt 从代码中解耦，外置到 YAML 配置文件，
支持版本管理、灰度发布和效果回溯。

核心能力：
  - Prompt 外置：所有 Agent 的 System Prompt 从 YAML 文件加载
  - 版本管理：每次修改自动记录版本，支持回滚
  - 灰度发布：按百分比将流量路由到新版本 Prompt
  - 效果追踪：记录每个版本的使用次数和用户反馈

使用方式：
    from agent.core.prompt_registry import get_prompt_registry

    registry = get_prompt_registry()
    prompt = await registry.get_prompt("Supervisor")
"""

import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


class PromptVersion(BaseModel):
    """Prompt 版本记录"""

    version: str = Field(default="1.0.0", description="语义化版本号")
    content: str = Field(..., description="Prompt 内容")
    author: str = Field(default="system", description="修改者")
    description: str = Field(default="", description="变更说明")
    created_at: float = Field(default_factory=time.time)
    is_active: bool = Field(default=True, description="是否为当前活跃版本")


class PromptEntry(BaseModel):
    """Prompt 注册条目"""

    agent_name: str = Field(..., description="Agent 名称")
    description: str = Field(default="", description="Prompt 用途说明")
    current_version: str = Field(default="1.0.0", description="当前活跃版本")
    versions: list[PromptVersion] = Field(default_factory=list, description="版本历史")
    canary_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="灰度百分比")
    canary_version: str = Field(default="", description="灰度版本号")


class IntentDefinition(BaseModel):
    """意图标签定义"""

    name: str = Field(..., description="意图标签名称")
    label: str = Field(default="", description="中文标签")
    description: str = Field(default="", description="意图说明")


class IntentExample(BaseModel):
    """意图分类示例"""

    input: str = Field(..., description="用户输入")
    output: str = Field(..., description="期望意图标签")
    reason: str = Field(default="", description="分类原因")


class PromptRegistry:
    """Prompt 注册中心

    从 YAML 文件加载所有 Agent 的 System Prompt，
    支持版本管理和灰度发布。
    """

    def __init__(self, prompts_dir: Path | str | None = None) -> None:
        self._prompts_dir = Path(prompts_dir) if prompts_dir else PROMPTS_DIR
        self._entries: dict[str, PromptEntry] = {}
        self._intents: list[IntentDefinition] = []
        self._intent_examples: list[IntentExample] = []
        self._loaded: bool = False

    def _ensure_loaded(self) -> None:
        """确保 Prompt 已从文件加载"""
        if self._loaded:
            return
        self._load_from_files()
        self._loaded = True
        self._validate_intents()

    def _validate_intents(self) -> None:
        """校验意图标签配置的完整性和一致性

        校验项：
          1. 意图标签名称不能重复
          2. 分类示例中引用的意图标签必须存在于意图列表中
          3. Prompt 模板渲染后不应残留未替换的占位符
        """
        if not self._intents:
            logger.warning("Schema校验: 未加载到任何意图标签，意图分类功能将不可用")
            return

        # 校验1：意图标签名称不能重复
        seen_names: set[str] = set()
        for intent in self._intents:
            if intent.name in seen_names:
                logger.error("Schema校验[ERROR]: 意图标签名称重复 - %s", intent.name)
            seen_names.add(intent.name)

        # 校验2：分类示例中引用的意图标签必须存在于意图列表中
        intent_names = {i.name for i in self._intents}
        for example in self._intent_examples:
            if example.output not in intent_names:
                logger.error(
                    "Schema校验[ERROR]: 分类示例引用了不存在的意图标签 - 示例'%s' -> '%s'",
                    example.input, example.output,
                )

        # 校验3：Prompt 模板渲染后不应残留未替换的占位符
        entry = self._entries.get("IntentClassifier")
        if entry and entry.versions:
            latest_content = entry.versions[-1].content
            if "{{ " in latest_content and "}}" in latest_content:
                logger.error("Schema校验[ERROR]: IntentClassifier Prompt 模板渲染不完整，存在未替换的占位符")

        logger.info("Schema校验: 意图标签校验完成, 共 %d 个意图标签, %d 个分类示例", len(self._intents), len(self._intent_examples))

    def _load_from_files(self) -> None:
        """从 YAML 文件加载所有 Prompt"""
        if not self._prompts_dir.exists():
            logger.warning("Prompt 配置目录不存在: %s，使用代码内嵌默认值", self._prompts_dir)
            self._load_defaults()
            return

        yaml_files = list(self._prompts_dir.glob("*.yaml")) + list(self._prompts_dir.glob("*.yml"))
        if not yaml_files:
            logger.warning("Prompt 配置目录为空: %s，使用代码内嵌默认值", self._prompts_dir)
            self._load_defaults()
            return

        for yaml_file in yaml_files:
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue

                agent_name = data.get("agent_name", yaml_file.stem)
                description = data.get("description", "")
                content = data.get("content", "")
                version = data.get("version", "1.0.0")
                canary_percent = data.get("canary_percent", 0.0)
                canary_version = data.get("canary_version", "")

                # 解析结构化的意图标签和示例（仅 IntentClassifier）
                if agent_name == "IntentClassifier":
                    self._load_intent_definitions(data)

                # 渲染 Prompt 模板中的占位符
                if content and "{{" in content:
                    content = self._render_prompt_template(content)

                if not content:
                    logger.warning("Prompt 文件 %s 内容为空，跳过", yaml_file.name)
                    continue

                prompt_version = PromptVersion(
                    version=version,
                    content=content,
                    description="初始版本",
                )

                entry = PromptEntry(
                    agent_name=agent_name,
                    description=description,
                    current_version=version,
                    versions=[prompt_version],
                    canary_percent=canary_percent,
                    canary_version=canary_version,
                )
                self._entries[agent_name] = entry
                logger.info("加载 Prompt: agent=%s version=%s", agent_name, version)

            except Exception as e:
                logger.error("加载 Prompt 文件 %s 失败: %s", yaml_file.name, e)

        if not self._entries:
            logger.warning("未从文件加载到任何 Prompt，使用代码内嵌默认值")
            self._load_defaults()

    def _load_intent_definitions(self, data: dict[str, Any]) -> None:
        """从 YAML 数据中加载结构化的意图标签和示例

        Args:
            data: YAML 文件解析后的字典
        """
        intents_data = data.get("intents", [])
        if intents_data and isinstance(intents_data, list):
            self._intents = [
                IntentDefinition(
                    name=item.get("name", ""),
                    label=item.get("label", ""),
                    description=item.get("description", ""),
                )
                for item in intents_data
                if item.get("name")
            ]
            logger.info("加载意图标签: %d 个", len(self._intents))

        examples_data = data.get("examples", [])
        if examples_data and isinstance(examples_data, list):
            self._intent_examples = [
                IntentExample(
                    input=item.get("input", ""),
                    output=item.get("output", ""),
                    reason=item.get("reason", ""),
                )
                for item in examples_data
                if item.get("input") and item.get("output")
            ]
            logger.info("加载意图示例: %d 个", len(self._intent_examples))

    def _render_prompt_template(self, template: str) -> str:
        """渲染 Prompt 模板中的占位符

        支持的占位符：
        - {{ intents }}: 渲染意图标签列表
        - {{ examples }}: 渲染分类示例

        Args:
            template: 包含占位符的 Prompt 模板

        Returns:
            渲染后的 Prompt 文本
        """
        result = template

        if "{{ intents }}" in result:
            if self._intents:
                intent_lines = [
                    f"- {item.name}: {item.description}"
                    for item in self._intents
                ]
                result = result.replace("{{ intents }}", "\n".join(intent_lines))
            else:
                result = result.replace("{{ intents }}", "（未配置意图标签）")

        if "{{ examples }}" in result:
            if self._intent_examples:
                example_lines = []
                for ex in self._intent_examples:
                    line = f'- "{ex.input}" -> {ex.output}'
                    if ex.reason:
                        line += f"({ex.reason})"
                    example_lines.append(line)
                result = result.replace("{{ examples }}", "\n".join(example_lines))
            else:
                result = result.replace("{{ examples }}", "（未配置分类示例）")

        return result

    def get_intents(self) -> list[IntentDefinition]:
        """获取所有意图标签定义

        Returns:
            意图标签列表
        """
        self._ensure_loaded()
        return list(self._intents)

    def get_intent_examples(self) -> list[IntentExample]:
        """获取所有意图分类示例

        Returns:
            分类示例列表
        """
        self._ensure_loaded()
        return list(self._intent_examples)

    def _load_defaults(self) -> None:
        """加载代码内嵌的默认 Prompt（作为降级方案）"""
        from agent.agents.supervisor import SUPERVISOR_SYSTEM_PROMPT, INTENT_CLASSIFICATION_PROMPT
        from agent.agents.domain import AGENT_PROMPTS
        from agent.agents.reviewer import REVIEWER_SYSTEM_PROMPT

        defaults: dict[str, str] = {
            "Supervisor": SUPERVISOR_SYSTEM_PROMPT,
            "IntentClassifier": INTENT_CLASSIFICATION_PROMPT,
            "Reviewer": REVIEWER_SYSTEM_PROMPT,
        }
        defaults.update(AGENT_PROMPTS)

        for agent_name, content in defaults.items():
            if agent_name in self._entries:
                continue
            entry = PromptEntry(
                agent_name=agent_name,
                current_version="1.0.0",
                versions=[PromptVersion(version="1.0.0", content=content, description="代码内嵌默认版本")],
            )
            self._entries[agent_name] = entry

    async def get_prompt(self, agent_name: str) -> str:
        """获取 Agent 的当前活跃 System Prompt

        支持灰度发布：根据 canary_percent 概率返回灰度版本。

        Args:
            agent_name: Agent 名称

        Returns:
            System Prompt 字符串
        """
        self._ensure_loaded()

        entry = self._entries.get(agent_name)
        if entry is None:
            logger.warning("未找到 Agent %s 的 Prompt，使用空字符串", agent_name)
            return ""

        # 灰度发布逻辑
        if entry.canary_version and entry.canary_percent > 0:
            import random
            if random.random() * 100 < entry.canary_percent:
                canary = self._find_version(entry, entry.canary_version)
                if canary:
                    logger.debug("Agent %s 命中灰度版本: %s", agent_name, entry.canary_version)
                    return canary.content

        # 返回当前活跃版本
        active = self._find_version(entry, entry.current_version)
        if active:
            return active.content

        if entry.versions:
            return entry.versions[-1].content

        return ""

    def get_prompt_sync(self, agent_name: str) -> str:
        """获取 Agent 的当前活跃 System Prompt（同步版本）

        适用于同步上下文（如 create_supervisor_agent）。
        灰度发布逻辑与异步版本一致。

        Args:
            agent_name: Agent 名称

        Returns:
            System Prompt 字符串
        """
        self._ensure_loaded()

        entry = self._entries.get(agent_name)
        if entry is None:
            return ""

        if entry.canary_version and entry.canary_percent > 0:
            import random
            if random.random() * 100 < entry.canary_percent:
                canary = self._find_version(entry, entry.canary_version)
                if canary:
                    return canary.content

        active = self._find_version(entry, entry.current_version)
        if active:
            return active.content

        if entry.versions:
            return entry.versions[-1].content

        return ""

    def get_entry(self, agent_name: str) -> PromptEntry | None:
        """获取 Agent 的 Prompt 条目（含版本历史）"""
        self._ensure_loaded()
        return self._entries.get(agent_name)

    def list_entries(self) -> list[PromptEntry]:
        """列出所有 Prompt 条目"""
        self._ensure_loaded()
        return list(self._entries.values())

    def register_version(
        self,
        agent_name: str,
        content: str,
        version: str,
        author: str = "system",
        description: str = "",
    ) -> PromptVersion:
        """注册新版本的 Prompt

        Args:
            agent_name: Agent 名称
            content: Prompt 内容
            version: 语义化版本号
            author: 修改者
            description: 变更说明

        Returns:
            新创建的 PromptVersion
        """
        self._ensure_loaded()

        new_version = PromptVersion(
            version=version,
            content=content,
            author=author,
            description=description,
        )

        if agent_name not in self._entries:
            entry = PromptEntry(
                agent_name=agent_name,
                current_version=version,
                versions=[new_version],
            )
            self._entries[agent_name] = entry
        else:
            entry = self._entries[agent_name]
            existing = self._find_version(entry, version)
            if existing:
                existing.content = content
                existing.author = author
                existing.description = description
                existing.created_at = time.time()
            else:
                entry.versions.append(new_version)

        logger.info("注册 Prompt 版本: agent=%s version=%s", agent_name, version)
        return new_version

    def activate_version(self, agent_name: str, version: str) -> bool:
        """激活指定版本的 Prompt

        Args:
            agent_name: Agent 名称
            version: 要激活的版本号

        Returns:
            是否激活成功
        """
        self._ensure_loaded()

        entry = self._entries.get(agent_name)
        if entry is None:
            return False

        target = self._find_version(entry, version)
        if target is None:
            return False

        for v in entry.versions:
            v.is_active = (v.version == version)

        entry.current_version = version
        logger.info("激活 Prompt 版本: agent=%s version=%s", agent_name, version)
        return True

    def set_canary(self, agent_name: str, version: str, percent: float) -> bool:
        """设置灰度发布

        Args:
            agent_name: Agent 名称
            version: 灰度版本号
            percent: 灰度百分比（0-100）

        Returns:
            是否设置成功
        """
        self._ensure_loaded()

        entry = self._entries.get(agent_name)
        if entry is None:
            return False

        if not self._find_version(entry, version):
            return False

        entry.canary_version = version
        entry.canary_percent = min(100.0, max(0.0, percent))
        logger.info("设置灰度: agent=%s version=%s percent=%.1f%%", agent_name, version, percent)
        return True

    def rollback(self, agent_name: str) -> bool:
        """回滚到上一个版本

        Args:
            agent_name: Agent 名称

        Returns:
            是否回滚成功
        """
        self._ensure_loaded()

        entry = self._entries.get(agent_name)
        if entry is None or len(entry.versions) < 2:
            return False

        versions = entry.versions
        current_idx = next(
            (i for i, v in enumerate(versions) if v.version == entry.current_version),
            len(versions) - 1,
        )
        if current_idx <= 0:
            return False

        prev_version = versions[current_idx - 1].version
        return self.activate_version(agent_name, prev_version)

    @staticmethod
    def _find_version(entry: PromptEntry, version: str) -> PromptVersion | None:
        """在版本列表中查找指定版本"""
        for v in entry.versions:
            if v.version == version:
                return v
        return None


_registry: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    """获取全局 Prompt 注册中心实例"""
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry
