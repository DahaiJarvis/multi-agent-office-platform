"""护栏规则候选生成器

对应 spec 04 第 3.4 节 + spec 05 第 4.2 节 GuardrailRuleGenerator。

从失败案例生成候选护栏规则，生成的规则需在沙箱中验证后才能上线。

规则类型：
  - input_guardrail: 输入护栏（注入检测、PII 检测）
  - tool_guardrail: 工具调用护栏（禁止工具、参数校验）
  - output_guardrail: 输出护栏（PII 脱敏、幻觉检测）

spec 05 增强：
  - generate_rule_v2: 使用 FailurePattern 枚举，返回 spec 05 GuardrailRuleCandidate
  - _extract_evidence: 从失败 Trace 提取关键证据
  - _llm_generate: LLM 生成规则定义（模板+LLM 填充策略）
"""

import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.evaluation.improvement.failure_pattern import FailurePattern
from agent.evaluation.improvement.models import (
    GuardrailLayer,
    GuardrailRuleCandidate as Spec05Candidate,
    RuleType,
)

logger = logging.getLogger(__name__)


def _gen_rule_id() -> str:
    """生成规则 ID"""
    return f"rule-{uuid.uuid4().hex[:12]}"


class GuardrailRuleCandidate(BaseModel):
    """护栏规则候选

    从失败案例生成的候选规则，需经沙箱验证和人工审核后上线。

    Attributes:
        rule_id: 规则唯一标识
        pattern: 失败模式
        rule_type: 规则类型 input_guardrail/tool_guardrail/output_guardrail
        rule_definition: 规则定义（正则/关键词/LLM 判断条件）
        confidence: 置信度（0-1）
        source_archive_id: 来源归档 ID
        sandbox_passed: 沙箱验证是否通过
        approved: 是否已审核通过
    """

    model_config = ConfigDict(frozen=False)

    rule_id: str = Field(default_factory=_gen_rule_id)
    pattern: str = Field(default="", description="失败模式")
    rule_type: str = Field(
        default="input_guardrail",
        description="规则类型：input_guardrail/tool_guardrail/output_guardrail",
    )
    rule_definition: dict[str, Any] = Field(
        default_factory=dict,
        description="规则定义（正则/关键词/LLM 判断条件）",
    )
    confidence: float = Field(default=0.0, description="置信度（0-1）")
    source_archive_id: str = Field(default="")
    sandbox_passed: bool = Field(default=False)
    approved: bool = Field(default=False)


