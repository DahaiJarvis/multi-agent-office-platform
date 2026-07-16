"""确定性模式上下文管理器

================================================================================
模块职责
================================================================================
为 pass@k 评估提供可重复的确定性执行环境，确保多次运行结果一致。

核心能力：
  - patch model_client.get_model_client 返回确定性客户端
  - 固定 random.seed / uuid 生成
  - 支持真实模型（temperature=0）或 Mock 客户端两种模式

================================================================================
使用示例
================================================================================
    # 使用 Mock 客户端
    mode = DeterministicMode(mock_responses={"邮件": "邮件已发送"})
    with mode() as ctx:
        # 此处 get_model_client 返回 Mock 客户端
        ...

    # 使用真实模型 + temperature=0
    mode = DeterministicMode()
    with mode() as ctx:
        # 此处 get_model_client 返回真实客户端（temperature=0）
        ...
"""

import logging
import random
import uuid
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

logger = logging.getLogger(__name__)


class _DeterministicUUIDSequence:
    """确定性 UUID 序列生成器

    基于 Random 实例生成可重复的 UUID 序列，
    相同种子下每次调用返回相同序列。
    """

    def __init__(self, seed: int) -> None:
        """初始化 UUID 序列生成器

        Args:
            seed: 随机种子
        """
        self._rng = random.Random(seed)

    def __call__(self) -> uuid.UUID:
        """生成下一个确定性 UUID"""
        return uuid.UUID(int=self._rng.getrandbits(128))


class DeterministicMode:
    """确定性模式上下文管理器

    通过 patch 模型客户端、随机种子和 uuid 生成器，
    保证 pass@k 评估的可重复性。

    两种模式：
      - mock_responses=None：使用真实模型，设置 temperature=0
      - mock_responses 提供时：使用 MockChatCompletionClient 返回预设响应

    Attributes:
        DEFAULT_SEED: 默认随机种子
        DEFAULT_TEMPERATURE: 默认温度（0 表示最确定性）
    """

    DEFAULT_SEED = 42
    DEFAULT_TEMPERATURE = 0.0

    def __init__(
        self,
        seed: int = DEFAULT_SEED,
        mock_responses: dict[str, str] | None = None,
    ) -> None:
        """初始化确定性模式

        Args:
            seed: 随机种子，用于固定 random 和 uuid 序列
            mock_responses: Mock 客户端预设响应（None 时使用真实模型 + temperature=0）
        """
        self._seed = seed
        self._mock_responses = mock_responses
        self._mock_client: Any | None = None

    @contextmanager
    def __call__(self) -> Iterator["DeterministicMode"]:
        """进入确定性模式

        执行以下 patch 操作：
          1. patch model_client.get_model_client 返回确定性客户端
          2. 固定 random.seed
          3. patch uuid.uuid4 返回确定性序列

        Yields:
            确定性上下文（self）
        """
        # 保存原始随机状态，退出时恢复
        original_random_state = random.getstate()
        random.seed(self._seed)

        # 构造确定性 uuid 序列
        uuid_sequence = _DeterministicUUIDSequence(self._seed)
        uuid_patch = patch("uuid.uuid4", side_effect=uuid_sequence)

        # 构造模型客户端 patch
        if self._mock_responses is not None:
            self._mock_client = self.create_mock_client()
            client_patch = patch(
                "agent.core.model.model_client.get_model_client",
                return_value=self._mock_client,
            )
            mode_desc = "mock"
        else:
            # 真实模型模式：返回 temperature=0 的客户端
            client_patch = patch(
                "agent.core.model.model_client.get_model_client",
                side_effect=lambda tier="plus": self._create_zero_temp_client(tier),
            )
            mode_desc = "real-zero-temp"

        with client_patch, uuid_patch:
            logger.debug(
                "进入确定性模式: seed=%d, mode=%s",
                self._seed,
                mode_desc,
            )
            try:
                yield self
            finally:
                # 恢复随机状态
                random.setstate(original_random_state)
                logger.debug("退出确定性模式")

    def create_mock_client(self):
        """创建确定性 Mock 客户端

        复用 tests.mocks.llm_mock.MockChatCompletionClient，
        使用初始化时传入的 mock_responses 作为预设响应。

        Returns:
            MockChatCompletionClient 实例
        """
        from tests.mocks.llm_mock import MockChatCompletionClient

        return MockChatCompletionClient(
            responses=self._mock_responses,
            default_response="确定性模式默认回复。",
        )

    def _create_zero_temp_client(self, tier: str = "plus"):
        """创建 temperature=0 的真实模型客户端

        Args:
            tier: 模型级别（max/plus/turbo）

        Returns:
            OpenAIChatCompletionClient 实例
        """
        from agent.core.model.model_client import MODEL_TIERS, _create_client

        model_name = MODEL_TIERS.get(tier, MODEL_TIERS["plus"])
        return _create_client(model_name, temperature=self.DEFAULT_TEMPERATURE)

    @property
    def mock_client(self) -> Any | None:
        """获取当前 Mock 客户端（仅在 mock 模式且已进入上下文后有效）"""
        return self._mock_client

    @property
    def seed(self) -> int:
        """获取随机种子"""
        return self._seed
