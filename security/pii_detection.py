"""扩展型 PII (个人身份信息) 检测引擎

在原有手机号/身份证/邮箱基础上，扩展检测范围：
  - 护照号码（中国/国际格式）
  - 社会保障号 (SSN)
  - 银行卡号
  - 中文姓名（基于常见姓氏库 + 姓名模式）
  - 地址信息（中国地址模式）
  - 车牌号
  - 军官证号
  - 自定义规则（正则 + 关键词）

支持自定义规则热加载，无需重启服务。
"""

import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PIICategory(str, Enum):
    """PII 类别"""

    PHONE = "phone"
    ID_CARD = "id_card"
    EMAIL = "email"
    PASSPORT = "passport"
    SSN = "ssn"
    BANK_CARD = "bank_card"
    NAME = "name"
    ADDRESS = "address"
    LICENSE_PLATE = "license_plate"
    MILITARY_ID = "military_id"
    CUSTOM = "custom"


class PIISensitivity(str, Enum):
    """敏感级别"""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class PIIDetection(BaseModel):
    """单条 PII 检测结果"""

    category: PIICategory
    sensitivity: PIISensitivity
    value: str
    start: int
    end: int
    confidence: float = Field(ge=0.0, le=1.0)
    rule_name: str


class PIIDetectionResult(BaseModel):
    """PII 检测综合结果"""

    has_pii: bool
    detections: list[PIIDetection] = Field(default_factory=list)
    redacted_content: str = ""
    summary: dict[str, int] = Field(default_factory=dict)


class CustomPIIRule(BaseModel):
    """自定义 PII 检测规则"""

    name: str = Field(description="规则名称")
    category: PIICategory = Field(default=PIICategory.CUSTOM)
    sensitivity: PIISensitivity = Field(default=PIISensitivity.CONFIDENTIAL)
    pattern: str = Field(description="正则表达式")
    keywords: list[str] = Field(default_factory=list, description="关键词列表，与正则配合使用")
    enabled: bool = True
    description: str = ""


# ==================== 内置 PII 规则 ====================

# 中国常见姓氏（Top 200）
COMMON_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程"
    "嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗"
    "山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶"
    "郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟谭贡劳"
    "逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连"
    "茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚"
    "越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红"
    "游竺权逯盖益桓公".replace(" ", "")
)


def _luhn_check(number: str) -> bool:
    """Luhn 校验算法，用于银行卡号验证"""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _id_card_check(id_number: str) -> bool:
    """身份证号校验"""
    if len(id_number) != 18:
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    try:
        total = sum(int(id_number[i]) * weights[i] for i in range(17))
        return check_chars[total % 11] == id_number[17].upper()
    except (ValueError, IndexError):
        return False