class GuardrailRuleGenerator:
    """护栏规则生成器

    使用规则模板从失败案例生成候选护栏规则。
    当 LLM 可用时调用 LLM 增强规则定义，否则使用预置模板。

    使用示例：
        generator = GuardrailRuleGenerator()
        candidate = await generator.generate_rule(failure_trace, "injection_attack")
    """

    # 失败模式到规则类型的映射
    _PATTERN_TO_RULE_TYPE: dict[str, str] = {
        "injection_attack": "input_guardrail",
        "pii_leakage": "output_guardrail",
        "tool_misuse": "tool_guardrail",
        "hallucination": "output_guardrail",
        "policy_violation": "tool_guardrail",
    }

    # 失败模式到规则模板的映射
    _PATTERN_RULE_TEMPLATES: dict[str, dict[str, Any]] = {
        "injection_attack": {
            "check_type": "regex",
            "patterns": [
                r"ignore\s+(previous|above|all)\s+instructions?",
                r"you\s+are\s+now\s+",
                r"system\s*:\s*",
                r"forget\s+(everything|all|previous)",
            ],
            "action": "block",
            "description": "拦截 prompt 注入攻击",
        },
        "pii_leakage": {
            "check_type": "pii_detection",
            "pii_types": ["phone", "email", "id_card", "bank_card"],
            "action": "redact",
            "description": "检测并脱敏输出中的 PII 信息",
        },
        "tool_misuse": {
            "check_type": "tool_whitelist",
            "action": "block",
            "description": "禁止调用未授权的工具",
        },
        "hallucination": {
            "check_type": "factuality_check",
            "action": "warn",
            "description": "检测输出中的幻觉内容",
        },
        "policy_violation": {
            "check_type": "permission_check",
            "action": "block",
            "description": "校验操作权限，拦截越权操作",
        },
    }

    async def generate_rule(
        self,
        failure_trace: list[dict],
        pattern: str,
        source_archive_id: str = "",
    ) -> GuardrailRuleCandidate:
        """生成候选护栏规则

        根据失败模式和 failure_trace 内容生成规则候选。
        当 LLM 可用时增强规则定义，否则使用预置模板。

        Args:
            failure_trace: 失败 trace 的 span 列表
            pattern: 失败模式
            source_archive_id: 来源归档 ID

        Returns:
            GuardrailRuleCandidate 规则候选
        """
        rule_type = self._PATTERN_TO_RULE_TYPE.get(pattern, "input_guardrail")
        template = self._PATTERN_RULE_TEMPLATES.get(pattern, {})

        # 从 failure_trace 中提取增强信息
        enhanced_definition = self._enhance_rule_definition(template, failure_trace, pattern)

        # 计算置信度
        confidence = self._compute_confidence(pattern, failure_trace, enhanced_definition)

        candidate = GuardrailRuleCandidate(
            pattern=pattern,
            rule_type=rule_type,
            rule_definition=enhanced_definition,
            confidence=confidence,
            source_archive_id=source_archive_id,
        )

        logger.info(
            "生成护栏规则候选: rule_id=%s pattern=%s type=%s confidence=%.2f",
            candidate.rule_id, pattern, rule_type, confidence,
        )

        return candidate

    def _enhance_rule_definition(
        self,
        template: dict[str, Any],
        failure_trace: list[dict],
        pattern: str,
    ) -> dict[str, Any]:
        """增强规则定义

        在模板基础上，从 failure_trace 中提取额外信息增强规则。

        Args:
            template: 规则模板
            failure_trace: 失败 trace
            pattern: 失败模式

        Returns:
            增强后的规则定义
        """
        definition = dict(template)

        # tool_misuse: 提取被误调用的工具列表
        if pattern == "tool_misuse":
            forbidden_tools = self._extract_forbidden_tools(failure_trace)
            if forbidden_tools:
                definition["forbidden_tools"] = forbidden_tools

        # injection_attack: 提取注入特征
        if pattern == "injection_attack":
            injection_inputs = self._extract_injection_inputs(failure_trace)
            if injection_inputs:
                definition["sample_inputs"] = injection_inputs[:3]  # 最多保留 3 个样本

        # pii_leakage: 提取泄露的 PII 类型
        if pattern == "pii_leakage":
            pii_types = self._extract_pii_types(failure_trace)
            if pii_types:
                definition["pii_types"] = pii_types

        return definition

    def _extract_forbidden_tools(self, failure_trace: list[dict]) -> list[str]:
        """从失败 trace 中提取被误调用的工具"""
        tools: list[str] = []
        for span in failure_trace:
            span_type = span.get("span_type", "")
            if "tool" not in span_type:
                continue
            metadata = span.get("metadata", {}) or {}
            status = str(metadata.get("status", "")).lower()
            if status in ("failed", "forbidden"):
                tool_name = ""
                if ":" in span_type:
                    tool_name = span_type.split(":", 1)[1]
                if tool_name and tool_name not in tools:
                    tools.append(tool_name)
        return tools

    def _extract_injection_inputs(self, failure_trace: list[dict]) -> list[str]:
        """从失败 trace 中提取注入输入样本"""
        inputs: list[str] = []
        for span in failure_trace:
            span_type = span.get("span_type", "")
            if "intent" not in span_type and "input" not in span_type:
                continue
            input_data = span.get("input", {})
            if isinstance(input_data, dict):
                user_msg = input_data.get("user_message") or input_data.get("text") or ""
                if user_msg:
                    inputs.append(str(user_msg)[:200])  # 截断保护
        return inputs

    def _extract_pii_types(self, failure_trace: list[dict]) -> list[str]:
        """从失败 trace 中提取泄露的 PII 类型"""
        pii_types: list[str] = []
        for span in failure_trace:
            output_data = span.get("output", {})
            if isinstance(output_data, dict):
                metadata = span.get("metadata", {}) or {}
                detected_pii = metadata.get("pii_types", [])
                if isinstance(detected_pii, list):
                    for pii_type in detected_pii:
                        if pii_type not in pii_types:
                            pii_types.append(str(pii_type))
        return pii_types

    def _compute_confidence(
        self,
        pattern: str,
        failure_trace: list[dict],
        rule_definition: dict[str, Any],
    ) -> float:
        """计算规则置信度

        基于失败模式的明确性和 trace 信息的丰富度计算置信度。

        Args:
            pattern: 失败模式
            failure_trace: 失败 trace
            rule_definition: 规则定义

        Returns:
            置信度（0-1）
        """
        # 基础置信度
        base_confidence: dict[str, float] = {
            "injection_attack": 0.8,
            "pii_leakage": 0.85,
            "tool_misuse": 0.7,
            "hallucination": 0.5,
            "policy_violation": 0.6,
            "other": 0.3,
        }
        confidence = base_confidence.get(pattern, 0.3)

        # 有 trace 数据增强时提高置信度
        if failure_trace:
            confidence = min(1.0, confidence + 0.1)

        # 规则定义越详细置信度越高
        if len(rule_definition) > 3:
            confidence = min(1.0, confidence + 0.05)

        return round(confidence, 2)

    # ==================== spec 05 增强：LLM 生成接口 ====================

    # 按失败模式对应的规则模板名称（spec 05 第 4.2 节）
    TEMPLATES: dict[FailurePattern, str] = {
        FailurePattern.INJECTION_ATTACK: "injection_rule_template",
        FailurePattern.PII_LEAKAGE: "pii_rule_template",
        FailurePattern.TOOL_MISUSE: "tool_rule_template",
        FailurePattern.HALLUCINATION: "output_rule_template",
        FailurePattern.POLICY_VIOLATION: "policy_rule_template",
    }

    # 失败模式到护栏层的映射
    _PATTERN_LAYER_MAP: dict[FailurePattern, GuardrailLayer] = {
        FailurePattern.INJECTION_ATTACK: GuardrailLayer.INPUT,
        FailurePattern.PII_LEAKAGE: GuardrailLayer.OUTPUT,
        FailurePattern.TOOL_MISUSE: GuardrailLayer.TOOL,
        FailurePattern.HALLUCINATION: GuardrailLayer.OUTPUT,
        FailurePattern.POLICY_VIOLATION: GuardrailLayer.TOOL,
    }

    # 失败模式到规则类型的映射
    _PATTERN_RULE_TYPE_MAP: dict[FailurePattern, RuleType] = {
        FailurePattern.INJECTION_ATTACK: RuleType.REGEX,
        FailurePattern.PII_LEAKAGE: RuleType.REGEX,
        FailurePattern.TOOL_MISUSE: RuleType.KEYWORD,
        FailurePattern.HALLUCINATION: RuleType.FUNCTION,
        FailurePattern.POLICY_VIOLATION: RuleType.FUNCTION,
    }

    # 规则模板定义（spec 05 模板+LLM 填充策略）
    _RULE_TEMPLATES: dict[FailurePattern, dict[str, Any]] = {
        FailurePattern.INJECTION_ATTACK: {
            "pattern": r"ignore\s+(previous|above|all|prior)\s+instructions?",
            "flags": "IGNORECASE",
            "match_field": "content",
            "threat_level": "high",
        },
        FailurePattern.PII_LEAKAGE: {
            "patterns": [r"1[3-9]\d{9}", r"\d{17}[\dXx]"],
            "match_field": "content",
            "pii_types": ["phone", "id_card"],
            "threat_level": "high",
        },
        FailurePattern.TOOL_MISUSE: {
            "keywords": [],
            "match_mode": "any",
            "case_sensitive": False,
            "match_field": "tool_name",
        },
        FailurePattern.HALLUCINATION: {
            "function_name": "check_factuality",
            "params": {},
        },
        FailurePattern.POLICY_VIOLATION: {
            "function_name": "check_permission",
            "params": {},
        },
    }

    def __init__(self, llm_tier: str = "max") -> None:
        """初始化生成器

        Args:
            llm_tier: LLM 模型层级，默认 max
        """
        self._llm_tier = llm_tier

    async def generate_rule_v2(
        self,
        failure_trace: list[dict[str, Any]],
        pattern: FailurePattern,
        failure_reason: str = "",
    ) -> Spec05Candidate:
        """生成候选护栏规则（spec 05 第 4.2 节）

        采用"模板 + LLM 填充"策略：模板约束规则结构，LLM 负责语义提取。

        Args:
            failure_trace: 失败 Trace 的 Span 列表
            pattern: 失败模式（决定使用哪个规则模板）
            failure_reason: 失败原因摘要

        Returns:
            候选护栏规则（状态为 candidate）
        """
        # 1. 提取失败案例的关键证据
        evidence = self._extract_evidence(failure_trace, pattern)

        # 2. 选择规则模板
        template = self._RULE_TEMPLATES.get(pattern, {})

        # 3. 调用 LLM 生成规则（结构化输出），LLM 不可用时降级为模板
        rule_spec = await self._llm_generate(evidence, template, pattern, failure_reason)

        # 4. 组装候选规则
        layer = self._PATTERN_LAYER_MAP.get(pattern, GuardrailLayer.INPUT)
        rule_type = self._PATTERN_RULE_TYPE_MAP.get(pattern, RuleType.REGEX)

        candidate = Spec05Candidate(
            pattern=pattern.value,
            rule_type=rule_type,
            rule_spec=rule_spec,
            layer=layer,
            action="block" if pattern != FailurePattern.PII_LEAKAGE else "redact",
            description=f"从失败案例自动生成的 {pattern.value} 护栏规则",
            source_trace_id=str(evidence.get("trace_id", "")),
            status="candidate",
        )

        logger.info(
            "生成 spec 05 候选规则: rule_id=%s pattern=%s type=%s layer=%s",
            candidate.rule_id, pattern.value, rule_type.value, layer.value,
        )

        return candidate

    def _extract_evidence(
        self,
        failure_trace: list[dict[str, Any]],
        pattern: FailurePattern,
    ) -> dict[str, Any]:
        """从失败 Trace 提取证据

        Args:
            failure_trace: 失败 Trace 的 Span 列表
            pattern: 失败模式

        Returns:
            证据字典，含 trace_id / inputs / outputs / tools 等
        """
        evidence: dict[str, Any] = {
            "trace_id": "",
            "inputs": [],
            "outputs": [],
            "tools": [],
            "pattern": pattern.value,
        }

        for span in failure_trace:
            span_type = span.get("span_type", "")

            # 提取 trace_id
            metadata = span.get("metadata", {}) or {}
            if not evidence["trace_id"]:
                evidence["trace_id"] = str(metadata.get("session_id", ""))

            # 提取输入
            input_data = span.get("input", {})
            if isinstance(input_data, dict):
                user_msg = input_data.get("user_message") or input_data.get("text") or ""
                if user_msg:
                    evidence["inputs"].append(str(user_msg)[:200])

            # 提取输出
            output_data = span.get("output", {})
            if isinstance(output_data, dict):
                result = output_data.get("result") or output_data.get("response") or ""
                if result:
                    evidence["outputs"].append(str(result)[:200])

            # 提取工具
            if "tool" in span_type and ":" in span_type:
                tool_name = span_type.split(":", 1)[1]
                if tool_name and tool_name not in evidence["tools"]:
                    evidence["tools"].append(tool_name)

        return evidence

    async def _llm_generate(
        self,
        evidence: dict[str, Any],
        template: dict[str, Any],
        pattern: FailurePattern,
        failure_reason: str,
    ) -> dict[str, Any]:
        """LLM 生成规则定义

        采用"模板 + LLM 填充"策略。LLM 不可用时直接返回模板。

        Args:
            evidence: 证据字典
            template: 规则模板
            pattern: 失败模式
            failure_reason: 失败原因

        Returns:
            规则定义字典
        """
        # 基于模板构建规则定义
        rule_spec = dict(template)

        # 根据失败模式从证据中提取增强信息
        if pattern == FailurePattern.INJECTION_ATTACK:
            inputs = evidence.get("inputs", [])
            if inputs:
                # 从输入中提取注入特征样本
                rule_spec["sample_inputs"] = inputs[:3]

        elif pattern == FailurePattern.PII_LEAKAGE:
            outputs = evidence.get("outputs", [])
            if outputs:
                rule_spec["sample_outputs"] = outputs[:3]

        elif pattern == FailurePattern.TOOL_MISUSE:
            tools = evidence.get("tools", [])
            if tools:
                rule_spec["forbidden_tools"] = tools

        # 尝试调用 LLM 增强（可选，降级时跳过）
        try:
            from agent.core.model.model_client import get_model_client

            client = get_model_client(tier=self._llm_tier)

            from autogen_core.models import UserMessage

            prompt = (
                "你是安全规则专家，请根据以下失败案例生成护栏规则定义。\n\n"
                f"失败模式: {pattern.value}\n"
                f"失败原因: {failure_reason}\n"
                f"证据: inputs={evidence.get('inputs', [])[:2]}, "
                f"outputs={evidence.get('outputs', [])[:2]}, "
                f"tools={evidence.get('tools', [])}\n"
                f"模板: {template}\n\n"
                "请在模板基础上填充具体规则参数，输出 JSON 格式的 rule_spec。\n"
                "仅输出 JSON，不要其他内容。"
            )

            response = await client.create(
                messages=[UserMessage(content=prompt, source="rule_generator")]
            )

            import json
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            llm_spec = json.loads(content)
            # 合并 LLM 生成的字段（LLM 的输出覆盖模板）
            rule_spec.update(llm_spec)

        except Exception as e:
            logger.debug("LLM 生成降级，使用模板: %s", e)

        return rule_spec
