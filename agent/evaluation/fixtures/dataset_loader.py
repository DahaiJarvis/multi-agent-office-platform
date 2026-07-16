"""数据集加载器

从 YAML/JSON 文件加载 Fixture 集合，支持按 category/severity/tags 过滤。
对应 spec 文档 3.1 节。

支持的文件格式：
  - YAML: .yaml / .yml（主格式，可读性强，支持注释）
  - JSON: .json（兼容格式）

数据集目录结构：
  datasets/
    email_query.yaml       # 包含多个 Fixture 的列表
    email_send.yaml
    approval_action.yaml
    ...
    adversarial/
      injection_attempt.yaml
      pii_leakage.yaml
"""

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from agent.evaluation.fixtures.fixture_schema import Fixture

logger = logging.getLogger(__name__)

# 默认数据集目录
_DEFAULT_DATASETS_DIR = Path(__file__).parent / "datasets"


class DatasetLoader:
    """评估数据集加载器

    从 YAML/JSON 文件加载 Fixture 集合，支持按 category/severity/tags 过滤。

    使用示例：
        loader = DatasetLoader()
        # 加载单个数据集
        fixtures = loader.load_dataset("email_query")
        # 加载全部数据集
        all_fixtures = loader.load_all(category="email", tags=["canary"])
        # 加载金丝雀核心套件
        canary_fixtures = loader.load_canary_suite()
    """

    def __init__(self, datasets_dir: str | Path | None = None) -> None:
        """初始化加载器

        Args:
            datasets_dir: 数据集目录路径，默认为 agent/evaluation/fixtures/datasets
        """
        self._datasets_dir = Path(datasets_dir) if datasets_dir else _DEFAULT_DATASETS_DIR
        if not self._datasets_dir.exists():
            logger.warning("数据集目录不存在: %s", self._datasets_dir)

    def load_dataset(self, name: str) -> list[Fixture]:
        """加载指定名称的数据集

        按优先级查找 .yaml > .yml > .json 文件。

        Args:
            name: 数据集名称（不含扩展名），如 "email_query"

        Returns:
            Fixture 列表（文件不存在或为空时返回空列表）
        """
        for ext in (".yaml", ".yml", ".json"):
            file_path = self._datasets_dir / f"{name}{ext}"
            if file_path.exists():
                return self._load_file(file_path)

        logger.warning("数据集文件不存在: %s（已查找 .yaml/.yml/.json）", name)
        return []

    def load_all(
        self,
        category: str | None = None,
        severity: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Fixture]:
        """加载全部数据集，支持过滤

        递归扫描数据集目录下所有 .yaml/.yml/.json 文件，合并加载并过滤。

        Args:
            category: 仅加载该分类（email/approval/crm/hr/finance/knowledge/adversarial）
            severity: 仅加载该严重度（normal/edge/adversarial）
            tags: 仅加载含任一标签的 fixture

        Returns:
            过滤后的 Fixture 列表
        """
        all_fixtures: list[Fixture] = []

        # 递归扫描所有数据集文件
        for ext in ("*.yaml", "*.yml", "*.json"):
            for file_path in sorted(self._datasets_dir.rglob(ext)):
                fixtures = self._load_file(file_path)
                all_fixtures.extend(fixtures)

        # 应用过滤
        filtered = self._apply_filters(all_fixtures, category, severity, tags)

        logger.info(
            "加载数据集: 总计 %d 个 fixture，过滤后 %d 个（category=%s, severity=%s, tags=%s）",
            len(all_fixtures),
            len(filtered),
            category,
            severity,
            tags,
        )
        return filtered

    def load_canary_suite(self) -> list[Fixture]:
        """加载金丝雀核心套件

        选取规则：tags 含 "canary" 且 severity != "adversarial"
        预期规模：10-15 个 fixture

        Returns:
            金丝雀 Fixture 列表
        """
        all_fixtures = self.load_all()
        canary_fixtures = [
            f for f in all_fixtures
            if f.is_canary() and not f.is_adversarial()
        ]

        logger.info("加载金丝雀套件: %d 个 fixture", len(canary_fixtures))
        return canary_fixtures

    def list_datasets(self) -> list[str]:
        """列出所有可用的数据集名称（不含扩展名）"""
        names: list[str] = []
        for ext in ("*.yaml", "*.yml", "*.json"):
            for file_path in self._datasets_dir.rglob(ext):
                name = file_path.stem
                if name not in names:
                    names.append(name)
        return sorted(names)

    def _load_file(self, file_path: Path) -> list[Fixture]:
        """从单个文件加载 Fixture 列表"""
        try:
            raw_data = self._read_file(file_path)
            if not isinstance(raw_data, list):
                logger.warning("数据集文件 %s 顶层应为列表，实际为 %s", file_path, type(raw_data).__name__)
                return []

            fixtures: list[Fixture] = []
            for idx, item in enumerate(raw_data):
                if not isinstance(item, dict):
                    logger.warning("数据集 %s 第 %d 项非字典，跳过", file_path.name, idx)
                    continue
                try:
                    fixtures.append(Fixture(**item))
                except Exception as e:
                    logger.warning("数据集 %s 第 %d 项加载失败: %s", file_path.name, idx, e)

            logger.debug("从 %s 加载 %d 个 fixture", file_path.name, len(fixtures))
            return fixtures

        except Exception as e:
            logger.error("加载数据集文件 %s 失败: %s", file_path, e)
            return []

    def _read_file(self, file_path: Path) -> Any:
        """读取 YAML/JSON 文件"""
        content = file_path.read_text(encoding="utf-8")
        if file_path.suffix in (".yaml", ".yml"):
            return yaml.safe_load(content)
        elif file_path.suffix == ".json":
            return json.loads(content)
        else:
            # 尝试 YAML 解析（兼容）
            return yaml.safe_load(content)

    def _apply_filters(
        self,
        fixtures: list[Fixture],
        category: str | None = None,
        severity: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Fixture]:
        """应用过滤条件"""
        filtered = fixtures

        if category is not None:
            filtered = [f for f in filtered if f.category == category]

        if severity is not None:
            filtered = [f for f in filtered if f.severity == severity]

        if tags is not None and tags:
            tag_set = set(tags)
            filtered = [f for f in filtered if tag_set & set(f.tags)]

        return filtered