# 内置检测规则
BUILTIN_RULES: list[dict[str, Any]] = [
    {
        "name": "中国手机号",
        "category": PIICategory.PHONE,
        "sensitivity": PIISensitivity.CONFIDENTIAL,
        "pattern": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "validator": None,
        "confidence": 0.95,
    },
    {
        "name": "中国身份证号",
        "category": PIICategory.ID_CARD,
        "sensitivity": PIISensitivity.RESTRICTED,
        "pattern": r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)",
        "validator": _id_card_check,
        "confidence": 0.98,
    },
    {
        "name": "电子邮箱",
        "category": PIICategory.EMAIL,
        "sensitivity": PIISensitivity.CONFIDENTIAL,
        "pattern": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "validator": None,
        "confidence": 0.9,
    },
    {
        "name": "中国护照号",
        "category": PIICategory.PASSPORT,
        "sensitivity": PIISensitivity.RESTRICTED,
        "pattern": r"(?<![A-Z0-9])[EGDP][EHJKLMSX]\d{8}(?![A-Z0-9])",
        "validator": None,
        "confidence": 0.85,
    },
    {
        "name": "国际护照号",
        "category": PIICategory.PASSPORT,
        "sensitivity": PIISensitivity.RESTRICTED,
        "pattern": r"(?<![A-Z0-9])[A-Z]{1,2}\d{6,9}(?![A-Z0-9])",
        "validator": None,
        "confidence": 0.7,
    },
    {
        "name": "美国SSN",
        "category": PIICategory.SSN,
        "sensitivity": PIISensitivity.RESTRICTED,
        "pattern": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
        "validator": None,
        "confidence": 0.9,
    },
    {
        "name": "银行卡号",
        "category": PIICategory.BANK_CARD,
        "sensitivity": PIISensitivity.RESTRICTED,
        "pattern": r"(?<!\d)(?:62|4\d|5[1-5]|35)\d{13,16}(?!\d)",
        "validator": _luhn_check,
        "confidence": 0.85,
    },
    {
        "name": "中国车牌号",
        "category": PIICategory.LICENSE_PLATE,
        "sensitivity": PIISensitivity.CONFIDENTIAL,
        "pattern": r"(?<![京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁][A-Z])[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁][A-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]",
        "validator": None,
        "confidence": 0.9,
    },
    {
        "name": "军官证号",
        "category": PIICategory.MILITARY_ID,
        "sensitivity": PIISensitivity.RESTRICTED,
        "pattern": r"(?<![A-Z0-9])军字第\d{6,8}号(?![A-Z0-9])",
        "validator": None,
        "confidence": 0.85,
    },
    {
        "name": "中国地址-省市区",
        "category": PIICategory.ADDRESS,
        "sensitivity": PIISensitivity.CONFIDENTIAL,
        "pattern": r"(?:北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆)(?:省|市|自治区|壮族自治区|回族自治区|维吾尔自治区)(?:[\u4e00-\u9fff]{2,6}(?:市|区|县|镇|乡|街道))",
        "validator": None,
        "confidence": 0.75,
    },
    {
        "name": "中国地址-路街号",
        "category": PIICategory.ADDRESS,
        "sensitivity": PIISensitivity.CONFIDENTIAL,
        "pattern": r"[\u4e00-\u9fff]{2,8}(?:路|街|道|巷|弄|胡同)\d{1,5}号?(?:\d{1,3}(?:室|层|栋|号楼))?",
        "validator": None,
        "confidence": 0.7,
    },
]

# 自定义规则存储
_custom_rules: list[CustomPIIRule] = []


def add_custom_rule(rule: CustomPIIRule) -> None:
    """添加自定义 PII 检测规则"""
    try:
        re.compile(rule.pattern)
    except re.error as e:
        raise ValueError(f"无效的正则表达式: {e}")

    _custom_rules.append(rule)
    logger.info("自定义 PII 规则已添加: %s", rule.name)


def remove_custom_rule(name: str) -> bool:
    """移除自定义规则"""
    for i, rule in enumerate(_custom_rules):
        if rule.name == name:
            _custom_rules.pop(i)
            logger.info("自定义 PII 规则已移除: %s", name)
            return True
    return False


def list_custom_rules() -> list[CustomPIIRule]:
    """列出所有自定义规则"""
    return list(_custom_rules)


def _detect_chinese_names(content: str) -> list[PIIDetection]:
    """检测中文姓名

    基于常见姓氏库 + 2-3字姓名模式检测。
    为降低误报率，要求姓名前有特定上下文关键词。
    """
    detections: list[PIIDetection] = []

    name_context_keywords = [
        "姓名", "先生", "女士", "同志", "经理", "总监", "主管",
        "负责人", "联系人", "收件人", "发件人", "申请人", "审批人",
        "先生/", "女士/", "收货人", "顾客", "客户",
    ]

    for keyword in name_context_keywords:
        pattern = re.compile(re.escape(keyword) + r"[：:]\s*([\u4e00-\u9fff]{2,4})")
        for match in pattern.finditer(content):
            name = match.group(1)
            surname = name[0]
            if surname in COMMON_SURNAMES:
                detections.append(PIIDetection(
                    category=PIICategory.NAME,
                    sensitivity=PIISensitivity.CONFIDENTIAL,
                    value=name,
                    start=match.start(1),
                    end=match.end(1),
                    confidence=0.8,
                    rule_name="中文姓名-上下文",
                ))

    return detections


