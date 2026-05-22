"""技能依赖解析器

解析技能包的依赖声明（dependencies.yaml），检查依赖的技能、
工具和 MCP Server 是否满足要求，提供完整的依赖解析结果。

解析流程：
  1. 加载技能的 dependencies.yaml
  2. 检查依赖的技能是否已安装且版本满足要求
  3. 检查依赖的工具是否在 MCP 注册表或原生工具注册表中
  4. 检查依赖的 MCP Server 是否在线
  5. 缺少必需依赖时拒绝安装，缺少可选依赖时降级运行

版本匹配规则：
  - "1.0.0": 精确匹配
  - ">=1.0.0": 大于等于
  - ">=1.0.0,<2.0.0": 范围匹配
  - "": 任意版本
"""

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from agent.core.skill_adapter import SkillRegistry, SkillDependency

logger = logging.getLogger(__name__)


class DependencyStatus(BaseModel):
    """单个依赖的状态"""

    name: str = Field(description="依赖名称")
    dep_type: str = Field(description="依赖类型: skill/tool/mcp_server")
    required: bool = Field(default=True, description="是否必需")
    satisfied: bool = Field(default=False, description="是否满足")
    installed_version: str = Field(default="", description="已安装版本")
    required_version: str = Field(default="", description="要求版本")
    message: str = Field(default="", description="状态说明")


class DependencyResolutionResult(BaseModel):
    """依赖解析结果"""

    skill_name: str = Field(description="技能名称")
    resolvable: bool = Field(default=True, description="是否可解析（所有必需依赖满足）")
    dependencies: list[DependencyStatus] = Field(default_factory=list, description="依赖状态列表")
    missing_required: list[str] = Field(default_factory=list, description="缺失的必需依赖")
    missing_optional: list[str] = Field(default_factory=list, description="缺失的可选依赖")
    warnings: list[str] = Field(default_factory=list, description="警告信息")


def _parse_version(version_str: str) -> tuple[int, ...]:
    """解析语义化版本号为元组

    Args:
        version_str: 版本号字符串（如 "1.2.3"）

    Returns:
        版本号元组（如 (1, 2, 3)）
    """
    parts = []
    for part in version_str.strip().split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _check_version_constraint(version: str, constraint: str) -> bool:
    """检查版本号是否满足约束条件

    支持的约束格式：
      - "": 任意版本
      - "1.0.0": 精确匹配
      - ">=1.0.0": 大于等于
      - ">1.0.0": 大于
      - "<=2.0.0": 小于等于
      - "<2.0.0": 小于
      - ">=1.0.0,<2.0.0": 范围匹配（逗号分隔多个条件）

    Args:
        version: 实际版本号
        constraint: 版本约束条件

    Returns:
        是否满足约束
    """
    if not constraint:
        return True

    version_tuple = _parse_version(version)

    constraints = [c.strip() for c in constraint.split(",")]

    for single_constraint in constraints:
        if not single_constraint:
            continue

        match = re.match(r"^(>=|>|<=|<|==|=)?(.+)$", single_constraint)
        if not match:
            continue

        operator = match.group(1) or "=="
        target = _parse_version(match.group(2))

        if operator in (">=", "=>"):
            if version_tuple < target:
                return False
        elif operator == ">":
            if version_tuple <= target:
                return False
        elif operator in ("<=", "=<"):
            if version_tuple > target:
                return False
        elif operator == "<":
            if version_tuple >= target:
                return False
        elif operator in ("==", "="):
            if version_tuple != target:
                return False

    return True


def _check_skill_dependency(
    dep_info: dict[str, Any],
    registry: SkillRegistry,
) -> DependencyStatus:
    """检查技能依赖是否满足

    Args:
        dep_info: 依赖信息（name, version, required）
        registry: Skill 注册表

    Returns:
        DependencyStatus
    """
    dep_name = dep_info.get("name", "")
    required_version = str(dep_info.get("version", ""))
    required = dep_info.get("required", True)

    status = DependencyStatus(
        name=dep_name,
        dep_type="skill",
        required=required,
        required_version=required_version,
    )

    doc = registry.get(dep_name)
    if doc is None:
        status.satisfied = False
        status.message = f"技能 {dep_name} 未安装"
        return status

    installed_version = doc.manifest.version
    status.installed_version = installed_version

    if _check_version_constraint(installed_version, required_version):
        status.satisfied = True
        status.message = f"技能 {dep_name}@{installed_version} 满足要求"
    else:
        status.satisfied = False
        status.message = f"技能 {dep_name}@{installed_version} 不满足版本要求 {required_version}"

    return status


