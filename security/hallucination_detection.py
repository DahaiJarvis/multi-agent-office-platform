"""幻觉检测与输出质量守卫

在输出侧增加幻觉检测层，提供引用溯源和事实一致性检查。
检测策略：
  1. 引用溯源：检查回复是否包含来源引用
  2. 事实一致性：对比回复与知识库原文的语义相似度
  3. 完整性检查：检测回复是否遗漏关键信息
  4. 时效性标注：标注信息的时效

幻觉检测不阻断正常输出，仅附加警告和置信度，避免误杀。
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    """引用信息"""

    source_type: str       # document / url / knowledge_base
    source_name: str       # 来源名称
    snippet: str           # 引用片段
    relevance_score: float  # 相关度分数 0-1


@dataclass
class HallucinationCheckResult:
    """幻觉检测结果"""

    passed: bool
    confidence: float          # 整体置信度 0-1
    factuality_score: float    # 事实一致性分数 0-1
    has_citations: bool        # 是否包含引用
    citations: list[Citation]  # 引用列表
    completeness_score: float  # 完整性分数 0-1
    warnings: list[str] = field(default_factory=list)


# 引用标记正则模式
CITATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\[来源[：:]\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"\[引用[：:]\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"\[ref[：:]\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"\[source[：:]\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"参考[：:]\s*(.+?)(?:\n|$)", re.IGNORECASE),
    re.compile(r"根据[《]([^》]+)[》]", re.IGNORECASE),
]

# 时效性关键词
TIME_KEYWORDS = [
    "目前", "当前", "最新", "截至", "截至目前", "截至目前为止",
    "今年", "去年", "本月", "上月", "本周", "上周",
    "2024", "2025", "2026",
]


class HallucinationDetector:
    """幻觉检测器

    检测策略：
    1. 引用溯源：检查回复是否包含来源引用
    2. 事实一致性：对比回复与知识库原文的语义相似度
    3. 完整性检查：检测回复是否遗漏关键信息
    4. 时效性标注：标注信息的时效
    """

    # 检测阈值
    FACTUALITY_THRESHOLD = 0.6
    COMPLETENESS_THRESHOLD = 0.5

    async def check(
        self,
        content: str,
        knowledge_context: list[str] | None = None,
        query: str = "",
    ) -> HallucinationCheckResult:
        """执行幻觉检测

        Args:
            content: Agent 输出内容
            knowledge_context: 知识库检索到的原文片段列表
            query: 用户原始查询

        Returns:
            HallucinationCheckResult
        """
        warnings: list[str] = []

        # 1. 引用溯源
        has_citations, citations = self._check_citations(content)
        if not has_citations and knowledge_context:
            warnings.append("回复未包含引用来源，无法验证信息出处")

        # 2. 事实一致性检查
        factuality_score = 1.0
        if knowledge_context:
            factuality_score = await self._check_factuality(content, knowledge_context)
            if factuality_score < self.FACTUALITY_THRESHOLD:
                warnings.append(
                    f"事实一致性较低 ({factuality_score:.0%})，回复内容可能与知识库原文不符"
                )

        # 3. 完整性检查
        completeness_score = 1.0
        if query:
            completeness_score = self._check_completeness(content, query)
            if completeness_score < self.COMPLETENESS_THRESHOLD:
                warnings.append(
                    f"完整性较低 ({completeness_score:.0%})，回复可能遗漏了查询的关键信息"
                )

        # 4. 时效性标注
        timeliness_warnings = self._check_timeliness(content)
        warnings.extend(timeliness_warnings)

        # 综合判定
        confidence = self._calculate_confidence(
            factuality_score, has_citations, completeness_score
        )
        passed = (
            factuality_score >= self.FACTUALITY_THRESHOLD
            and (has_citations or not knowledge_context)
            and completeness_score >= self.COMPLETENESS_THRESHOLD
        )

        return HallucinationCheckResult(
            passed=passed,
            confidence=confidence,
            factuality_score=factuality_score,
            has_citations=has_citations,
            citations=citations,
            completeness_score=completeness_score,
            warnings=warnings,
        )

    def _check_citations(self, content: str) -> tuple[bool, list[Citation]]:
        """检查引用：解析内容中的引用标记

        Args:
            content: Agent 输出内容

        Returns:
            (是否包含引用, 引用列表)
        """
        citations: list[Citation] = []

        for pattern in CITATION_PATTERNS:
            matches = pattern.findall(content)
            for match in matches:
                source_name = match.strip()
                if source_name:
                    # 根据引用内容推断来源类型
                    source_type = "document"
                    if "http" in source_name or "www" in source_name:
                        source_type = "url"
                    elif "知识库" in source_name:
                        source_type = "knowledge_base"

                    citations.append(Citation(
                        source_type=source_type,
                        source_name=source_name,
                        snippet="",
                        relevance_score=0.8,
                    ))

        return len(citations) > 0, citations

    async def _check_factuality(
        self,
        content: str,
        knowledge_context: list[str],
    ) -> float:
        """事实一致性检查

        通过关键词重叠度和语义相似度综合评估回复与知识库原文的一致性。
        不依赖外部 LLM 调用，使用轻量级算法实现。

        Args:
            content: Agent 输出内容
            knowledge_context: 知识库检索到的原文片段列表

        Returns:
            事实一致性分数 0-1
        """
        if not knowledge_context:
            return 1.0

        # 将所有知识库原文合并
        combined_context = " ".join(knowledge_context)

        # 提取关键词（简单分词：按标点和空格切分，过滤短词）
        content_words = self._extract_keywords(content)
        context_words = self._extract_keywords(combined_context)

        if not content_words or not context_words:
            return 0.5

        # 计算关键词重叠率
        content_set = set(content_words)
        context_set = set(context_words)
        overlap = content_set & context_set

        # Jaccard 相似度
        union = content_set | context_set
        jaccard = len(overlap) / len(union) if union else 0

        # 关键词覆盖率（内容关键词在原文中出现的比例）
        coverage = len(overlap) / len(content_set) if content_set else 0

        # 综合分数（覆盖率权重更高，因为更关注回复内容是否可溯源）
        score = 0.4 * jaccard + 0.6 * coverage

        return min(1.0, score)

    def _check_completeness(self, content: str, query: str) -> float:
        """完整性检查

        检测回复是否覆盖了查询的关键点。
        通过提取查询中的关键词，检查回复是否包含这些关键词。

        Args:
            content: Agent 输出内容
            query: 用户原始查询

        Returns:
            完整性分数 0-1
        """
        query_keywords = self._extract_keywords(query)
        if not query_keywords:
            return 1.0

        content_lower = content.lower()
        covered = 0
        for keyword in query_keywords:
            if keyword.lower() in content_lower:
                covered += 1

        return covered / len(query_keywords)

    def _check_timeliness(self, content: str) -> list[str]:
        """时效性检查

        检测内容中的时间相关表述，提醒用户注意信息时效。

        Args:
            content: Agent 输出内容

        Returns:
            时效性警告列表
        """
        warnings: list[str] = []

        found_time_keywords = [kw for kw in TIME_KEYWORDS if kw in content]
        if found_time_keywords:
            warnings.append(
                f"回复包含时效性表述 ({', '.join(found_time_keywords[:3])})，"
                "请注意信息的有效期限"
            )

        return warnings

    def _calculate_confidence(
        self,
        factuality_score: float,
        has_citations: bool,
        completeness_score: float,
    ) -> float:
        """计算整体置信度

        Args:
            factuality_score: 事实一致性分数
            has_citations: 是否包含引用
            completeness_score: 完整性分数

        Returns:
            整体置信度 0-1
        """
        # 引用加成
        citation_bonus = 0.15 if has_citations else 0.0

        # 加权计算
        confidence = (
            0.5 * factuality_score
            + 0.2 * completeness_score
            + 0.15 * (1.0 if has_citations else 0.0)
            + 0.15  # 基础置信度
            + citation_bonus
        )

        return min(1.0, confidence)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """提取文本关键词

        简单分词：按标点和空格切分，过滤短词和停用词。

        Args:
            text: 输入文本

        Returns:
            关键词列表
        """
        # 按标点和空格切分
        words = re.split(r"[，。！？、；：\u201c\u201d\u2018\u2019\s,.\!?;:\"'()\[\]{}<>]+", text)
        # 过滤短词和纯数字
        stop_words = {
            "的", "了", "是", "在", "有", "和", "与", "或", "不", "也",
            "都", "就", "而", "及", "等", "为", "这", "那", "个", "一",
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "can", "could", "may", "might", "shall", "should",
            "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "into", "about",
        }
        keywords = [
            w for w in words
            if len(w) >= 2 and w.lower() not in stop_words and not w.isdigit()
        ]
        return keywords
