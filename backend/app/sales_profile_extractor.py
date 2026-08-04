from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.sales_config import SalesConfigBundle, sales_config
from app.sales_models import Language, SalesProfileExtraction

_SPACE = re.compile(r"\s+")
_CLAUSE_END = r"(?=$|[，。！？；;,.!?]|\s+(?:但是|不过|然后|and|but|then)\b)"
_DEMO_PATTERN = re.compile(
    r"演示|展示|给我看|现场看|马上看|试试看|体验一下|\bdemo(?:nstrate|nstration)?\b|"
    r"\bshow\s+me\b|\blet\s+me\s+see\b|\btry\s+it\b",
    re.I,
)
_DIRECT_OPERATION_PATTERN = re.compile(
    r"(?:打开|关闭|关掉|启动|退出|下一页|上一页|跳到第|调到|设置为|播放音乐|停止音乐|"
    r"创建.{0,12}草稿|发送.{0,12}邮件|开始录音|停止录音|保存录音|打开登记|打开预约)|"
    r"\b(?:open|close|launch|quit|next\s+slide|previous\s+slide|go\s+to\s+slide|"
    r"set\s+(?:the\s+)?volume|play\s+music|stop\s+music|create\s+(?:an?\s+)?draft|"
    r"send\s+(?:the\s+)?email|start\s+recording|stop\s+recording)\b",
    re.I,
)
_AFFIRM = re.compile(
    r"^(?:可以|好|好的|行|愿意|没问题|当然|试一下|体验一下|是的|对|yes|yeah|yep|sure|okay|ok|"
    r"why\s+not|please\s+do|go\s+ahead)[。.!！ ]*$",
    re.I,
)
_REJECT = re.compile(
    r"^(?:不|不用|不用了|不了|不需要|暂时不用|算了|没兴趣|只是看看|只是路过|no|nope|"
    r"no\s+thanks|not\s+now|maybe\s+later|not\s+interested|just\s+looking)[。.!！ ]*$",
    re.I,
)
_COST = re.compile(r"价格|价钱|成本|费用|多少钱|贵不贵|便宜|\bprice\b|\bcost\b|expensive|cheap|how\s+much", re.I)
_PRIVACY = re.compile(r"隐私|泄露|数据安全|保存什么|人脸|录音保存|\bprivacy\b|data\s+(?:security|leak)|what\s+do\s+you\s+save|face\s+data", re.I)
_CHATBOT = re.compile(r"普通\s*(?:ChatGPT|聊天机器人)|只是.{0,4}聊天机器人|有什么区别|just\s+a\s+chatbot|ordinary\s+chatgpt|what(?:'s|\s+is)\s+different", re.I)
_BOOKING = re.compile(r"预约|约个时间|安排时间|到公司体验|完整体验|book(?:ing)?|schedule(?:\s+a)?\s+(?:meeting|demo)|appointment", re.I)
_CONTACT = re.compile(r"联系方式|登记信息|留下.{0,6}(?:电话|邮箱|信息)|联系我|contact\s+details|register\s+my\s+details|leave\s+(?:my\s+)?(?:email|phone|details)", re.I)
_NEGATED_BOOKING = re.compile(r"不(?:用|想|需要).{0,8}预约|不预约|no\s+(?:booking|appointment)|do\s+not\s+(?:book|schedule)", re.I)
_NEGATED_CONTACT = re.compile(r"不(?:用|想|需要).{0,8}(?:登记|留|提供).{0,8}(?:信息|联系方式|电话|邮箱)|不登记|do\s+not\s+(?:register|leave|share).{0,10}(?:details|email|phone)", re.I)
_DISENGAGED = re.compile(r"不需要|不用介绍|别介绍了|没兴趣|只是路过|只是看看|stop\s+explaining|not\s+interested|just\s+looking|no\s+need", re.I)
_DECLINE_INDUSTRY = re.compile(r"不方便.{0,8}(?:行业|公司)|不想说.{0,8}(?:行业|公司)|prefer\s+not\s+to\s+say.{0,12}(?:industry|company)", re.I)
_DECLINE_ROLE = re.compile(r"不方便.{0,8}(?:职责|职位|工作)|不想说.{0,8}(?:职责|职位|工作)|prefer\s+not\s+to\s+say.{0,12}(?:role|job)", re.I)
_DECLINE_PAIN = re.compile(r"没有(?:什么)?(?:办公)?痛点|不想说.{0,8}(?:问题|痛点)|prefer\s+not\s+to\s+say.{0,12}(?:problem|pain\s+point)", re.I)


def _clean(value: str, maximum: int = 300) -> str:
    return _SPACE.sub(" ", str(value or "").strip()).strip(" ，。！？；;,.!?:：")[:maximum]


def _unique(values: Iterable[str], maximum: int = 20) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = _clean(value)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= maximum:
            break
    return result


