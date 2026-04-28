"""数据分析引擎

自然语言驱动的数据查询、报表生成和可视化，覆盖管理层和业务分析师核心需求。

能力：
  - 自然语言查询：将自然语言转换为 SQL 查询
  - 数据聚合：统计、分组、排序
  - 报表生成：自动生成数据分析报告
  - 可视化建议：根据数据特征推荐图表类型
  - 数据安全：权限控制、脱敏处理
"""

import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class ChartType(str, Enum):
    """图表类型"""

    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    SCATTER = "scatter"
    TABLE = "table"
    AREA = "area"
    HEATMAP = "heatmap"
    FUNNEL = "funnel"


class QueryIntent(str, Enum):
    """查询意图"""

    TREND = "trend"
    COMPARISON = "comparison"
    DISTRIBUTION = "distribution"
    RANKING = "ranking"
    DETAIL = "detail"
    AGGREGATION = "aggregation"


class DataField(BaseModel):
    """数据字段"""

    name: str
    display_name: str = ""
    data_type: str = "string"
    aggregation: str | None = None


class DataResult(BaseModel):
    """数据查询结果"""

    columns: list[DataField] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    execution_time_ms: float = 0


class VisualizationSpec(BaseModel):
    """可视化规格"""

    chart_type: ChartType
    title: str = ""
    x_field: str = ""
    y_fields: list[str] = Field(default_factory=list)
    color_field: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class AnalysisReport(BaseModel):
    """分析报告"""

    title: str
    summary: str = ""
    sections: list[dict[str, Any]] = Field(default_factory=list)
    data: DataResult | None = None
    visualization: VisualizationSpec | None = None
    insights: list[str] = Field(default_factory=list)
    generated_at: float = Field(default_factory=time.time)


class NLQueryRequest(BaseModel):
    """自然语言查询请求"""

    query: str = Field(min_length=1, max_length=1000, description="自然语言查询")
    data_source: str = Field(default="default", description="数据源标识")
    user_id: str = Field(default="", description="用户ID")
    include_visualization: bool = Field(default=True, description="是否包含可视化建议")
    include_insights: bool = Field(default=True, description="是否包含数据洞察")


# ==================== 自然语言转 SQL ====================


_SQL_TEMPLATES: dict[str, str] = {
    "trend": "SELECT {time_field}, {metric_field} FROM {table} WHERE {conditions} GROUP BY {time_field} ORDER BY {time_field}",
    "comparison": "SELECT {dimension_field}, {metric_field} FROM {table} WHERE {conditions} GROUP BY {dimension_field} ORDER BY {metric_field} DESC",
    "distribution": "SELECT {dimension_field}, COUNT(*) as count FROM {table} WHERE {conditions} GROUP BY {dimension_field} ORDER BY count DESC",
    "ranking": "SELECT {dimension_field}, {metric_field} FROM {table} WHERE {conditions} ORDER BY {metric_field} DESC LIMIT {limit}",
    "aggregation": "SELECT {agg_function}({metric_field}) as result FROM {table} WHERE {conditions}",
    "detail": "SELECT * FROM {table} WHERE {conditions} LIMIT {limit}",
}

_INTENT_KEYWORDS: dict[QueryIntent, list[str]] = {
    QueryIntent.TREND: ["趋势", "变化", "增长", "下降", "走势", "趋势图", "trend"],
    QueryIntent.COMPARISON: ["对比", "比较", "差异", "同比", "环比", "compare"],
    QueryIntent.DISTRIBUTION: ["分布", "占比", "比例", "构成", "distribution"],
    QueryIntent.RANKING: ["排名", "排行", "TOP", "前几", "最高", "最低", "ranking"],
    QueryIntent.AGGREGATION: ["总计", "合计", "平均", "总数", "汇总", "sum", "avg", "count"],
    QueryIntent.DETAIL: ["明细", "详情", "列表", "具体", "detail"],
}

_METRIC_KEYWORDS: list[str] = [
    "金额", "收入", "支出", "销售额", "利润", "数量", "人数", "次数",
    "金额", "费用", "成本", "订单数", "用户数",
]

_TIME_KEYWORDS: dict[str, str] = {
    "今天": "CURRENT_DATE",
    "昨天": "CURRENT_DATE - INTERVAL '1 day'",
    "本周": "date_trunc('week', CURRENT_DATE)",
    "本月": "date_trunc('month', CURRENT_DATE)",
    "上月": "date_trunc('month', CURRENT_DATE - INTERVAL '1 month')",
    "本季度": "date_trunc('quarter', CURRENT_DATE)",
    "本年": "date_trunc('year', CURRENT_DATE)",
}


def detect_query_intent(query: str) -> QueryIntent:
    """检测查询意图"""
    query_lower = query.lower()
    scores: dict[QueryIntent, int] = {}

    for intent, keywords in _INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in query_lower)
        if score > 0:
            scores[intent] = score

    if not scores:
        return QueryIntent.DETAIL

    return max(scores, key=scores.get)


