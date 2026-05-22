"""文件路径安全校验

对工具接收的文件路径进行安全校验，防止路径穿越攻击。

校验规则：
  -------------------------------------------------------------------------
  1. os.path.realpath() 消除符号链接和 ../ 穿越
  2. 白名单目录检查（仅允许访问指定目录下的文件）
  3. 扩展名白名单（仅允许指定类型的文件）
  4. 文件大小限制（防止读取超大文件导致内存溢出）
  -------------------------------------------------------------------------

安全约束：
  - ../../etc/passwd 必须被拒绝
  - 符号链接穿越必须被阻止
  - 白名单外的扩展名必须被拒绝
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_DIRECTORIES: list[str] = [
    "/data/documents",
    "/data/uploads",
    "/tmp/agent_uploads",
]

ALLOWED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx",
}

MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50MB


class PathValidationError(ValueError):
    """路径校验错误

    当文件路径未通过安全校验时抛出，包含具体的错误原因。
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"路径校验失败: {path} - {reason}")


def validate_file_path(file_path: str) -> str:
    """校验文件路径安全性

    执行四层安全校验：
      1. 路径规范化：消除符号链接和 ../ 穿越
      2. 白名单目录检查：确保文件在允许的目录下
      3. 扩展名白名单：确保文件类型允许访问
      4. 文件大小限制：确保文件不超过大小限制

    Args:
        file_path: 待校验的文件路径

    Returns:
        规范化后的安全文件路径

    Raises:
        PathValidationError: 路径未通过安全校验
    """
    if not file_path:
        raise PathValidationError(file_path, "文件路径不能为空")

    # 第1层：路径规范化，消除符号链接和 ../ 穿越
    real_path = os.path.realpath(file_path)

    # 第2层：白名单目录检查
    in_allowed_dir = False
    for allowed_dir in ALLOWED_DIRECTORIES:
        if real_path.startswith(allowed_dir + os.sep) or real_path == allowed_dir:
            in_allowed_dir = True
            break

    if not in_allowed_dir:
        raise PathValidationError(
            file_path,
            f"文件不在允许的目录中，允许的目录: {', '.join(ALLOWED_DIRECTORIES)}",
        )

    # 第3层：扩展名白名单
    _, ext = os.path.splitext(real_path)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise PathValidationError(
            file_path,
            f"不允许的文件扩展名: {ext}，允许的扩展名: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # 第4层：文件大小限制（仅对已存在的文件检查）
    if os.path.isfile(real_path):
        file_size = os.path.getsize(real_path)
        if file_size > MAX_FILE_SIZE_BYTES:
            raise PathValidationError(
                file_path,
                f"文件大小超过限制: {file_size / (1024 * 1024):.1f}MB > {MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f}MB",
            )

    return real_path


def is_path_safe(file_path: str) -> bool:
    """判断文件路径是否安全

    不抛出异常的校验方法，返回布尔值。

    Args:
        file_path: 待校验的文件路径

    Returns:
        True 表示路径安全，False 表示不安全
    """
    try:
        validate_file_path(file_path)
        return True
    except PathValidationError:
        return False
