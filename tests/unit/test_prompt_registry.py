"""Prompt Registry 单元测试

验证 Prompt 注册中心的加载、版本管理、灰度发布等功能。
"""

import pytest
import tempfile
import os

from agent.core.prompt.prompt_registry import PromptRegistry, PromptEntry, PromptVersion


@pytest.fixture
def temp_prompts_dir():
    """创建临时 Prompt 配置目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试用 YAML 文件
        yaml_content = """agent_name: TestAgent
description: "测试 Agent"
version: "1.0.0"
content: |
  你是测试 Agent，用于单元测试。
"""
        with open(os.path.join(tmpdir, "TestAgent.yaml"), "w", encoding="utf-8") as f:
            f.write(yaml_content)
        yield tmpdir


@pytest.fixture
def empty_prompts_dir():
    """创建空的临时目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def registry(temp_prompts_dir):
    """创建使用临时目录的 PromptRegistry"""
    return PromptRegistry(prompts_dir=temp_prompts_dir)


class TestPromptLoading:
    """Prompt 加载测试"""

    def test_load_from_yaml(self, registry):
        """测试从 YAML 文件加载 Prompt"""
        import asyncio
        prompt = asyncio.run(registry.get_prompt("TestAgent"))
        assert "测试 Agent" in prompt

    def test_load_empty_dir_falls_back_to_defaults(self, empty_prompts_dir):
        """测试空目录时降级到代码内嵌默认值"""
        reg = PromptRegistry(prompts_dir=empty_prompts_dir)
        import asyncio
        prompt = asyncio.run(reg.get_prompt("Supervisor"))
        assert len(prompt) > 0

    def test_nonexistent_dir_falls_back_to_defaults(self):
        """测试目录不存在时降级到代码内嵌默认值"""
        reg = PromptRegistry(prompts_dir="/nonexistent/path")
        import asyncio
        prompt = asyncio.run(reg.get_prompt("ApprovalAgent"))
        assert len(prompt) > 0

    def test_get_nonexistent_agent_returns_empty(self, registry):
        """测试获取不存在的 Agent Prompt 返回空字符串"""
        import asyncio
        prompt = asyncio.run(registry.get_prompt("NonExistentAgent"))
        assert prompt == ""


class TestPromptVersionManagement:
    """Prompt 版本管理测试"""

    def test_register_new_version(self, registry):
        """测试注册新版本"""
        registry.register_version(
            agent_name="TestAgent",
            content="新版本内容",
            version="2.0.0",
            author="tester",
            description="重大更新",
        )

        entry = registry.get_entry("TestAgent")
        assert entry is not None
        assert len(entry.versions) == 2
        assert entry.versions[-1].version == "2.0.0"

    def test_activate_version(self, registry):
        """测试激活指定版本"""
        registry.register_version(
            agent_name="TestAgent",
            content="版本2内容",
            version="2.0.0",
        )

        result = registry.activate_version("TestAgent", "2.0.0")
        assert result is True

        entry = registry.get_entry("TestAgent")
        assert entry.current_version == "2.0.0"

    def test_activate_nonexistent_version(self, registry):
        """测试激活不存在的版本"""
        result = registry.activate_version("TestAgent", "99.0.0")
        assert result is False

    def test_rollback(self, registry):
        """测试回滚到上一个版本"""
        registry.register_version(
            agent_name="TestAgent",
            content="版本2内容",
            version="2.0.0",
        )
        registry.activate_version("TestAgent", "2.0.0")

        result = registry.rollback("TestAgent")
        assert result is True

        entry = registry.get_entry("TestAgent")
        assert entry.current_version == "1.0.0"

    def test_rollback_no_previous_version(self, registry):
        """测试只有一个版本时无法回滚"""
        result = registry.rollback("TestAgent")
        assert result is False


class TestCanaryRelease:
    """灰度发布测试"""

    def test_set_canary(self, registry):
        """测试设置灰度发布"""
        registry.register_version(
            agent_name="TestAgent",
            content="灰度版本内容",
            version="2.0.0",
        )

        result = registry.set_canary("TestAgent", "2.0.0", 20.0)
        assert result is True

        entry = registry.get_entry("TestAgent")
        assert entry.canary_version == "2.0.0"
        assert entry.canary_percent == 20.0

    def test_set_canary_nonexistent_version(self, registry):
        """测试灰度不存在的版本"""
        result = registry.set_canary("TestAgent", "99.0.0", 50.0)
        assert result is False

    def test_canary_percent_clamped(self, registry):
        """测试灰度百分比范围限制"""
        registry.register_version(
            agent_name="TestAgent",
            content="灰度版本",
            version="2.0.0",
        )

        registry.set_canary("TestAgent", "2.0.0", 150.0)
        entry = registry.get_entry("TestAgent")
        assert entry.canary_percent == 100.0

    def test_canary_zero_percent(self, registry):
        """测试灰度百分比为0时不生效"""
        registry.register_version(
            agent_name="TestAgent",
            content="灰度版本",
            version="2.0.0",
        )
        registry.set_canary("TestAgent", "2.0.0", 0.0)

        import asyncio
        prompt = asyncio.run(registry.get_prompt("TestAgent"))
        # 0% 灰度，应始终返回当前活跃版本
        entry = registry.get_entry("TestAgent")
        active = registry._find_version(entry, entry.current_version)
        assert prompt == active.content


class TestSyncPromptAccess:
    """同步 Prompt 访问测试"""

    def test_get_prompt_sync(self, registry):
        """测试同步获取 Prompt"""
        prompt = registry.get_prompt_sync("TestAgent")
        assert "测试 Agent" in prompt

    def test_get_prompt_sync_nonexistent(self, registry):
        """测试同步获取不存在的 Prompt"""
        prompt = registry.get_prompt_sync("NonExistentAgent")
        assert prompt == ""


class TestListEntries:
    """条目列表测试"""

    def test_list_entries(self, registry):
        """测试列出所有条目"""
        entries = registry.list_entries()
        assert len(entries) >= 1
        agent_names = [e.agent_name for e in entries]
        assert "TestAgent" in agent_names