def detect_pii(content: str, categories: set[PIICategory] | None = None) -> PIIDetectionResult:
    """检测文本中的 PII 信息

    Args:
        content: 待检测文本
        categories: 指定检测类别，None 则检测所有类别

    Returns:
        PIIDetectionResult
    """
    all_detections: list[PIIDetection] = []

    # 内置规则检测
    for rule in BUILTIN_RULES:
        if categories and rule["category"] not in categories:
            continue

        pattern = re.compile(rule["pattern"])
        for match in pattern.finditer(content):
            value = match.group()

            if rule.get("validator") and not rule["validator"](value):
                continue

            all_detections.append(PIIDetection(
                category=rule["category"],
                sensitivity=rule["sensitivity"],
                value=value,
                start=match.start(),
                end=match.end(),
                confidence=rule["confidence"],
                rule_name=rule["name"],
            ))

    # 中文姓名检测
    if not categories or PIICategory.NAME in categories:
        name_detections = _detect_chinese_names(content)
        all_detections.extend(name_detections)

    # 自定义规则检测
    for custom_rule in _custom_rules:
        if not custom_rule.enabled:
            continue
        if categories and custom_rule.category not in categories:
            continue

        try:
            pattern = re.compile(custom_rule.pattern)
            for match in pattern.finditer(content):
                value = match.group()

                if custom_rule.keywords:
                    context_start = max(0, match.start() - 50)
                    context_end = min(len(content), match.end() + 50)
                    context = content[context_start:context_end]
                    if not any(kw in context for kw in custom_rule.keywords):
                        continue

                all_detections.append(PIIDetection(
                    category=custom_rule.category,
                    sensitivity=custom_rule.sensitivity,
                    value=value,
                    start=match.start(),
                    end=match.end(),
                    confidence=0.75,
                    rule_name=custom_rule.name,
                ))
        except re.error:
            logger.warning("自定义规则 %s 正则编译失败", custom_rule.name)

    # 去重（同一位置可能被多个规则匹配）
    seen_positions: set[tuple[int, int]] = set()
    unique_detections: list[PIIDetection] = []
    for det in sorted(all_detections, key=lambda d: d.confidence, reverse=True):
        pos = (det.start, det.end)
        if pos not in seen_positions:
            seen_positions.add(pos)
            unique_detections.append(det)

    # 生成脱敏内容
    redacted = _redact_content(content, unique_detections)

    # 统计摘要
    summary: dict[str, int] = {}
    for det in unique_detections:
        key = det.category.value
        summary[key] = summary.get(key, 0) + 1

    return PIIDetectionResult(
        has_pii=len(unique_detections) > 0,
        detections=unique_detections,
        redacted_content=redacted,
        summary=summary,
    )


def _redact_content(content: str, detections: list[PIIDetection]) -> str:
    """脱敏处理

    根据敏感级别采用不同脱敏策略：
    - RESTRICTED: 完全遮盖
    - CONFIDENTIAL: 部分遮盖（保留首尾字符）
    - INTERNAL: 标记但不遮盖
    - PUBLIC: 不处理
    """
    if not detections:
        return content

    result = list(content)
    sorted_detections = sorted(detections, key=lambda d: d.start, reverse=True)

    for det in sorted_detections:
        length = det.end - det.start
        if det.sensitivity == PIISensitivity.RESTRICTED:
            replacement = "[REDACTED]"
        elif det.sensitivity == PIISensitivity.CONFIDENTIAL:
            if length <= 2:
                replacement = "*" * length
            else:
                original = content[det.start:det.end]
                replacement = original[0] + "*" * (length - 2) + original[-1]
        else:
            continue

        result[det.start:det.end] = list(replacement)

    return "".join(result)