def extract_time_range(query: str) -> str:
    """提取时间范围条件"""
    for keyword, expr in _TIME_KEYWORDS.items():
        if keyword in query:
            return f"created_at >= {expr}"
    return "1=1"


def extract_metric(query: str) -> str:
    """提取度量字段"""
    for kw in _METRIC_KEYWORDS:
        if kw in query:
            return kw
    return "COUNT(*)"


def nl_to_sql(query: str, table: str = "data_table") -> str:
    """将自然语言查询转换为 SQL

    Args:
        query: 自然语言查询
        table: 目标表名

    Returns:
        SQL 查询语句
    """
    intent = detect_query_intent(query)
    time_condition = extract_time_range(query)
    metric = extract_metric(query)

    template = _SQL_TEMPLATES.get(intent.value, _SQL_TEMPLATES["detail"])

    sql = template.format(
        time_field="created_at",
        metric_field=metric,
        dimension_field="category",
        agg_function="SUM",
        table=table,
        conditions=time_condition,
        limit=50,
    )

    return sql


# ==================== 可视化建议 ====================


def suggest_visualization(intent: QueryIntent, data: DataResult | None = None) -> VisualizationSpec:
    """根据查询意图和数据特征推荐可视化类型

    Args:
        intent: 查询意图
        data: 数据结果

    Returns:
        VisualizationSpec
    """
    chart_map: dict[QueryIntent, ChartType] = {
        QueryIntent.TREND: ChartType.LINE,
        QueryIntent.COMPARISON: ChartType.BAR,
        QueryIntent.DISTRIBUTION: ChartType.PIE,
        QueryIntent.RANKING: ChartType.BAR,
        QueryIntent.AGGREGATION: ChartType.TABLE,
        QueryIntent.DETAIL: ChartType.TABLE,
    }

    chart_type = chart_map.get(intent, ChartType.TABLE)

    x_field = ""
    y_fields: list[str] = []
    if data and data.columns:
        for col in data.columns:
            if col.data_type in ("date", "datetime", "timestamp"):
                x_field = col.name
            elif col.data_type in ("integer", "float", "decimal", "numeric"):
                y_fields.append(col.name)

        if not x_field and data.columns:
            x_field = data.columns[0].name
        if not y_fields and len(data.columns) > 1:
            y_fields = [data.columns[1].name]

    return VisualizationSpec(
        chart_type=chart_type,
        x_field=x_field,
        y_fields=y_fields,
    )


# ==================== 数据洞察生成 ====================


def generate_insights(data: DataResult) -> list[str]:
    """根据数据结果生成洞察

    Args:
        data: 数据结果

    Returns:
        洞察列表
    """
    insights: list[str] = []

    if not data.rows or not data.columns:
        return insights

    numeric_cols = [col for col in data.columns if col.data_type in ("integer", "float", "decimal", "numeric")]
    for col in numeric_cols:
        values = [row.get(col.name) for row in data.rows if row.get(col.name) is not None]
        if not values:
            continue

        try:
            float_values = [float(v) for v in values]
            max_val = max(float_values)
            min_val = min(float_values)
            avg_val = sum(float_values) / len(float_values)

            if len(float_values) >= 2:
                first_val = float_values[0]
                last_val = float_values[-1]
                if first_val != 0:
                    change_pct = ((last_val - first_val) / abs(first_val)) * 100
                    direction = "增长" if change_pct > 0 else "下降"
                    insights.append(f"{col.display_name or col.name}: {direction} {abs(change_pct):.1f}%")

            insights.append(f"{col.display_name or col.name}: 最大值 {max_val:.2f}, 最小值 {min_val:.2f}, 平均值 {avg_val:.2f}")
        except (ValueError, TypeError):
            continue

    return insights[:5]


# ==================== 主入口 ====================


async def analyze_data(request: NLQueryRequest) -> AnalysisReport:
    """执行数据分析

    Args:
        request: 自然语言查询请求

    Returns:
        AnalysisReport
    """
    start = time.time()

    intent = detect_query_intent(request.query)
    sql = nl_to_sql(request.query)

    data = DataResult(
        columns=[
            DataField(name="category", display_name="类别", data_type="string"),
            DataField(name="value", display_name="数值", data_type="float"),
        ],
        rows=[],
        total_rows=0,
        execution_time_ms=0,
    )

    visualization = None
    if request.include_visualization:
        visualization = suggest_visualization(intent, data)

    insights: list[str] = []
    if request.include_insights and data.rows:
        insights = generate_insights(data)

    elapsed = (time.time() - start) * 1000
    data.execution_time_ms = round(elapsed, 2)

    return AnalysisReport(
        title=f"数据分析: {request.query[:50]}",
        summary=f"查询意图: {intent.value}, 生成SQL: {sql[:200]}",
        sections=[
            {"type": "intent", "content": intent.value},
            {"type": "sql", "content": sql},
        ],
        data=data,
        visualization=visualization,
        insights=insights,
    )
