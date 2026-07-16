"""回放专用工具客户端

在回放过程中替代真实的 MCP 工具客户端，确保回放不会产生
任何真实副作用（如发送邮件、创建审批、修改日历等）。

安全保证：
    - 所有工具调用返回预设的回放响应
    - 不建立任何网络连接
    - 不访问任何真实数据源
    - 响应基于原执行记录中的 output_data 生成

对应规格文档：docs/spec/03-检查点时间旅行-spec.md 第 7.2 节
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReplayToolClient:
    """回放专用工具客户端

    在回放过程中替代真实的 MCP 工具客户端，确保回放不会产生
    任何真实副作用（如发送邮件、创建审批、修改日历等）。

    安全保证：
        - 所有工具调用返回预设的回放响应
        - 不建立任何网络连接
        - 不访问任何真实数据源
        - 响应基于原执行记录中的 output_data 生成
    """

    def __init__(self, original_outputs: dict[str, Any]) -> None:
        """初始化回放工具客户端

        Args:
            original_outputs: 原执行记录中各步骤的输出数据，
                              用于生成一致的回放响应。
                              键为工具名称或步骤名称，值为输出数据字典。
        """
        self._original_outputs = original_outputs
        # 调用计数，用于审计与日志
        self._call_count: int = 0

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """调用工具（回放模式）

        返回预设的回放响应，不产生任何真实副作用。

        Args:
            tool_name: 工具名称
            arguments: 调用参数

        Returns:
            回放响应字典
        """
        self._call_count += 1
        logger.info(
            "ReplayToolClient 工具调用(count=%d): tool=%s args_keys=%s",
            self._call_count,
            tool_name,
            list(arguments.keys()) if arguments else [],
        )

        # 优先按工具名称匹配预设输出
        if tool_name in self._original_outputs:
            return self._original_outputs[tool_name]

        # 通用回放响应：标记为回放结果
        return {
            "status": "mocked",
            "tool": tool_name,
            "message": "回放模式 Mock 响应，无真实副作用",
            "mock_call_count": self._call_count,
        }

    @property
    def call_count(self) -> int:
        """返回总调用次数"""
        return self._call_count
