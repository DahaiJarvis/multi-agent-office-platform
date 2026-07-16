"""金丝雀回归测试管理

管理金丝雀测试集合，支持 Fast/Slow 套件划分与执行。
对应 spec 文档 3.6 节。

套件划分：
  - Fast 套件：tags 含 "canary" 且 severity != "adversarial"，预期 10-15 个 fixture
  - Slow 套件：全部 fixture（含 adversarial）
"""

import logging
from pathlib import Path

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.fixtures.dataset_loader import DatasetLoader

logger = logging.getLogger(__name__)


class CanaryManager:
    """金丝雀管理器

    管理金丝雀测试集合，支持 Fast/Slow 套件划分与执行。

    使用示例：
        manager = CanaryManager()
        fast_suite = manager.get_fast_suite()
        slow_suite = manager.get_slow_suite()
    """

    def __init__(self, datasets_dir: str | None = None) -> None:
        """初始化

        Args:
            datasets_dir: 数据集目录，None 时使用默认目录
        """
        self._loader = DatasetLoader(datasets_dir)

    def get_fast_suite(self) -> list[Fixture]:
        """获取 Fast 套件（核心金丝雀场景）

        选取规则：tags 含 "canary" 且 severity != "adversarial"
        预期规模：10-15 个 fixture

        Returns:
            金丝雀 Fixture 列表
        """
        all_fixtures = self._loader.load_all()
        fast_suite = [
            f for f in all_fixtures
            if f.is_canary() and not f.is_adversarial()
        ]

        logger.info("Fast 套件: %d 个 fixture", len(fast_suite))
        return fast_suite

    def get_slow_suite(self) -> list[Fixture]:
        """获取 Slow 套件（全量场景含对抗）

        选取规则：全部 fixture

        Returns:
            全部 Fixture 列表
        """
        all_fixtures = self._loader.load_all()
        logger.info("Slow 套件: %d 个 fixture", len(all_fixtures))
        return all_fixtures

    def add_canary(self, fixture: Fixture) -> None:
        """新增金丝雀场景

        将 fixture 加入金丝雀集合（自动打上 "canary" 标签）。
        注意：此方法仅修改内存对象，不持久化。持久化需手动写入 YAML 文件。

        Args:
            fixture: 待加入的 fixture
        """
        if "canary" not in fixture.tags:
            # Pydantic 模型默认非 frozen，可直接修改
            fixture.tags.append("canary")
        logger.info("新增金丝雀: %s", fixture.fixture_id)

    def remove_canary(self, fixture_id: str) -> bool:
        """移除金丝雀场景

        从金丝雀集合中移除指定 fixture（移除 "canary" 标签）。
        注意：此方法仅修改内存对象，不持久化。

        Args:
            fixture_id: fixture 唯一标识

        Returns:
            是否移除成功
        """
        all_fixtures = self._loader.load_all()
        for fixture in all_fixtures:
            if fixture.fixture_id == fixture_id:
                if "canary" in fixture.tags:
                    fixture.tags.remove("canary")
                    logger.info("移除金丝雀: %s", fixture_id)
                    return True
                else:
                    logger.warning("fixture %s 不在金丝雀集合中", fixture_id)
                    return False

        logger.warning("fixture 未找到: %s", fixture_id)
        return False

    def list_canaries(self) -> list[dict]:
        """列出所有金丝雀场景摘要"""
        fast_suite = self.get_fast_suite()
        return [f.to_summary() for f in fast_suite]

    def get_fixture_by_id(self, fixture_id: str) -> Fixture | None:
        """按 ID 获取 fixture"""
        all_fixtures = self._loader.load_all()
        for fixture in all_fixtures:
            if fixture.fixture_id == fixture_id:
                return fixture
        return None