def _capture(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = _clean(match.group("value"), 160)
        if value:
            return value
    return None


_INDUSTRY_PATTERNS = (
    re.compile(rf"(?:我们(?:公司)?|本公司)(?:主要)?(?:是做|做|从事|属于)\s*(?P<value>[^，。！？；;,.!?]{{2,40}}?){_CLAUSE_END}", re.I),
    re.compile(r"我(?:在|属于)\s*(?P<value>[^，。！？；;,.!?]{2,30}?)(?:行业|领域)", re.I),
    re.compile(rf"(?:our\s+company|we)(?:\s+mainly)?\s+(?:works?|operates?|is)\s+(?:in|within)\s+(?P<value>[^,.!?;]{{2,60}}?){_CLAUSE_END}", re.I),
    re.compile(rf"I\s+work\s+in\s+(?P<value>[^,.!?;]{{2,60}}?){_CLAUSE_END}", re.I),
)
_ROLE_PATTERNS = (
    re.compile(rf"我(?:主要)?(?:负责|担任|的职位是|的工作是)\s*(?P<value>[^，。！？；;,.!?]{{2,40}}?){_CLAUSE_END}", re.I),
    re.compile(r"我是(?:一名|一个)?\s*(?P<value>经理|主管|负责人|工程师|顾问|销售|行政|教师|学生|老板|创始人|产品经理|项目经理|运营经理)", re.I),
    re.compile(rf"(?:my\s+role\s+is|I\s+am\s+(?:an?|the)|I\s+work\s+as)\s+(?P<value>[^,.!?;]{{2,60}}?){_CLAUSE_END}", re.I),
    re.compile(rf"I\s+(?:mainly\s+)?(?:manage|handle|lead|look\s+after|am\s+responsible\s+for)\s+(?P<value>[^,.!?;]{{2,60}}?){_CLAUSE_END}", re.I),
)
_COMPANY_TYPE_PATTERNS = (
    re.compile(r"我们(?:公司)?是(?:一家|一个)?\s*(?P<value>初创公司|创业公司|小企业|中小企业|大型企业|跨国公司|政府部门|学校|大学|研究机构|非营利组织)", re.I),
    re.compile(r"we\s+are\s+(?:an?|a)\s+(?P<value>startup|small business|medium-sized company|large enterprise|multinational|government department|university|research organisation|non-profit)", re.I),
)
_GENERIC_PAIN = re.compile(
    rf"(?:最大的问题是|最麻烦的是|最浪费时间的是|我们的问题是|痛点是|头疼的是|struggle\s+with|biggest\s+problem\s+is|takes?\s+too\s+much\s+time)\s*(?P<value>[^，。！？；;,.!?]{{2,100}}?){_CLAUSE_END}",
    re.I,
)


@dataclass(frozen=True)
class _MatchedCapability:
    capability_id: str
    matched_keyword: str


class SalesProfileExtractor:
    """Deterministic, explicit-only sales extraction.

    The extractor never estimates demographic, emotional, financial or authority
    attributes. It records only text the visitor actually said or an approved
    catalog/playbook keyword directly present in that text.
    """

    @staticmethod
    def _bundle() -> SalesConfigBundle:
        return sales_config.require_valid()

    @staticmethod
    def _capability_matches(text: str, language: Language, bundle: SalesConfigBundle) -> list[_MatchedCapability]:
        folded = text.casefold()
        matches: list[_MatchedCapability] = []
        for capability in bundle.capabilities.capabilities:
            for keyword in capability.keywords.get(language, []):
                clean_keyword = _clean(keyword)
                if clean_keyword and clean_keyword.casefold() in folded:
                    matches.append(_MatchedCapability(capability.capability_id, clean_keyword))
                    break
        return matches

    @staticmethod
    def _playbook_matches(text: str, language: Language, bundle: SalesConfigBundle) -> tuple[list[str], list[str]]:
        folded = text.casefold()
        playbook_ids: list[str] = []
        evidence: list[str] = []
        for playbook in bundle.playbooks.playbooks:
            signals = playbook.get("signals", {}).get(language, [])
            for signal in signals:
                clean_signal = _clean(signal)
                if clean_signal and clean_signal.casefold() in folded:
                    playbook_id = str(playbook.get("playbook_id") or "").strip()
                    if playbook_id and playbook_id not in playbook_ids:
                        playbook_ids.append(playbook_id)
                    evidence.append(f"playbook_signal:{clean_signal}")
                    break
        return playbook_ids, evidence

    @staticmethod
    def _objection_matches(text: str, language: Language, bundle: SalesConfigBundle) -> tuple[list[str], list[str]]:
        folded = text.casefold()
        objections: list[str] = []
        evidence: list[str] = []
        for objection in bundle.playbooks.objections:
            for signal in objection.get("signals", {}).get(language, []):
                clean_signal = _clean(signal)
                if clean_signal and clean_signal.casefold() in folded:
                    objection_id = str(objection.get("objection_id") or "").strip()
                    if objection_id and objection_id not in objections:
                        objections.append(objection_id)
                    evidence.append(f"objection_signal:{clean_signal}")
                    break
        return objections, evidence

    def extract(
        self,
        text: str,
        *,
        language: Language,
        recent_context: str = "",
    ) -> SalesProfileExtraction:
        clean = _clean(text, 12_000)
        context = _clean(recent_context, 20_000)
        bundle = self._bundle()
        evidence: list[str] = []
        explicit_facts: dict[str, str] = {}

        industry = _capture(_INDUSTRY_PATTERNS, clean)
        if industry:
            explicit_facts["industry"] = industry
            evidence.append("explicit_industry")
        role = _capture(_ROLE_PATTERNS, clean)
        if role:
            explicit_facts["role"] = role
            evidence.append("explicit_role")
        company_type = _capture(_COMPANY_TYPE_PATTERNS, clean)
        if company_type:
            explicit_facts["company_type"] = company_type
            evidence.append("explicit_company_type")

        declined_fields: list[str] = []
        if _DECLINE_INDUSTRY.search(clean):
            declined_fields.extend(["industry", "company_type"])
            evidence.append("declined_industry_or_company")
        if _DECLINE_ROLE.search(clean):
            declined_fields.append("role")
            evidence.append("declined_role")
        if _DECLINE_PAIN.search(clean):
            declined_fields.append("office_pain_points")
            evidence.append("declined_office_pain_points")

        capability_matches = self._capability_matches(clean, language, bundle)
        interested_capabilities = _unique(item.capability_id for item in capability_matches)
        evidence.extend(f"capability_keyword:{item.matched_keyword}" for item in capability_matches)

        matched_playbooks, playbook_evidence = self._playbook_matches(clean, language, bundle)
        evidence.extend(playbook_evidence)
        pain_points: list[str] = []
        generic_pain = _GENERIC_PAIN.search(clean)
        if generic_pain:
            pain_points.append(_clean(generic_pain.group("value")))
            evidence.append("explicit_pain_statement")
        for playbook_id in matched_playbooks:
            if playbook_id not in pain_points:
                pain_points.append(playbook_id)

        objections, objection_evidence = self._objection_matches(clean, language, bundle)
        evidence.extend(objection_evidence)

        demo_requested = bool(_DEMO_PATTERN.search(clean))
        demo_capability_id = capability_matches[0].capability_id if demo_requested and capability_matches else None
        if demo_requested:
            evidence.append("explicit_demo_request")

        brief_affirmation = bool(_AFFIRM.fullmatch(clean))
        brief_rejection = bool(_REJECT.fullmatch(clean))
        context_folded = context.casefold()
        booking_in_context = bool(_BOOKING.search(context_folded))
        contact_in_context = bool(_CONTACT.search(context_folded))

        booking_intent = "none"
        if _NEGATED_BOOKING.search(clean) or (brief_rejection and booking_in_context):
            booking_intent = "reject"
        elif _BOOKING.search(clean):
            booking_intent = "direct"
        elif brief_affirmation and booking_in_context:
            booking_intent = "accept"

        contact_intent = "none"
        if _NEGATED_CONTACT.search(clean) or (brief_rejection and contact_in_context):
            contact_intent = "reject"
        elif _CONTACT.search(clean):
            contact_intent = "direct"
        elif brief_affirmation and contact_in_context:
            contact_intent = "accept"

        cost_question = bool(_COST.search(clean))
        privacy_question = bool(_PRIVACY.search(clean))
        ordinary_chatbot_objection = bool(_CHATBOT.search(clean))
        disengaged = bool(_DISENGAGED.search(clean)) or (
            brief_rejection and not booking_in_context and not contact_in_context
        )

        direct_operational_command = bool(
            _DIRECT_OPERATION_PATTERN.search(clean)
            and not demo_requested
            and booking_intent == "none"
            and contact_intent == "none"
        )

        sales_relevant = bool(
            explicit_facts
            or pain_points
            or interested_capabilities
            or objections
            or demo_requested
            or booking_intent != "none"
            or contact_intent != "none"
            or cost_question
            or privacy_question
            or ordinary_chatbot_objection
            or disengaged
            or declined_fields
            or (brief_affirmation and bool(context))
        )

        return SalesProfileExtraction(
            explicit_facts=explicit_facts,
            pain_points=_unique(pain_points),
            interested_capabilities=interested_capabilities,
            objections=_unique(objections),
            declined_fields=_unique(declined_fields),
            matched_playbooks=_unique(matched_playbooks),
            demo_capability_id=demo_capability_id,
            explicit_demo_request=demo_requested,
            booking_intent=booking_intent,  # type: ignore[arg-type]
            contact_intent=contact_intent,  # type: ignore[arg-type]
            cost_question=cost_question,
            privacy_question=privacy_question,
            ordinary_chatbot_objection=ordinary_chatbot_objection,
            disengaged=disengaged,
            brief_affirmation=brief_affirmation,
            brief_rejection=brief_rejection,
            direct_operational_command=direct_operational_command,
            sales_relevant=sales_relevant,
            evidence=_unique(evidence, maximum=40),
        )


sales_profile_extractor = SalesProfileExtractor()
