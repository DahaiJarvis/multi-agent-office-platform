"""测试全局 conftest

解决 pydantic 2.13.4 + pytest-cov + mcp 的兼容性问题：
当 pytest-cov 在 collect 阶段插桩时，pydantic.root_model 模块可能尚未
加载到 sys.modules，导致 mcp.types 中 RootModel[...] 泛型子类化失败
（KeyError: 'pydantic.root_model'）。

此处提前显式导入 pydantic.root_model，确保 coverage 插桩前模块已就绪。
该预导入对所有测试无害，仅修复环境兼容性问题。
"""

import pydantic.root_model  # noqa: F401  确保模块加载到 sys.modules
