"""作者：Wangyuan；日期：2026-10-29；职责：通用中文义务与期限候选解析，结果须人工复核。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

MAX_TEXT = 200_000

_NUMBER = r"(?:\d+|[零〇一二两三四五六七八九十百千万]+)"
_DATE_RE = re.compile(
    rf"(?:"
    rf"(?:(?P<year>{_NUMBER})\s*年\s*)?(?P<month>{_NUMBER})\s*月\s*(?P<day>{_NUMBER})\s*[日号]?"
    rf"|(?P<iso_year>\d{{4}})\s*[-/.]\s*(?P<iso_month>\d{{1,2}})\s*[-/.]\s*(?P<iso_day>\d{{1,2}})"
    rf")"
)
_WORKDAY_RE = re.compile(rf"(?P<count>{_NUMBER})\s*个?\s*工作日")
_CALENDAR_DAY_RE = re.compile(
    rf"(?:限期|期限|在|于)?\s*(?P<count>{_NUMBER})\s*个?\s*(?:自然日|日|天)(?:内|以内)"
)
_IMMEDIATE_RE = re.compile(r"即日起(?:执行|生效|实施|办理|起算)?")
# 相对日期词只说“相对哪天”，没有可信原消息/原函锚点时不能按本次收文日期推算。
# “即日”由 _IMMEDIATE_RE 处理，这里不重复，避免即日起执行变成待确认期限。
_RELATIVE_DAY_RE = re.compile(r"(?P<day>今日|今天|本日|当日|当天|明日|明天|次日|翌日|后天)")
_RELATIVE_DAY_OFFSETS = {
    "今日": 0,
    "今天": 0,
    "本日": 0,
    "当日": 0,
    "当天": 0,
    "明日": 1,
    "明天": 1,
    "次日": 1,
    "翌日": 1,
    "后天": 2,
}
# “按时/按期”是相对期限的说法，缺可信锚点时是待核期限，不能当成没有期限。
_LOOSE_DEADLINE_RE = re.compile(r"按时|按期|准时")
_NEGATION_RE = re.compile(r"无需|无须|不必|不需要|不再|不要|不用|未要求|不予")
_PROHIBITION_RE = re.compile(r"不得|禁止|严禁")
_CLOCK_PERIOD = r"凌晨|早上|上午|中午|下午|傍晚|晚上"
_CLOCK_RE = re.compile(
    rf"\s*(?P<period>{_CLOCK_PERIOD})?\s*(?P<hour>\d{{1,2}})\s*"
    r"(?:[:：]|时|点)\s*(?P<minute>\d{1,2})?\s*分?"
)
_HISTORY_RE = re.compile(
    r"(?:已(?:经)?|此前已|曾经)\s*(?:于[^，,。；;]{1,24})?\s*"
    r"(?:完成|反馈|办理|提交|报送|执行|整改)"
)
# “已提前转/已分别通知/已同步”是转办履历，基线规则原先没有“转/告知/同步”类动词，
# 这里单列：只有履历后不再出现新的日期或工作日期限时才按履历丢弃，避免误删混合句里的
# 新义务。“已同步…反馈”里的“反馈”是履历本身的动作词，不因它另造待办；而“请…同步并
# 反馈”没有“已/曾经”前缀，仍然按当前要求办理。
_HANDOFF_RE = re.compile(
    r"(?:已(?:经)?|此前已|曾经)\s*(?:于[^，,。；;]{1,24})?\s*"
    # 副词会夹在“已”与动词之间（“已提前转”），同样属于履历。
    r"(?:提前|及时|先行|统一|正式|分别|再次|书面|口头|重新|按要求)?\s*"
    r"(?:转(?:达|办|交|发|告|知)?|告知|通知|答复|送达|移交|同步)"
)
# “经电话询问机场，反馈……”“据了解……”是沟通履历与转述，不是新的办理义务。
_COMMUNICATION_RECORD_RE = re.compile(
    r"(?:经|通过|根据)?(?:电话|致电|去电|口头|微信)"
    r"(?:询问|联系|沟通|核实|确认|通知|反馈|说明|了解)"
    r"|据了解|经核实|经了解|据反馈"
)
# “申请/提请”里的“请”是词内字，不是请求语气，避免把标题或“申报截止日期”误判为当前要求。
_CURRENT_REQUEST_RE = re.compile(r"(?<!申)请|应当|须|必须|现要求|仍需|需继续|补充(?:提交|反馈)")
# “应/要”是公文里最常用的义务情态词，但也大量出现在词内：“应急、响应、适应、供应、
# 相应、对应、反应、需要、重要、要求”等。这里按语义位置识别——“应/要”必须不是这些
# 复合词的构词字，才按当前义务语气处理。
# 只在“是否还有当前义务”的判断里与 _CURRENT_REQUEST_RE 并用（申报/报动作门、履历过滤），
# 不放宽标题判定、可选说明与流程描述等既有边界。
_MODAL_OBLIGATION_RE = re.compile(
    r"(?<![响适供相对效反接策内因顺照呼理本])应(?![急])"
    r"|(?<![需重主将只必概摘纪])要(?![求])"
)
_CONDITION_RE = re.compile(
    r"(?:如果|如(?!下|期)|若|一旦)[^，,。；;\n]+"
    # “因……需求/情况”是条件适用前提（如因空管运行提出的需求），不是无条件义务。
    r"|因(?!此)[^，,。；;]{1,40}?(?:需求|情况|原因|情形)"
)
_ATTACHMENT_LABEL_RE = re.compile(
    rf"\s*(?:附件(?:目录|清单)?(?:{_NUMBER})?|参考文件|引用文件|引用原文|参考原文|摘录)\s*[:：]"
)
_NUMBERING_RE = re.compile(r"\s*(?:[一二三四五六七八九十]+|\d+)[、.．]\s*")
_CONTRAST_RE = re.compile(r"(?:但是|不过|但|仍然|仍|同时)\s*")
# 折行合并：句终标点、编号/条款起始行才是真实分句边界，其余换行按续行处理。
# 半角句点只有在不夹在字母数字之间时才按句终处理，避免拆散网址。
_SENTENCE_END_CHARS = "。！？!?；;：:"
_SENTENCE_END_RE = re.compile(r"[。！？!?；;]|(?<![A-Za-z0-9])\.(?![A-Za-z0-9])")
_STRUCTURAL_START_RE = re.compile(
    r"(?:[一二三四五六七八九十]+|\d+)[、.．]"
    r"|[（(]\s*(?:[一二三四五六七八九十]+|\d+|[A-Za-z]|[-—－=]+)\s*[)）]"
)
_HEADING_LINE_MAX = 24
_HEADING_LINE_STOP_CHARS = "，,；;。！？!?："

_ACTION_RE = re.compile(
    r"研究提出意见|填报|填写|注明|协商|遵照执行|办理|处理|落实|执行|实施|整改|核查|核实|确认|完成|"
    r"反馈|回复|报告|报送|提交|配合|会同|检查|核对|使用|启用|遵守|做好|建立|上报|"
    # “报/申报”只有在后面确实跟着报送对象或内容时才是办理动作（“报机场集团”“申报…计划”）；
    # 分句末的“五、资源计划申报”和“通报/预报/虚报”这类构词位置只是名词或结果，不是义务。
    r"申报(?=\s*[\u4e00-\u9fffA-Za-z0-9])"
    r"|(?<![上通汇预警情简快公海党虚多瞒谎误])报(?![告送道纸表章社刊警号销账税值要])"
    r"(?=\s*[\u4e00-\u9fffA-Za-z0-9])"
)
_TASK_ACTIONS = {
    "研究提出意见",
    "填写",
    "注明",
    "协商",
    "遵照执行",
    "办理",
    "处理",
    "落实",
    "执行",
    "实施",
    "整改",
    "核查",
    "核实",
    "确认",
    "完成",
    "配合",
    "会同",
    "检查",
    "核对",
    "使用",
    "启用",
    "遵守",
    "做好",
    "建立",
    "上报",
    "报",
    "申报",
}
_FEEDBACK_ACTIONS = {"反馈", "回复", "报告", "报送", "提交", "填报"}
# “报/申报”是最常用的报送动词，也是“申报方式/报批工作”这类名词短语的构词字：
# 只在同一分句里另有请求语气时才算办理义务，避免章节标题和目的状语变成待办。
_SUBMISSION_ACTIONS = frozenset({"报", "申报"})
# “执行情况/办理流程/填写说明”等名词性复合词里的动词只是描述，不是办理义务，等长判断保留索引。
_DESCRIPTIVE_VERBS = frozenset({"执行", "办理", "处理", "落实", "实施", "填写", "填报"})
_DESCRIPTIVE_ACTION_TAIL_RE = re.compile(r"(?:情况|性|流程|程序|说明|方式|标准|能力)")
# “可在/如需/或……咨询、支持、帮助、下载、查询、联系”是可选项说明，不是必须办理义务。
_OPTIONAL_SUPPORT_RE = re.compile(
    r"(?:可(?:以)?|如需|如有需要|或)\s*[^，。；;\n]{0,30}?(?:咨询|支持|帮助|下载|查询|联系)"
)
# “由……决策/批准后……系统/流程……执行”是审批模式或系统流程说明，不是本单位待办。
_PROCESS_DESCRIPTION_RE = re.compile(
    r"(?:决策|批准|审定|核准|审批|同意)后[^。；;]{0,40}?(?:录入|归档|办理|执行|处理|完成)"
)

_DEPARTMENT_RE = re.compile(
    r"(?P<department>"
    r"[各相关主要牵头协同由请将和与、，,：:（）()A-Za-z0-9_\-\u4e00-\u9fff]{1,32}?"
    r"(?:部门|部|处|室|中心|委员会|分公司|公司|航司|科|单位|机构)"
    r")"
)
# 公文标题块：文号行等前置噪声后接“关于……的通知”，本身不是待办。
_DOC_TITLE_RE = re.compile(r"^\s*[\s\S]{0,120}?关于[\s\S]{1,80}?(?:通知|函|报告|意见)\s*[.。]?\s*$")
# 泛称单位/机构只有处于承办主体位置（后跟顿号或应/要/需等）才算责任主体。
_PARTY_FOLLOW_CHARS = "、，,；;和与及等均都应要需须请"
# 单字部门名词若被后续字组成其他词（建立应急“处”置方案），不作为部门名。
_SINGLE_CHAR_NOUNS = "部处室科"
_NOUN_CONTINUATION_CHARS = "置理分门派队学技内长委务"
# “（单位、姓名、职务、联系电话）”是名单/表格字段说明，其中的“单位/职务”只是字段名，
# 不是单位名称；等长遮罩后仍保留原文索引。
_FIELD_LIST_RE = re.compile(
    r"[（(][^）)]{0,60}?(?:单位|姓名|职务|联系电话|联系方式|手机|身份证)[^）)]{0,60}?[)）]"
)
# “向/提交至 X 申报”“报至 X”里的 X 是收件方（本例是外部机场接收中心），不是本公司
# 承办主体；只遮罩收件方本身，句首的真实主体（“请运行部…报安全部”）仍然保留。
_RECIPIENT_FRAME_RE = re.compile(
    r"(?:向|至|给)\s*[^，,。；;：:\n]{1,30}?(?=(?:申报|报送|报|提交|递交|发送))"
    r"|(?<![上通汇预警情简快公海党虚多瞒谎误])(?:申报|报送|提交|递交|发送|报)(?:至|给)?"
    r"\s*[^，,。；;：:\n]{1,30}?(?=[，,。；;：:\n]|$)"
)
# “应与X协商/取得X的同意”中的X是外部协商对象，不是本公司承办部门，等长遮罩保留索引。
_COUNTERPARTY_FRAME_RE = re.compile(
    r"(?:应|请)?(?:与|同|跟)[^，,。；;、]{1,30}?(?:协商|沟通|协调|交换意见)"
    r"|取得[^，,。；;、]{1,30}?的同意"
)


_CONFIDENTIALITY_OPTIONS = ("公开", "内部", "不公开", "秘密", "机密", "绝密")
_URGENCY_OPTIONS = ("平件", "急件", "紧急", "特急", "加急")
_CONFIDENTIALITY_LABEL_RE = re.compile(
    rf"(?:密级|密?别|保密等级)\s*[:：]\s*(?P<value>{'|'.join(_CONFIDENTIALITY_OPTIONS)})"
)
_URGENCY_LABEL_RE = re.compile(
    # 明传电报表头为“等级（值）”且常无冒号，显式标识优先于默认平件。
    rf"(?:紧急程度|紧急性|缓急|优先级|等级)\s*[:：]?\s*(?P<value>{'|'.join(_URGENCY_OPTIONS)})"
)
_LEADING_METADATA_RE = re.compile(
    rf"^\s*(?:(?:密级|密?别|保密等级)\s*[:：]\s*(?:{'|'.join(_CONFIDENTIALITY_OPTIONS)})|"
    rf"(?:紧急程度|紧急性|缓急|优先级)\s*[:：]\s*(?:{'|'.join(_URGENCY_OPTIONS)})|"
    rf"(?:{'|'.join(_CONFIDENTIALITY_OPTIONS)})\s+(?:{'|'.join(_URGENCY_OPTIONS)}))"
    r"[\s，,；;：:]*"
)


@dataclass(frozen=True)
class _Calendar:
    """内部的显式工作日日历；未覆盖的日期不做默认推算。"""

    calendar_id: str
    valid_from: date
    valid_to: date
    holidays: frozenset[date]
    workdays: frozenset[date]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> _Calendar:
        """把冻结格式的日历字典转为不可变校验对象。"""
        required = ("calendar_id", "valid_from", "valid_to", "holidays", "workdays")
        if any(key not in value for key in required):
            raise ValueError("工作日日历字段不完整")
        calendar_id = value["calendar_id"]
        if not isinstance(calendar_id, str) or not calendar_id.strip():
            raise ValueError("工作日日历标识无效")
        valid_from = _as_date(value["valid_from"], "工作日日历起始日期")
        valid_to = _as_date(value["valid_to"], "工作日日历结束日期")
        if valid_from > valid_to:
            raise ValueError("工作日日历范围无效")
        holidays = _date_set(value["holidays"], "节假日")
        workdays = _date_set(value["workdays"], "调班工作日")
        if holidays & workdays:
            raise ValueError("工作日与节假日配置冲突")
        if any(not valid_from <= item <= valid_to for item in holidays | workdays):
            raise ValueError("工作日日历例外超出覆盖范围")
        return cls(calendar_id.strip(), valid_from, valid_to, holidays, workdays)

    def deadline_after(self, received_on: date, count: int) -> date:
        """按收文次日起算工作日；越出显式日历范围就拒绝猜测。"""
        if count < 1 or count > 366:
            raise ValueError("工作日期限数量超出支持范围")
        if not self.valid_from <= received_on <= self.valid_to:
            raise ValueError("收文日期不在工作日日历范围内")
        remaining = count
        current = received_on
        while remaining:
            current += timedelta(days=1)
            if current > self.valid_to:
                raise ValueError("工作日期限超出日历范围")
            if current in self.workdays or (current.weekday() < 5 and current not in self.holidays):
                remaining -= 1
        return current


def _as_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field}无效") from exc
    raise ValueError(f"{field}无效")


def _date_set(value: Any, field: str) -> frozenset[date]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise TypeError(f"{field}格式无效")
    try:
        return frozenset(_as_date(item, field) for item in value)
    except TypeError as exc:
        raise ValueError(f"{field}格式无效") from exc


def _number(value: str) -> int:
    """解析常见阿拉伯/中文数量，不对不认识的口语做猜测。"""
    compact = re.sub(r"\s+", "", value)
    if compact.isascii() and compact.isdigit():
        return int(compact)
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if all(char in digits for char in compact):
        return int("".join(str(digits[char]) for char in compact))
    total = 0
    current = 0
    for char in compact:
        if char in digits:
            current = digits[char]
        elif char in {"十", "百", "千", "万"}:
            unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            total += (current or 1) * unit
            current = 0
        else:
            raise ValueError("中文数量无法识别")
    return total + current


def _metadata(
    text: str,
) -> tuple[str, str, bool, list[str]]:
    values: list[tuple[str, str, re.Pattern[str], tuple[str, ...], str]] = [
        (
            "confidentiality",
            "密级",
            _CONFIDENTIALITY_LABEL_RE,
            _CONFIDENTIALITY_OPTIONS,
            "内部",
        ),
        ("urgency", "紧急程度", _URGENCY_LABEL_RE, _URGENCY_OPTIONS, "平件"),
    ]
    found: dict[str, list[str]] = {}
    raw_found: dict[str, list[str]] = {}
    header = text[:4000]
    for key, _label, pattern, options, _default in values:
        matches = list(pattern.finditer(header))
        raw_found[key] = [match.group("value") for match in matches]
        found[key] = ["急件" if value == "加急" else value for value in raw_found[key]]
        # 一些公文把标识单独放在前几行；仅接受完全独立的一行，避免正文偶然命中。
        for line_match in re.finditer(r"(?m)^\s*([^\n\r]+?)\s*$", header):
            line = line_match.group(1).strip(" \t：:，,；;")
            paired = re.fullmatch(
                rf"(?P<confidentiality>{'|'.join(_CONFIDENTIALITY_OPTIONS)})\s+"
                rf"(?P<urgency>{'|'.join(_URGENCY_OPTIONS)})",
                line,
            )
            if paired:
                line = paired.group(key)
            if line in options and line not in found[key]:
                raw_found[key].append(line)
                found[key].append("急件" if line == "加急" else line)

    warnings: list[str] = []
    confidentiality = (
        max(found["confidentiality"], key=_CONFIDENTIALITY_OPTIONS.index)
        if found["confidentiality"]
        else "待确认"
    )
    urgency = (
        max(
            ("急件" if value == "加急" else value for value in found["urgency"]),
            key=_URGENCY_OPTIONS.index,
        )
        if found["urgency"]
        else "待确认"
    )
    metadata_needs_confirmation = False
    for key, label, _pattern, _options, _default in values:
        markers = found[key]
        if not markers:
            metadata_needs_confirmation = True
            warnings.append(f"{label}待人工确认")
        elif len(set(markers)) > 1:
            metadata_needs_confirmation = True
            warnings.append(f"{label}显式标识冲突，待人工确认")
        if key == "urgency" and "加急" in raw_found[key]:
            metadata_needs_confirmation = True
            warnings.append("紧急程度“加急”按急件归一，原文待人工确认")
    return confidentiality, urgency, metadata_needs_confirmation, warnings


def _negated_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for match in _NEGATION_RE.finditer(text):
        end = len(text)
        punctuation = re.search(r"[，,。；;\n]", text[match.end() :])
        if punctuation:
            end = match.end() + punctuation.start()
            if text[end] in "，,":
                # 逗号本身不一定解除否定。“无需办理，反馈”语义不完整，
                # 不能把后半句自动当成肯定任务；只有“请/但/但是/不过”
                # 这样的明确转折由分句逻辑解除作用域。
                tail = text[end + 1 :]
                if not re.match(r"\s*(?:请|但|但是|不过)", tail):
                    end = len(text)
        contrast = _CONTRAST_RE.search(text, match.end())
        if contrast and contrast.start() < end:
            end = contrast.start()
        ranges.append((match.start(), end))
    return ranges


def _in_ranges(match: re.Match[str], ranges: list[tuple[int, int]]) -> bool:
    return any(start <= match.start() and match.end() <= end for start, end in ranges)


def _positive_matches(
    pattern: re.Pattern[str], text: str, ranges: list[tuple[int, int]]
) -> list[re.Match[str]]:
    return [match for match in pattern.finditer(text) if not _in_ranges(match, ranges)]


def _descriptive_action(text: str, match: re.Match[str]) -> bool:
    """动作词后接“情况/流程/说明”等名词时按名词性描述处理，不算办理义务。"""
    if match.group(0) not in _DESCRIPTIVE_VERBS:
        return False
    return bool(_DESCRIPTIVE_ACTION_TAIL_RE.match(text[match.end() : match.end() + 2]))


def _action_matches(text: str) -> list[re.Match[str]]:
    """正面办理动作匹配；排除否定范围与“执行情况/填写说明”式名词性描述。"""
    return [
        match
        for match in _positive_matches(_ACTION_RE, text, _negated_ranges(text))
        if not _descriptive_action(text, match)
    ]


def _is_history_record(semantic: str) -> bool:
    """整段都是已办/已转/电话核实的履历记录时，不新建待办。

    原有履历动词保持基线语义；新增的转办/告知与电话核实类动词只在履历之后不再出现
    新的日期、工作日或“即日起”时才丢弃，混合句里的新义务仍留给人工核对。
    “已转业务甲，请X于某日前反馈”这类混合句的新要求已由分句逻辑单独成段；
    “已转业务甲，运行部应核实并反馈”这类没有“请”但带“应”的混合句必须保留候选。
    """
    if _HISTORY_RE.search(semantic):
        return not _has_current_obligation(semantic)
    match = _HANDOFF_RE.search(semantic) or _COMMUNICATION_RECORD_RE.search(semantic)
    if match is None:
        return False
    if _has_current_obligation(semantic):
        return False
    tail = semantic[match.end() :]
    return not (_DATE_RE.search(tail) or _WORKDAY_RE.search(tail) or _IMMEDIATE_RE.search(tail))


def _has_current_obligation(semantic: str) -> bool:
    """分句里是否还有当前义务语气：请求语或“应/要”类情态。"""
    return bool(_CURRENT_REQUEST_RE.search(semantic) or _MODAL_OBLIGATION_RE.search(semantic))


def _coordination_background(source: str) -> bool:
    """只有配合/协助动作、没有期限与请求语气的说明片段，不是独立待办。"""
    semantic = _semantic_text(source)
    if _CURRENT_REQUEST_RE.search(semantic) or _CONDITION_RE.search(semantic):
        return False
    if _DATE_RE.search(semantic) or _WORKDAY_RE.search(semantic) or _IMMEDIATE_RE.search(semantic):
        return False
    actions = {match.group(0) for match in _action_matches(semantic)}
    return bool(actions) and actions <= {"配合", "协助"}


def _party_value(raw: str) -> str:
    """把部门候选原文清成责任主体名，含引导词、连接词与前置说明的剥离。

    部门前可带“请/由”，也可能夹在“于 2026 年 9 月 30 日前由”之后；先取最后一个引导词或
    分隔符，避免把日期、连接词和前置说明当成部门名称。收件方同名遮罩复用同一口径。
    """
    value = raw.strip(" ，,：:；;。")
    value = re.sub(r"^.*(?:请|由|将|向|报|报送|申报)\s*", "", value)
    value = re.sub(r"^.*[，,、；;：:]\s*", "", value)
    value = re.sub(r"^应\s*(?:与|和|同|跟)\s*", "", value)
    value = re.sub(r"^(?:和|与|及|、)+", "", value).strip()
    return re.sub(r"^[^\u4e00-\u9fffA-Za-z]*(?=[\u4e00-\u9fffA-Za-z])", "", value)


def _department_candidates(text: str) -> list[str]:
    # 浏览器/剪贴板可能把标题、密级和正文合成一行。仅在它们位于开头时
    # 去掉标题与明确元数据，避免“真实公司”这类标题尾词被当成责任部门；
    # 中间正文一律保留，避免吞掉多事项或正文证据。
    scan_text = text
    leading = len(scan_text) - len(scan_text.lstrip())
    scan_text = scan_text[leading:]
    # OCR/排版会把“航班时刻协\n调工作机构”这类词内换行断开，先按续行合并再取候选。
    scan_text = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]*\n[ \t]*(?=[\u4e00-\u9fff])", "", scan_text)
    # 名单/表格字段括注先遮罩，避免“参会名单（单位、姓名…）”里的字段名被当成部门。
    scan_text = _FIELD_LIST_RE.sub(lambda match: " " * len(match.group()), scan_text)
    # 申报/报送的收件方先遮罩，避免“向示例机构接收中心申报”的外部对象成为承办部门；
    # 同一外部收件方常在“以便 X 及时掌握”里再次出现，按同名一并遮罩。
    recipient_names: set[str] = set()
    for frame in _RECIPIENT_FRAME_RE.finditer(scan_text):
        recipient_names.update(
            _party_value(match.group("department"))
            for match in _DEPARTMENT_RE.finditer(frame.group())
        )
    for name in sorted(filter(None, recipient_names), key=len, reverse=True):
        scan_text = scan_text.replace(name, " " * len(name))
    scan_text = _RECIPIENT_FRAME_RE.sub(lambda match: " " * len(match.group()), scan_text)
    if scan_text.startswith(("【", "《")):
        closing = "】" if scan_text[0] == "【" else "》"
        end = scan_text.find(closing, 1)
        if end >= 0:
            scan_text = scan_text[end + 1 :]
    while True:
        match = _LEADING_METADATA_RE.match(scan_text)
        if not match:
            break
        scan_text = scan_text[match.end() :]
    # 协商对象不是承办部门：等长遮罩后仍保持原有索引位置。
    scan_text = _COUNTERPARTY_FRAME_RE.sub(lambda match: " " * len(match.group()), scan_text)
    candidates: list[str] = []
    for match in _DEPARTMENT_RE.finditer(scan_text):
        raw = match.group("department")
        value = _party_value(raw)
        if _ACTION_RE.match(value):
            # 责任主体是名词短语：“反馈未提到的航司”“报请审批”是被报告或描述的动作，
            # 不能拿动作短语当承办部门。
            continue
        if value.count("（") + value.count("(") != value.count("）") + value.count(")"):
            # 半截括注（“参会名单（单位”）是排版截断的字段说明，不是部门名。
            continue
        if any(char.isdigit() for char in value) or any(char in value for char in "年月日"):
            continue
        if re.fullmatch(
            r"(?:因|由于|如|若|为|对|针对|关于|在|于)?(?:本|我|该|其|贵|各)?(?:公司|单位|部门|机构)",
            value,
        ):
            # “因公司/该公司/各单位”是代指主体或条件连接词，不是具体的责任部门名。
            continue
        if re.match(r"(?:根据|按照|依据|参照|针对)", value):
            # “根据相关审批部门要求”是要求依据，不是承办主体。
            continue
        if re.match(
            r"(?:决定|决策|批准|审定|核准|审批|同意)", scan_text[match.end() :].lstrip(" \t")
        ):
            # “由X决策/批准”说明X是决策或审批方，不是承办主体。
            continue
        if raw[-1] in _SINGLE_CHAR_NOUNS:
            following = scan_text[match.end() : match.end() + 1]
            if following and following in _NOUN_CONTINUATION_CHARS:
                continue
        if value.endswith(("单位", "机构")):
            following = scan_text[match.end() :].lstrip(" \t")
            if following[:1] and following[0] not in _PARTY_FOLLOW_CHARS:
                # 泛称单位/机构还可能是协商对象或名称引用，只在承办主体位置采用。
                continue
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _department(text: str) -> tuple[str, list[str]]:
    candidates = _department_candidates(text)
    warnings: list[str] = []
    joint = bool(re.search(r"共同(?:办理|负责|反馈|完成|执行)?|联合办理|协同办理", text))
    role_split = bool(re.search(r"(?:负责|牵头|承办|主办|配合)", text))
    if not candidates:
        return "", ["责任部门待确认"]
    if joint and len(candidates) > 1:
        value = "、".join(candidates)
        warnings.append("责任部门含共同办理/共同负责表述，待人工确认")
    else:
        value = candidates[0]
        if len(candidates) > 1:
            warnings.append("责任部门存在多个部门，待人工确认")
    if role_split:
        warnings.append("责任部门含负责/牵头/配合分工表述，待人工确认")
    if re.match(r"^各", value) or value.endswith(("单位", "机构")):
        warnings.append(f"原文责任主体为“{value}”，内部承办部门待确认")
    return value, warnings


def _line_break_is_boundary(text: str, index: int) -> bool:
    """判断换行是否为真实语义边界；排版/OCR折行在句中时按续行合并。

    句终标点、以“：”结尾的标签行、编号/条款起始行都按真实边界处理；
    “申报截止日期”这类标题与其下一行内容必须保持同段，否则期限会丢失。
    """
    head = text[:index].rstrip(" \t\r\n")
    if not head or head[-1] in _SENTENCE_END_CHARS:
        return True
    if head[-1] == "." and not (len(head) > 1 and head[-2].isalnum()):
        return True
    tail = text[index + 1 :].lstrip(" \t\r")
    if not tail:
        return False
    line_end = tail.find("\n")
    tail_line = tail if line_end < 0 else tail[:line_end]
    if tail_line.rstrip(" \t").endswith(("：", ":")):
        return True
    if re.match(r"[\u4e00-\u9fff]{1,10}[：:]", tail_line.lstrip()):
        # “签发日期：…”“运行部：…”等短标签行独立成段。
        return True
    head_line = head[max(head.rfind("\n"), -1) + 1 :].lstrip(" \t")
    if (
        len(head_line) <= _HEADING_LINE_MAX
        and not any(char in head_line for char in _HEADING_LINE_STOP_CHARS)
        and _STRUCTURAL_START_RE.match(head_line)
        and not _DEADLINE_LABEL_TAIL_RE.search(head_line)
    ):
        # 短标题行（“二、其他事项”“四、系统运行保障”）独立成段；
        # 编号开头的长句（含逗号的正文行）仍与后续折行合并。
        return True
    return bool(_STRUCTURAL_START_RE.match(tail))


def _split_spans(text: str) -> list[tuple[int, int]]:
    """按原文标点切分候选，保留每段在原文中的连续证据。"""
    spans: list[tuple[int, int]] = []
    semantic_text = _semantic_text(text)
    sentence_start = len(text) - len(text.lstrip())
    if text[sentence_start:].startswith(("【", "《")):
        closing = "】" if text[sentence_start] == "【" else "》"
        title_end = text.find(closing, sentence_start + 1)
        if title_end >= 0:
            sentence_start = title_end + 1
    while True:
        match = _LEADING_METADATA_RE.match(text[sentence_start:])
        if not match:
            break
        sentence_start += match.end()
    hard_end_positions = [match.end() for match in _SENTENCE_END_RE.finditer(semantic_text)]
    hard_end_positions.extend(
        index + 1
        for index, char in enumerate(semantic_text)
        if char == "\n" and _line_break_is_boundary(semantic_text, index)
    )
    hard_end_positions = sorted(set(hard_end_positions))
    for sentence_end in hard_end_positions + (
        [len(text)] if not hard_end_positions or hard_end_positions[-1] != len(text) else []
    ):
        if sentence_end <= sentence_start:
            continue
        segment_end = sentence_end
        cursor = sentence_start
        comma_matches = list(re.finditer(r"[，,]", semantic_text[sentence_start:segment_end]))
        for comma_match in comma_matches:
            comma = sentence_start + comma_match.start()
            tail_start = comma + 1
            while tail_start < segment_end and text[tail_start].isspace():
                tail_start += 1
            connector = re.match(r"(?:但是|不过|但|仍然|仍)\s*", text[tail_start:segment_end])
            if connector:
                candidate_start = tail_start + connector.end()
            else:
                candidate_start = tail_start
            tail = semantic_text[candidate_start:segment_end]
            head = semantic_text[cursor:comma]
            head_positive = _action_matches(head)
            split_for_new_request = bool(
                re.match(r"请", tail)
                or (
                    _department_candidates(tail)
                    and _action_matches(tail)
                    and (head_positive or _WORKDAY_RE.search(head))
                )
            )
            # 条件前半句与第一项要求不可分离，否则“如发生”会在候选中丢失。
            if (
                _CONDITION_RE.match(_NUMBERING_RE.sub("", head, count=1).strip())
                and not head_positive
            ):
                split_for_new_request = False
            # 同一责任部门可在后续分句省略；只有新的日期和独立动作都出现，
            # 才把“并于12日前反馈”切为另一项，不能拆开同一动作的冲突期限。
            split_for_new_request |= bool(
                head_positive
                and re.match(r"(?:并|且)?\s*(?:于|在|截至|\d{4}年)", tail)
                and _DATE_RE.search(tail)
                and _action_matches(tail)
            )
            if split_for_new_request:
                if text[cursor:comma].strip():
                    spans.append((cursor, comma))
                cursor = candidate_start
        if text[cursor:segment_end].strip():
            # 无逗号的双动作双期限仍各自保留原文片段。
            tail_cursor = cursor
            for conjunction in re.finditer(
                r"(?:并|且)(?=\s*(?:于|在|截至))", text[cursor:segment_end]
            ):
                split_at = cursor + conjunction.start()
                after = cursor + conjunction.end()
                if (
                    _action_matches(text[tail_cursor:split_at])
                    and _DATE_RE.search(text[after:segment_end])
                    and _action_matches(text[after:segment_end])
                ):
                    spans.append((tail_cursor, split_at))
                    tail_cursor = after
            spans.append((tail_cursor, segment_end))
        sentence_start = sentence_end
    return spans


_DEADLINE_LABEL_TAIL_RE = re.compile(
    # “申报截止日期/反馈截止时间”等标题后另起一行的日期，仍属该期限。
    r"(?:截至|截止(?:于|到)?(?:日期|时间|期限|时限)?)\s*(?:为|是)?\s*[：:，,、]?\s*$"
)
_DEADLINE_SUFFIX_RE = re.compile(
    rf"\s*(?:(?:{_CLOCK_PERIOD})?\s*\d{{1,2}}\s*(?:[:：]|时|点)\s*\d{{0,2}}\s*分?\s*)?"
    r"(?:前|以前|之前|内|以内|完成|办理|反馈|回复|报送|提交)"
)


def _clock_value(period: str, hour: int) -> int:
    """把中文时段并入24小时制；未给时段或无法判断时保持原值。"""
    if period in {"下午", "傍晚", "晚上"} and hour < 12:
        return hour + 12
    if period == "凌晨" and hour == 12:
        return 0
    return hour


def _deadline_clock(semantic: str, index: int) -> tuple[str | None, bool]:
    """解析紧邻期限日期之后的显式时刻，返回（时分文本，是否需要人工复核）。"""
    clock = _CLOCK_RE.match(semantic, index)
    if not clock:
        return None, False
    hour = _clock_value(clock.group("period") or "", int(clock.group("hour")))
    minute = int(clock.group("minute") or 0)
    incomplete = clock.group("minute") is None and bool(re.search(r"[:：]", clock.group()))
    if hour > 23 or minute > 59 or incomplete:
        return None, True
    return f"{hour:02d}:{minute:02d}", False


def _has_deadline_limit(text: str, match: re.Match[str]) -> bool:
    suffix = text[match.end() : match.end() + 24]
    prefix = text[max(0, match.start() - 20) : match.start()]
    if _DEADLINE_SUFFIX_RE.match(suffix):
        return True
    return bool(_DEADLINE_LABEL_TAIL_RE.search(prefix))


def _parse_date(match: re.Match[str], received_on: date) -> tuple[date | None, bool, str | None]:
    try:
        if match.group("iso_year"):
            year = int(match.group("iso_year"))
            month = int(match.group("iso_month"))
            day = int(match.group("iso_day"))
            year_missing = False
        else:
            year_missing = match.group("year") is None
            year = _number(match.group("year")) if match.group("year") else received_on.year
            month = _number(match.group("month"))
            day = _number(match.group("day"))
        return date(year, month, day), year_missing, None
    except (TypeError, ValueError, OverflowError):
        return None, False, "正文日期无效，待人工确认"


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _semantic_text(source: str) -> str:
    """文名中的动词不是办理义务；等长遮罩保留原文索引。"""
    return re.sub(r"《[^》]*》|“[^”]*”", lambda match: " " * len(match.group()), source)


def _event_condition(source: str) -> str:
    """只识别明示事件，不擅自用收文日期代替事件发生时间。"""
    match = re.search(
        r"(?:于|在)([^，,。；;\n]{0,30}?(?:会后|会议结束后|事件结束后|处理结束后))", source
    )
    if match:
        return match.group(1)
    match = re.search(r"会后|会议结束后|事件结束后|处理结束后", source)
    return match.group() if match else ""


def _system_agent(text: str, match: re.Match[str]) -> bool:
    """动作紧跟在“系统/自动”之后时是系统流程说明，不是本单位的办理义务。"""
    window = re.sub(r"\s+", "", text[max(0, match.start() - 10) : match.start()])
    return bool(re.search(r"(?:系统|自动)$", window))


def _task_for_span(
    source: str,
    *,
    received_on: date,
    issued_on: date | None,
    calendar: _Calendar | None,
    inherited_department: str = "",
    condition: str = "",
    source_quote: str | None = None,
) -> dict[str, Any] | None:
    semantic = _semantic_text(source)
    if _ATTACHMENT_LABEL_RE.match(semantic):
        return None
    if _DOC_TITLE_RE.match(semantic) and not _CURRENT_REQUEST_RE.search(semantic):
        return None
    if _is_history_record(semantic):
        return None
    negated = _negated_ranges(semantic)
    prohibition = bool(_positive_matches(_PROHIBITION_RE, semantic, negated))
    action_matches = [
        match for match in _action_matches(semantic) if not _system_agent(semantic, match)
    ]
    if not _has_current_obligation(semantic):
        action_matches = [
            match for match in action_matches if match.group(0) not in _SUBMISSION_ACTIONS
        ]
    date_matches = _positive_matches(_DATE_RE, semantic, negated)
    workday_matches = _positive_matches(_WORKDAY_RE, semantic, negated)
    calendar_day_matches = _positive_matches(_CALENDAR_DAY_RE, semantic, negated)
    immediate_matches = _positive_matches(_IMMEDIATE_RE, semantic, negated)
    relative_day_matches = _positive_matches(_RELATIVE_DAY_RE, semantic, negated)
    loose_deadline_matches = _positive_matches(_LOOSE_DEADLINE_RE, semantic, negated)
    deadline_dates = [match for match in date_matches if _has_deadline_limit(semantic, match)]
    effective_dates = [
        match
        for match in date_matches
        if re.match(r"\s*起(?:执行|生效|实施|使用|启用)", semantic[match.end() :])
    ]
    has_positive_time = bool(
        deadline_dates or workday_matches or calendar_day_matches or immediate_matches
    )
    has_positive_action = bool(action_matches) or prohibition
    # 可选帮助/咨询/下载与“由……决策后……系统……执行”的流程说明是背景信息；
    # 一旦带请求语气或明确期限，仍按真实义务继续提取。
    if (
        not _CURRENT_REQUEST_RE.search(semantic)
        and not has_positive_time
        and not prohibition
        and (_OPTIONAL_SUPPORT_RE.search(semantic) or _PROCESS_DESCRIPTION_RE.search(semantic))
    ):
        return None
    # “请于某日前无需反馈”中的日期只服务于被否定动作，不能单独生成伪任务。
    if has_positive_time and not has_positive_action and _NEGATION_RE.search(source):
        return None
    if not has_positive_time and not has_positive_action:
        return None

    task_warnings: list[str] = []
    if _NEGATION_RE.search(source):
        task_warnings.append("存在否定/条件语句，需人工确认是否遗漏待办")
    department, department_warnings = _department(semantic)
    if not department and inherited_department:
        department, department_warnings = inherited_department, []
    for warning in department_warnings:
        _append_unique(task_warnings, warning)

    categories: list[str] = []
    if has_positive_time:
        categories.append("时间")
    if prohibition or any(match.group(0) in _TASK_ACTIONS for match in action_matches):
        categories.append("任务")
    if any(match.group(0) in _FEEDBACK_ACTIONS for match in action_matches):
        categories.append("反馈")

    deadline: date | None = None
    deadline_time: str | None = None
    deadline_type = "none"
    effective_on: date | None = None
    deadline_conditions = len(deadline_dates) + len(workday_matches) + len(calendar_day_matches)
    event_condition = _event_condition(semantic)
    if event_condition:
        condition = condition or event_condition
        deadline_type = "event"
        task_warnings.append("事件时间待确认，不推算截止日期")
    if deadline_conditions > 1:
        task_warnings.append("多个时间条件待人工确认")
        deadline_type = "uncertain"
    elif deadline_dates:
        deadline_type = "fixed_date"
        found = deadline_dates[0]
        parsed, year_missing, parse_warning = _parse_date(found, received_on)
        if parse_warning:
            task_warnings.append(parse_warning)
            deadline_type = "uncertain"
        elif parsed:
            deadline = parsed
            if year_missing:
                task_warnings.append("年份待确认")
            if deadline < received_on:
                task_warnings.append("截止日期早于收文日期，待确认")
            deadline_time, clock_invalid = _deadline_clock(semantic, found.end())
            if clock_invalid:
                task_warnings.append("正文时分无效，待人工确认")
                deadline_type = "uncertain"
    elif workday_matches and not event_condition:
        deadline_type = "workdays"
        try:
            count = _number(workday_matches[0].group("count"))
        except ValueError:
            count = 0
        if calendar is None:
            task_warnings.append("工作日日历待配置")
        elif count:
            try:
                deadline = calendar.deadline_after(received_on, count)
                task_warnings.append("工作日起算口径待确认")
            except ValueError:
                task_warnings.append("工作日期限超出日历范围，待人工确认")
        else:
            task_warnings.append("工作日期限数量无效，待人工确认")
    elif calendar_day_matches:
        deadline_type = "uncertain" if not event_condition else "event"
        task_warnings.append("期限单位不明确，待人工确认")
    elif relative_day_matches:
        # “今天/明天”只说相对哪天：没有可信原消息时刻或原函签发日期时不能拿本次
        # 收文日期推算（历史聊天不能当今天处理），保留时期并标待核。
        relative_day = relative_day_matches[0]
        deadline_time, clock_invalid = _deadline_clock(semantic, relative_day.end())
        if issued_on is not None:
            deadline = issued_on + timedelta(days=_RELATIVE_DAY_OFFSETS[relative_day.group("day")])
            deadline_type = "fixed_date"
            task_warnings.append("相对期限按原函签发日期推算，待人工确认")
            if deadline < received_on:
                task_warnings.append("截止日期早于收文日期，待确认")
        else:
            deadline_type = "uncertain"
            task_warnings.append("原消息时间/相对期限待核，不能按本次收文日期推算")
        if clock_invalid:
            deadline_time = None
            deadline_type = "uncertain"
            task_warnings.append("正文时分无效，待人工确认")
    elif loose_deadline_matches:
        # “按时/按期”只是相对期限的说法，缺可信锚点时是待核期限而不是没有期限。
        deadline_type = "uncertain"
        task_warnings.append("相对期限（按时/按期）待核，原消息时间未知")

    if immediate_matches:
        effective_on = issued_on
        if issued_on is None:
            task_warnings.append("即日起生效日期待确认")

    if len(effective_dates) > 1:
        task_warnings.append("多个生效日期待人工确认")
    elif effective_dates:
        parsed_effective, year_missing, parse_warning = _parse_date(effective_dates[0], received_on)
        effective_on = parsed_effective
        if parse_warning:
            _append_unique(task_warnings, parse_warning)
        if year_missing:
            _append_unique(task_warnings, "年份待确认")

    if not deadline_dates and [match for match in date_matches if match not in effective_dates]:
        # 参考日期仍保留在证据中，但不能静默变成截止日期。
        task_warnings.append("日期用途待确认，不能直接视为截止时间")

    task_warnings = list(dict.fromkeys(task_warnings))
    if condition:
        status = "待触发"
    elif task_warnings:
        status = "待确认"
    elif (prohibition or immediate_matches or effective_dates) and not deadline_conditions:
        status = "持续执行"
    else:
        status = "待办"
    source_quote = (source_quote or source).strip()
    return {
        "description": source.strip(),
        "department": department,
        "deadline": deadline.isoformat() if deadline else None,
        "effective_on": effective_on.isoformat() if effective_on else None,
        "status": status,
        "source_quote": source_quote,
        "warnings": task_warnings,
        "categories": categories,
        "condition": condition,
        "condition_met": not bool(condition),
        "deadline_type": deadline_type,
        "deadline_time": deadline_time,
    }


def analyze(
    text: str,
    *,
    received_on: date,
    issued_on: date | None = None,
    calendar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """分析收文正文并返回严格冻结格式的人工复核候选。

    参数：
        text: UTF-8 解码后的正文，最长 20 万字符。
        received_on: 收文日期；工作日期限从次日起算。
        issued_on: 正文签发日期，用于“即日起”生效日期候选。
        calendar: 显式工作日日历字典；缺失时不推算工作日截止日。

    返回：
        仅包含 `confidentiality`、`urgency`、`metadata_needs_confirmation`、
        `classifications`、`warnings`、`tasks` 六个字段；无法确认的值留空并保留原文。
    """
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError("正文格式或长度无效")
    if not isinstance(received_on, date) or hasattr(received_on, "hour"):
        raise ValueError("收文日期无效")
    if issued_on is not None and (not isinstance(issued_on, date) or hasattr(issued_on, "hour")):
        raise ValueError("签发日期无效")
    parsed_calendar = _Calendar.from_mapping(calendar) if calendar is not None else None

    confidentiality, urgency, metadata_needs_confirmation, warnings = _metadata(text)
    tasks: list[dict[str, Any]] = []
    heading_department = ""
    heading_start: int | None = None
    attachment_directory = False
    previous_context: int | None = None
    previous_department = ""
    previous_start: int | None = None
    section_tasks_start = 0
    section_heading = ""
    section_heading_start: int | None = None
    pending_background_start: int | None = None
    for start, end in _split_spans(text):
        source = text[start:end]
        compact = source.strip()
        background_start = pending_background_start
        pending_background_start = None
        # 独立部门标题只在当前章节继承；新的通用标题、部门标题和附件目录
        # 都会终止旧作用域，避免把别的章节错误归到前一个部门。
        department_heading = re.fullmatch(
            r"(?:[一二三四五六七八九十\d]+[、.．]\s*)?([^\n：:]{1,32}(?:部门|部|处|室|中心|委员会|科))\s*[:：]",
            compact,
        )
        if department_heading:
            heading_department = department_heading.group(1)
            heading_start = start
            attachment_directory = False
            previous_context = None
            section_tasks_start = len(tasks)
            continue
        semantic_compact = _semantic_text(compact)
        unnumbered = _NUMBERING_RE.sub("", semantic_compact, count=1).strip()
        if _ATTACHMENT_LABEL_RE.match(compact):
            # 附件目录优先于标题分支，否则“附件目录：”会被当成冒号标题吞掉。
            heading_department, heading_start = "", None
            section_heading, section_heading_start = "", None
            previous_context = None
            section_tasks_start = len(tasks)
            attachment_directory = True
            continue
        if (
            (_NUMBERING_RE.match(compact) or compact.endswith(("：", ":")))
            and not _CURRENT_REQUEST_RE.search(semantic_compact)
            and not _CONDITION_RE.match(unnumbered)
            and (not _ACTION_RE.search(semantic_compact) or compact.endswith(("：", ":")))
        ):
            # 编号/冒号标题只用于重置章节作用域（含“现将有关事项通知如下:”
            # 未从句式），标题行本身不是待办候选。
            heading = re.fullmatch(
                r"(?:[一二三四五六七八九十]+|\d+)[、.．]\s*([^\n：:]{1,30})", compact
            )
            if heading:
                # “一、换季跨区班机航线”等编号章节标题用于给期限类事项标注范围。
                section_heading, section_heading_start = compact, start
            else:
                section_heading, section_heading_start = "", None
            if not attachment_directory:
                heading_department, heading_start = "", None
            previous_context = None
            section_tasks_start = len(tasks)
            continue
        if attachment_directory:
            if not _CURRENT_REQUEST_RE.search(_semantic_text(source)):
                continue
            attachment_directory = False
        context_start = max(
            text.rfind(mark, 0, start) + 1 for mark in ("。", "！", "？", "!", "?", "；", ";", "\n")
        )
        context = text[context_start:end]
        condition_match = _CONDITION_RE.match(_NUMBERING_RE.sub("", context, count=1).lstrip())
        condition = condition_match.group().strip() if condition_match else ""
        explicit_department = _department_candidates(_semantic_text(source))
        inherited_department = ""
        quote_start = start
        if not explicit_department:
            if previous_context == context_start and previous_department:
                inherited_department = previous_department
                quote_start = previous_start if previous_start is not None else start
            elif heading_department:
                inherited_department = heading_department
                quote_start = heading_start if heading_start is not None else start
        if condition:
            quote_start = min(quote_start, context_start)
        if background_start is not None and background_start < start:
            # 前一句“涉及……协同配合”只是背景说明，作为后一真实要求的原文依据保留。
            quote_start = min(quote_start, background_start)
        # 纯协助条款没有独立交付物或期限时，保留在主事项证据中；若有自己的
        # 反馈/提交义务或时间要求，则继续独立提取。固定合同不新增协办字段。
        if (
            len(tasks) > section_tasks_start
            and re.search(r"协助|配合", semantic_compact)
            and _department_candidates(semantic_compact)
            and not any(
                match.group() in _FEEDBACK_ACTIONS
                for match in _ACTION_RE.finditer(semantic_compact)
            )
            and not _DATE_RE.search(semantic_compact)
            and not _WORKDAY_RE.search(semantic_compact)
        ):
            primary = next(
                (
                    item
                    for item in reversed(tasks[section_tasks_start:])
                    if "任务" in item["categories"] and "反馈" not in item["categories"]
                ),
                tasks[-1],
            )
            primary_start = text.rfind(primary["source_quote"], 0, start)
            if primary_start >= 0:
                primary["source_quote"] = text[primary_start:end].strip()
                _append_unique(primary["warnings"], "含协办条款，主协办关系待人工确认")
                if primary["status"] != "待触发":
                    primary["status"] = "待确认"
                continue
        if _coordination_background(source) and re.match(
            r"\s*[，,]\s*(?:请|应|应当|须|必须|需)", text[end : end + 8]
        ):
            # “换季工作涉及军民航多单位间的协同配合，请各航司……”：前半只是说明，
            # 不单独成待办，原文并入紧随其后的真实要求证据。
            pending_background_start = start
            continue
        task = _task_for_span(
            source,
            received_on=received_on,
            issued_on=issued_on,
            calendar=parsed_calendar,
            inherited_department=inherited_department,
            condition=condition,
            source_quote=text[quote_start:end],
        )
        if task is not None:
            if section_heading and section_heading_start is not None and task["deadline"]:
                # 期限类事项必须带出所在章节，卡片与CSV才能独立看出适用范围。
                task["description"] = text[section_heading_start:end].strip()
                task["source_quote"] = text[min(quote_start, section_heading_start) : end].strip()
                _append_unique(
                    task["warnings"],
                    f"截止日期仅适用于“{section_heading}”一节，其他章节不继承，待人工确认",
                )
            # 转折切分后，肯定分句本身可能不再含否定词；同一原句仍需保留
            # 人工复核提示，避免把“无需 A，但请 B”看成完全无歧义。
            context_end_candidates = [
                position
                for mark in ("。", "！", "？", "!", "?", "\n")
                for position in [text.find(mark, start)]
                if position >= 0
            ]
            context_end = min(context_end_candidates, default=len(text))
            if _NEGATION_RE.search(text[context_start:context_end]):
                warning = "存在否定/条件语句，需人工确认是否遗漏待办"
                _append_unique(task["warnings"], warning)
                if task["status"] != "待触发":
                    task["status"] = "待确认"
            tasks.append(task)
            previous_context = context_start
            previous_department = task["department"]
            previous_start = quote_start
        elif _NEGATION_RE.search(source):
            _append_unique(warnings, "存在否定/条件语句，需人工确认是否遗漏待办")

    classifications: list[str] = []
    for task in tasks:
        for category in task["categories"]:
            _append_unique(classifications, category)
    if not tasks:
        _append_unique(warnings, "未识别到待办，请人工核对是否遗漏")
    return {
        "confidentiality": confidentiality,
        "urgency": urgency,
        "metadata_needs_confirmation": metadata_needs_confirmation,
        "classifications": classifications,
        "warnings": list(dict.fromkeys(warnings)),
        "tasks": tasks,
    }