def _check_tool_dependency(
    dep_info: dict[str, Any],
) -> DependencyStatus:
    """检查工具依赖是否满足

    Args:
        dep_info: 依赖信息（name, required）

    Returns:
        DependencyStatus
    """
    tool_name = dep_info.get("name", "")
    required = dep_info.get("required", True)

    status = DependencyStatus(
        name=tool_name,
        dep_type="tool",
        required=required,
    )

    # 检查原生工具注册表
    try:
        from agent.tools.registry import get_tool_registry
        tool_reg = get_tool_registry()
        if tool_reg and tool_reg.get_tool(tool_name):
            status.satisfied = True
            status.message = f"工具 {tool_name} 已注册（原生工具）"
            return status
    except Exception:
        pass

    # 检查 MCP 工具注册表
    try:
        from agent.core.mcp_integration import get_mcp_registry
        mcp_reg = get_mcp_registry()
        if mcp_reg:
            for server_tools in mcp_reg.values():
                if tool_name in server_tools:
                    status.satisfied = True
                    status.message = f"工具 {tool_name} 已注册（MCP 工具）"
                    return status
    except Exception:
        pass

    status.satisfied = False
    status.message = f"工具 {tool_name} 未注册"
    return status


async def _check_mcp_server_dependency(
    server_name: str,
) -> DependencyStatus:
    """检查 MCP Server 依赖是否满足

    Args:
        server_name: MCP Server 名称

    Returns:
        DependencyStatus
    """
    status = DependencyStatus(
        name=server_name,
        dep_type="mcp_server",
        required=True,
    )

    try:
        from agent.core.mcp_integration import get_mcp_registry
        mcp_reg = get_mcp_registry()
        if mcp_reg and server_name in mcp_reg:
            status.satisfied = True
            status.message = f"MCP Server {server_name} 在线"
        else:
            status.satisfied = False
            status.message = f"MCP Server {server_name} 不可用"
    except Exception as e:
        status.satisfied = False
        status.message = f"MCP Server {server_name} 检查失败: {e}"

    return status


async def resolve_dependencies(skill_name: str) -> DependencyResolutionResult:
    """解析技能的依赖关系

    检查技能声明的所有依赖是否满足，返回完整的解析结果。

    Args:
        skill_name: 技能名称

    Returns:
        DependencyResolutionResult 依赖解析结果
    """
    registry = SkillRegistry.get_instance()
    if not registry._loaded:
        registry.load_all()

    result = DependencyResolutionResult(skill_name=skill_name)

    # 获取依赖声明
    dependency = registry.get_pack_dependencies(skill_name)
    if not dependency.skills and not dependency.tools and not dependency.mcp_servers:
        result.resolvable = True
        return result

    # 检查技能依赖
    for dep_info in dependency.skills:
        status = _check_skill_dependency(dep_info, registry)
        result.dependencies.append(status)
        if not status.satisfied:
            if status.required:
                result.missing_required.append(status.name)
            else:
                result.missing_optional.append(status.name)

    # 检查工具依赖
    for dep_info in dependency.tools:
        status = _check_tool_dependency(dep_info)
        result.dependencies.append(status)
        if not status.satisfied:
            if status.required:
                result.missing_required.append(status.name)
            else:
                result.missing_optional.append(status.name)

    # 检查 MCP Server 依赖
    for server_name in dependency.mcp_servers:
        status = await _check_mcp_server_dependency(server_name)
        result.dependencies.append(status)
        if not status.satisfied:
            result.missing_required.append(status.name)

    # 判断是否可解析
    result.resolvable = len(result.missing_required) == 0

    # 生成警告
    if result.missing_optional:
        result.warnings.append(
            f"缺少可选依赖: {', '.join(result.missing_optional)}，将降级运行"
        )

    logger.info(
        "技能依赖解析: %s resolvable=%s missing_required=%d missing_optional=%d",
        skill_name, result.resolvable,
        len(result.missing_required), len(result.missing_optional),
    )

    return result
