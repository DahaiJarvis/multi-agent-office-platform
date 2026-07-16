"""Fixture 数据模型与数据集加载器单元测试

覆盖 spec 文档 4.1 节 Fixture 模型与 3.1 节 DatasetLoader 接口。
"""

import json
from pathlib import Path

import pytest

from agent.evaluation.fixtures.fixture_schema import Fixture
from agent.evaluation.fixtures.dataset_loader import DatasetLoader


class TestFixtureSchema:
    """Fixture 数据模型测试"""

    def test_fixture_basic_creation(self, sample_fixture):
        """测试 Fixture 基本字段创建"""
        f = sample_fixture
        assert f.fixture_id == "test_email_001"
        assert f.category == "email"
        assert f.severity == "normal"
        assert f.input == "帮我查询最近三封未读邮件"
        assert f.source == "manual"

    def test_fixture_default_values(self):
        """测试 Fixture 默认值"""
        f = Fixture(fixture_id="t1", input="hello")
        assert f.category == "general"
        assert f.severity == "normal"
        assert f.context == {}
        assert f.expected_tools == []
        assert f.forbidden_tools == []
        assert f.tags == []
        assert f.source == "manual"
        assert f.source_trace_id == ""

    def test_fixture_required_fields(self):
        """测试缺少必填字段时抛出校验错误"""
        with pytest.raises(Exception):
            Fixture(input="hello")  # 缺少 fixture_id
        with pytest.raises(Exception):
            Fixture(fixture_id="t1")  # 缺少 input

    def test_is_canary(self, sample_fixture, adversarial_fixture):
        """测试 is_canary 标签判断"""
        assert sample_fixture.is_canary() is True
        assert adversarial_fixture.is_canary() is False

    def test_is_adversarial(self, sample_fixture, adversarial_fixture):
        """测试 is_adversarial 判断（severity 或 category 为 adversarial）"""
        assert sample_fixture.is_adversarial() is False
        assert adversarial_fixture.is_adversarial() is True

    def test_is_readonly(self, sample_fixture, adversarial_fixture):
        """测试 is_readonly 标签判断"""
        assert sample_fixture.is_readonly() is True
        assert adversarial_fixture.is_readonly() is False

    def test_has_tag(self, sample_fixture):
        """测试 has_tag 方法"""
        assert sample_fixture.has_tag("canary") is True
        assert sample_fixture.has_tag("nonexistent") is False

    def test_to_summary(self, sample_fixture):
        """测试 to_summary 返回结构"""
        summary = sample_fixture.to_summary()
        assert summary["fixture_id"] == "test_email_001"
        assert summary["category"] == "email"
        assert summary["severity"] == "normal"
        assert "canary" in summary["tags"]
        assert summary["source"] == "manual"


class TestDatasetLoader:
    """DatasetLoader 数据集加载器测试"""

    def test_load_dataset_email_query(self):
        """测试加载 email_query 数据集"""
        loader = DatasetLoader()
        fixtures = loader.load_dataset("email_query")
        assert len(fixtures) >= 2
        assert all(isinstance(f, Fixture) for f in fixtures)
        assert all(f.category == "email" for f in fixtures)

    def test_load_dataset_not_found(self):
        """测试加载不存在的数据集返回空列表"""
        loader = DatasetLoader()
        fixtures = loader.load_dataset("nonexistent_dataset_xyz")
        assert fixtures == []

    def test_load_all_default(self):
        """测试 load_all 加载全部数据集"""
        loader = DatasetLoader()
        all_fixtures = loader.load_all()
        # 至少包含 12 个 fixture（10 个常规 + 2 个对抗）
        assert len(all_fixtures) >= 12

    def test_load_all_filter_by_category(self):
        """测试 load_all 按 category 过滤"""
        loader = DatasetLoader()
        email_fixtures = loader.load_all(category="email")
        assert len(email_fixtures) >= 2
        assert all(f.category == "email" for f in email_fixtures)

    def test_load_all_filter_by_severity(self):
        """测试 load_all 按 severity 过滤"""
        loader = DatasetLoader()
        adversarial_fixtures = loader.load_all(severity="adversarial")
        assert len(adversarial_fixtures) >= 2
        assert all(f.severity == "adversarial" for f in adversarial_fixtures)

    def test_load_all_filter_by_tags(self):
        """测试 load_all 按 tags 过滤（任一匹配）"""
        loader = DatasetLoader()
        canary_fixtures = loader.load_all(tags=["canary"])
        assert len(canary_fixtures) >= 10
        assert all(f.is_canary() for f in canary_fixtures)

    def test_load_canary_suite(self):
        """测试加载金丝雀套件（canary 标签且非 adversarial）"""
        loader = DatasetLoader()
        canary_suite = loader.load_canary_suite()
        # spec 要求 Fast 套件 10-15 个 fixture
        assert len(canary_suite) >= 10
        assert all(f.is_canary() for f in canary_suite)
        assert all(not f.is_adversarial() for f in canary_suite)

    def test_list_datasets(self):
        """测试列出所有数据集名称"""
        loader = DatasetLoader()
        names = loader.list_datasets()
        assert "email_query" in names
        assert "email_send" in names
        assert "approval_action" in names
        assert "injection_attempt" in names
        assert "pii_leakage" in names

    def test_load_all_contains_adversarial(self):
        """测试 load_all 包含 adversarial 子目录的数据集"""
        loader = DatasetLoader()
        all_fixtures = loader.load_all()
        adversarial_fixtures = [f for f in all_fixtures if f.is_adversarial()]
        assert len(adversarial_fixtures) >= 2

    def test_load_from_custom_dir(self, tmp_path):
        """测试从自定义目录加载（JSON 格式）"""
        # 构造临时 JSON 数据集
        data = [
            {
                "fixture_id": "custom_001",
                "category": "email",
                "input": "测试输入",
                "expected_tools": ["email_query"],
            }
        ]
        json_file = tmp_path / "custom.json"
        json_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        loader = DatasetLoader(datasets_dir=str(tmp_path))
        fixtures = loader.load_dataset("custom")
        assert len(fixtures) == 1
        assert fixtures[0].fixture_id == "custom_001"

    def test_load_from_custom_yaml(self, tmp_path):
        """测试从自定义目录加载（YAML 格式）"""
        yaml_content = """
- fixture_id: yaml_001
  category: approval
  input: 审批测试
  expected_tools:
    - approval_query
  tags:
    - canary
"""
        yaml_file = tmp_path / "yaml_dataset.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        loader = DatasetLoader(datasets_dir=str(tmp_path))
        fixtures = loader.load_dataset("yaml_dataset")
        assert len(fixtures) == 1
        assert fixtures[0].fixture_id == "yaml_001"
        assert fixtures[0].expected_tools == ["approval_query"]
