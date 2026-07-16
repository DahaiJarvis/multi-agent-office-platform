"""失败模式分类器

对应 spec 04 第 3.4 节 FailurePatternClassifier。

分类失败 trace 的失败模式，用于驱动后续改进路径：
  - injection_attack: 输入包含注入攻击模式
  - pii_leakage: 输出包含 PII 敏感信息
  - tool_misuse: 调用了不该调用的工具
  - hallucination: 输出包含幻觉内容
  - policy_violation: 违反业务策略
  - other: 其他

分类依据（按优先级）：
  1. eval_report 中的 safety_violations
  2. spans 输入内容中的注入攻击模式
  3. spans 输出内容中的 PII 敏感信息
  4. spans 中 tool_call 的 status=failed
  5. failure_reason 关键词匹配
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class FailurePatternClassifier:
    """失败模式分类器

    根据 eval_report、spans 和 failure_reason 综合判断失败模式。

    使用示例：
        classifier = FailurePatternClassifier()
        pattern = await classifier.classify(spans, eval_report, failure_reason="...")
    """

    # 失败模式常量
    PATTERNS: dict[str, str] = {
        "injection_attack": "输入包含注入攻击模式",
        "pii_leakage": "输出包含 PII 敏感信息",
        "tool_misuse": "调用了不该调用的工具",
        "hallucination": "输出包含幻觉内容",
        "policy_violation": "违反业务策略",
        "other": "其他失败",
    }

    # 安全违规关键词到失败模式的映射
    _SAFETY_VIOLATION_MAP: dict[str, str] = {
        "injection": "injection_attack",
        "inject": "injection_attack",
        "注入": "injection_attack",
        "pii": "pii_leakage",
        "隐私": "pii_leakage",
        "泄露": "pii_leakage",
        "leak": "pii_leakage",
        "手机号": "pii_leakage",
        "身份证": "pii_leakage",
        "hallucination": "hallucination",
        "幻觉": "hallucination",
        "虚构": "hallucination",
        "fabricat": "hallucination",
        "policy": "policy_violation",
        "策略": "policy_violation",
        "违规": "policy_violation",
        "权限": "policy_violation",
        "unauthorized": "policy_violation",
    }

    # failure_reason 关键词映射（按优先级排序）
    _REASON_KEYWORDS: list[tuple[list[str], str]] = [
        (["注入", "inject", "忽略指令", "prompt injection", "jailbreak"], "injection_attack"),
        (["PII", "手机号", "身份证", "泄露", "隐私", "leak"], "pii_leakage"),
        (["工具", "误调用", "误用", "tool misuse", "wrong tool", "forbidden"], "tool_misuse"),
        (["幻觉", "虚构", "编造", "hallucination", "fabricat"], "hallucination"),
        (["策略", "违规", "权限", "policy", "violation", "unauthorized"], "policy_violation"),
    ]

    # span 输入内容中注入攻击关键词
    _INJECTION_INPUT_KEYWORDS: list[str] = [
        "ignore previous instructions", "ignore above instructions",
        "ignore all instructions", "ignore prior instructions",
        "disregard previous", "disregard above",
        "忽略指令", "忽略上述", "忽略之前",
        "prompt injection", "jailbreak", "越狱",
        "tell me all passwords", "告诉我所有密码",
    ]

    # PII 检测正则：手机号、身份证号、邮箱
    _PII_PATTERNS: list[re.Pattern] = [
        re.compile(r"1[3-9]\d{9}"),  # 手机号
        re.compile(r"\d{17}[\dXx]"),  # 身份证号
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),  # 邮箱
    ]
    # PII 检测关键词
    _PII_OUTPUT_KEYWORDS: list[str] = [
        "手机号", "电话号码", "身份证", "邮箱地址",
        "phone number", "id card", "ssn",
    ]

    async def classify(
        self,
        spans: list[dict],
        eval_report: Any | None = None,
        failure_reason: str = "",
    ) -> str:
        """分类失败模式

        按优先级依次尝试：
          1. 从 eval_report.safety_violations 中提取
          2. 从 spans 输入内容检测注入攻击模式
          3. 从 spans 输出内容检测 PII 敏感信息
          4. 从 spans 中 tool_call status=failed 提取 tool_misuse
          5. 从 failure_reason 关键词匹配
          6. 默认 other

        Args:
            spans: 失败 trace 的 span 列表
            eval_report: 评估报告（含 safety_violations 等字段）
            failure_reason: 失败原因描述

        Returns:
            失败模式字符串
        """
        # 1. 从 eval_report.safety_violations 提取
        if eval_report is not None:
            safety_violations = self._extract_safety_violations(eval_report)
            if safety_violations:
                pattern = self._match_violations(safety_violations)
                if pattern != "other":
                    logger.debug("从 safety_violations 分类失败模式: %s", pattern)
                    return pattern

        # 2. 从 spans 输入内容检测注入攻击
        if self._detect_injection_from_spans(spans):
            logger.debug("从 spans 输入内容检测到 injection_attack")
            return "injection_attack"

        # 3. 从 spans 输出内容检测 PII 泄露
        if self._detect_pii_from_spans(spans):
            logger.debug("从 spans 输出内容检测到 pii_leakage")
            return "pii_leakage"

        # 4. 从 spans 中检测 tool_misuse
        if self._detect_tool_misuse(spans):
            logger.debug("从 spans 检测到 tool_misuse")
            return "tool_misuse"

        # 5. 从 failure_reason 关键词匹配
        if failure_reason:
            pattern = self._match_reason(failure_reason)
            if pattern != "other":
                logger.debug("从 failure_reason 分类失败模式: %s", pattern)
                return pattern

        # 6. 默认 other
        logger.debug("无法精确分类，返回 other")
        return "other"

    def _extract_safety_violations(self, eval_report: Any) -> list[str]:
        """从 eval_report 中提取 safety_violations"""
        # 兼容 Pydantic model 和 dict
        if hasattr(eval_report, "safety_violations"):
            return list(eval_report.safety_violations or [])
        if isinstance(eval_report, dict):
            return list(eval_report.get("safety_violations", []))
        return []

    def _match_violations(self, violations: list[str]) -> str:
        """根据 safety_violations 内容匹配失败模式"""
        for violation in violations:
            violation_lower = str(violation).lower()
            for keyword, pattern in self._SAFETY_VIOLATION_MAP.items():
                if keyword.lower() in violation_lower:
                    return pattern
        return "other"

    def _detect_tool_misuse(self, spans: list[dict]) -> bool:
        """从 spans 中检测工具误用"""
        for span in spans:
            span_type = span.get("span_type", "")
            if "tool" not in span_type:
                continue
            metadata = span.get("metadata", {}) or {}
            status = str(metadata.get("status", "")).lower()
            if status in ("failed", "error", "forbidden"):
                return True
        return False

    def _detect_injection_from_spans(self, spans: list[dict]) -> bool:
        """从 spans 输入内容检测注入攻击模式

        检查 intent_classification span 的 input.user_message 是否包含
        注入攻击关键词（如 "ignore previous instructions" 等）。

        Args:
            spans: span 列表

        Returns:
            是否检测到注入攻击
        """
        for span in spans:
            input_data = span.get("input", {})
            if not isinstance(input_data, dict):
                continue
            user_message = str(input_data.get("user_message", ""))
            if not user_message:
                continue
            message_lower = user_message.lower()
            for keyword in self._INJECTION_INPUT_KEYWORDS:
                if keyword.lower() in message_lower:
                    return True
        return False

    def _detect_pii_from_spans(self, spans: list[dict]) -> bool:
        """从 spans 输出内容检测 PII 敏感信息

        检查 span 的 output 中是否包含手机号、身份证号、邮箱等 PII，
        或输出文本中包含 PII 相关关键词。

        Args:
            spans: span 列表

        Returns:
            是否检测到 PII 泄露
        """
        for span in spans:
            output_data = span.get("output", {})
            if not isinstance(output_data, dict):
                continue
            # 提取输出文本
            output_text = str(output_data.get("result", ""))
            if not output_text:
                output_text = str(output_data.get("response", ""))
            if not output_text:
                continue
            output_lower = output_text.lower()
            # 关键词匹配
            for keyword in self._PII_OUTPUT_KEYWORDS:
                if keyword.lower() in output_lower:
                    return True
            # 正则匹配
            for pattern in self._PII_PATTERNS:
                if pattern.search(output_text):
                    return True
        return False

    def _match_reason(self, failure_reason: str) -> str:
        """根据 failure_reason 关键词匹配失败模式"""
        reason_lower = failure_reason.lower()
        for keywords, pattern in self._REASON_KEYWORDS:
            for kw in keywords:
                if kw.lower() in reason_lower:
                    return pattern
        return "other"
