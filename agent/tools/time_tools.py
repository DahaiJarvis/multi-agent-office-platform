"""时间日期原生工具

提供时间查询和日期计算功能，属于瞬时工具（无外部依赖）。

工具列表：
  -------------------------------------------------------------------------
  native_current_time: 查询当前日期时间，支持时区
    - 延迟分层: instant
    - 权限级别: read_only
    - 返回格式: ISO 8601

  native_date_calculate: 日期计算（加减天数/月数，计算间隔）
    - 延迟分层: instant
    - 权限级别: read_only
    - 支持负数（过去的日期）
  -------------------------------------------------------------------------

注册方式：立即注册（无外部依赖）
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from autogen_core.tools import FunctionTool

from agent.tools.base import LatencyTier, NativeToolMeta, PermissionLevel
from agent.tools.registry import NativeToolRegistry

logger = logging.getLogger(__name__)


async def _current_time(timezone: str = "Asia/Shanghai") -> str:
    """查询当前日期时间

    返回 ISO 8601 格式的当前时间，支持指定时区。

    Args:
        timezone: 时区名称，默认为 Asia/Shanghai

    Returns:
        JSON 格式的当前时间信息
    """
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo(timezone)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")

    now = datetime.now(tz)
    result = {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": timezone,
        "weekday": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()],
        "timestamp": int(now.timestamp()),
    }
    return json.dumps(result, ensure_ascii=False)


async def _date_calculate(
    base_date: str = "",
    operation: str = "add",
    value: int = 0,
    unit: str = "days",
) -> str:
    """日期计算

    对指定日期进行加减运算，或计算两个日期之间的间隔。

    Args:
        base_date: 基准日期，格式 YYYY-MM-DD，为空时使用当前日期
        operation: 操作类型，add(加) / subtract(减) / diff(计算间隔)
        value: 计算值，正数表示未来，负数表示过去
        unit: 计算单位，days(天) / months(月) / years(年)

    Returns:
        JSON 格式的计算结果
    """
    if base_date:
        try:
            base = datetime.strptime(base_date, "%Y-%m-%d")
        except ValueError:
            return json.dumps({"error": f"日期格式错误: {base_date}，请使用 YYYY-MM-DD 格式"}, ensure_ascii=False)
    else:
        base = datetime.now()

    if operation == "diff":
        if not base_date:
            return json.dumps({"error": "计算日期间隔需要提供 base_date 和 target_date"}, ensure_ascii=False)
        return json.dumps({"error": "计算日期间隔需要 target_date 参数，请使用 add/subtract 操作"}, ensure_ascii=False)

    actual_value = value if operation == "add" else -value

    if unit == "days":
        result_date = base + timedelta(days=actual_value)
    elif unit == "months":
        month = base.month + actual_value
        year = base.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(base.day, _days_in_month(year, month))
        result_date = base.replace(year=year, month=month, day=day)
    elif unit == "years":
        year = base.year + actual_value
        day = min(base.day, _days_in_month(year, base.month))
        result_date = base.replace(year=year, month=base.month, day=day)
    else:
        return json.dumps({"error": f"不支持的计算单位: {unit}，请使用 days/months/years"}, ensure_ascii=False)

    diff_days = (result_date - base).days
    result = {
        "base_date": base.strftime("%Y-%m-%d"),
        "result_date": result_date.strftime("%Y-%m-%d"),
        "weekday": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][result_date.weekday()],
        "operation": operation,
        "value": value,
        "unit": unit,
        "diff_days": diff_days,
    }
    return json.dumps(result, ensure_ascii=False)


def _days_in_month(year: int, month: int) -> int:
    """获取指定年份月份的天数

    Args:
        year: 年份
        month: 月份

    Returns:
        该月的天数
    """
    import calendar
    return calendar.monthrange(year, month)[1]


_CURRENT_TIME_META = NativeToolMeta(
    name="native_current_time",
    display_name="当前时间查询",
    description="查询当前日期时间，支持指定时区。返回 ISO 8601 格式的时间信息，包含日期、时间、时区、星期和 Unix 时间戳。",
    category="system",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "时区名称，如 Asia/Shanghai、America/New_York、Europe/London",
                "default": "Asia/Shanghai",
            },
        },
        "required": [],
    },
    latency_tier=LatencyTier.INSTANT,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=5,
    requires_llm=False,
    tags=["time", "date", "query"],
)

_DATE_CALCULATE_META = NativeToolMeta(
    name="native_date_calculate",
    display_name="日期计算",
    description="对指定日期进行加减运算。支持加减天数、月数、年数，支持负数（过去的日期）。返回计算后的日期、星期和与基准日期的天数差。",
    category="system",
    parameters={
        "type": "object",
        "properties": {
            "base_date": {
                "type": "string",
                "description": "基准日期，格式 YYYY-MM-DD，为空时使用当前日期",
                "default": "",
            },
            "operation": {
                "type": "string",
                "description": "操作类型: add(加) / subtract(减)",
                "enum": ["add", "subtract"],
                "default": "add",
            },
            "value": {
                "type": "integer",
                "description": "计算值，正整数",
                "default": 0,
            },
            "unit": {
                "type": "string",
                "description": "计算单位: days(天) / months(月) / years(年)",
                "enum": ["days", "months", "years"],
                "default": "days",
            },
        },
        "required": [],
    },
    latency_tier=LatencyTier.INSTANT,
    permission_level=PermissionLevel.READ_ONLY,
    timeout_seconds=5,
    requires_llm=False,
    tags=["time", "date", "calculate"],
)


def register_all(registry: NativeToolRegistry) -> None:
    """注册所有时间日期工具

    时间日期工具无外部依赖，使用立即注册模式。

    Args:
        registry: 工具注册中心实例
    """
    current_time_tool = FunctionTool(
        func=_current_time,
        name="native_current_time",
        description=_CURRENT_TIME_META.description,
    )
    registry.register(current_time_tool, _CURRENT_TIME_META)

    date_calculate_tool = FunctionTool(
        func=_date_calculate,
        name="native_date_calculate",
        description=_DATE_CALCULATE_META.description,
    )
    registry.register(date_calculate_tool, _DATE_CALCULATE_META)

    logger.debug("时间日期工具注册完成: native_current_time, native_date_calculate")
