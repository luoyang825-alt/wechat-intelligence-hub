from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

REQUEST = re.compile(r"麻烦|请你|请帮|需要你|帮我|帮忙|确认一下|回复一下|处理一下|跟进一下|看一下|审批|签字|发我|给我|能否|可以.{0,8}吗|方便.{0,8}吗", re.I)
CHASE = re.compile(r"再确认|再问|催一下|怎么样了|有结果吗|进展如何|还没|什么时候能|昨天.{0,12}(?:说|提|答应)|前天.{0,12}(?:说|提|答应)", re.I)
MENTION = re.compile(r"@\s*我|@\s*me\b", re.I)
DEADLINE = re.compile(r"今天|明天|后天|今晚|上午|下午|中午|下班前|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}[点时:]|deadline|截止|之前|前给|前发", re.I)
PROMISE = re.compile(r"我(?:会|来|可以|今晚|今天|明天|之后|稍后|回头|这周|本周).{0,40}(?:整理|确认|回复|发|给|做|改|补|推进|安排|提交|发布|完成|跟进|回传|处理)", re.I)
MEETING = re.compile(r"开会|会议|碰一下|沟通会|评审会|复盘会|腾讯会议|飞书会议|Teams|Zoom", re.I)
PAYMENT = re.compile(r"付款|打款|收款|结算|到账|发票|开票|invoice|payment", re.I)
RISK = re.compile(r"停机|宕机|中断|不可用|无法登录|数据丢失|泄露|报警|故障|事故|紧急|立即处理|马上处理", re.I)
QUESTION = re.compile(r"[?？]|请问|什么时候|能否|可以吗|方便吗", re.I)
CLOSE = re.compile(r"^(好的?|收到|明白|了解|嗯+|行|可以|谢谢|辛苦|哈哈+|hhh+|ok)[。！!～~ ]*$", re.I)

CATEGORY_LABELS = {
    "my_promise": "我的承诺",
    "direct_request": "待我处理",
    "chase": "对方催办",
    "deadline": "截止事项",
    "mention": "群聊@我",
    "meeting": "会议事项",
    "payment": "付款结算",
    "risk": "紧急风险",
    "follow_up_due": "跟进到期",
    "general": "重要信息",
}


@dataclass
class Decision:
    important: bool
    score: int
    category: str
    title: str
    summary: str
    action: str
    deadline: str
    confidence: float
    reasons: list[str]
    hard_signal: bool = False


def _text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or "").strip()


def _sender(row: dict[str, Any]) -> str:
    return str(row.get("sender") or "").strip()


def _from_me(row: dict[str, Any]) -> bool:
    value = row.get("from_me")
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _deadline_hint(text: str) -> str:
    for pattern in [
        r"(?:今天|明天|后天|今晚|下班前|本周|下周|周[一二三四五六日天]|星期[一二三四五六日天])[^，。；;！？!?]{0,24}",
        r"\d{1,2}[月/-]\d{1,2}[日号]?(?:\s*\d{1,2}[:点时]\d{0,2})?",
        r"\d{1,2}[:点时]\d{0,2}(?:分)?(?:前|之前)?",
    ]:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    return ""


def evaluate_message(message: dict[str, Any], context: list[dict[str, Any]] | None = None, *, priority_chat: bool = False) -> Decision:
    text = _text(message)
    sender = _sender(message)
    from_me = _from_me(message)
    if not text or CLOSE.fullmatch(text):
        return Decision(False, 0, "general", "", text[:140], "", "", 0.2, ["low_information"])

    score = 0
    reasons: list[str] = []
    category = "general"
    hard = False

    if priority_chat:
        score += 10
        reasons.append("priority_chat")
    if from_me and PROMISE.search(text):
        score += 72
        category = "my_promise"
        hard = True
        reasons.append("explicit_self_promise")
        if DEADLINE.search(text):
            score += 14
            reasons.append("deadline_in_promise")
    elif not from_me and REQUEST.search(text):
        score += 72
        category = "direct_request"
        hard = True
        reasons.append("direct_request")
    if not from_me and CHASE.search(text):
        score += 72
        category = "chase"
        hard = True
        reasons.append("chase")
    if not from_me and MENTION.search(text):
        score += 70
        category = "mention"
        hard = True
        reasons.append("mention_me")
    if DEADLINE.search(text):
        score += 24
        if category == "general":
            category = "deadline"
        reasons.append("deadline")
    if MEETING.search(text):
        score += 48
        if category == "general":
            category = "meeting"
        reasons.append("meeting")
    if PAYMENT.search(text):
        score += 45
        if category == "general":
            category = "payment"
        reasons.append("payment")
    if RISK.search(text):
        score += 88
        category = "risk"
        hard = True
        reasons.append("operational_risk")
    if not from_me and QUESTION.search(text):
        score += 10
        reasons.append("question")

    ctx = context or []
    if not from_me and ctx:
        previous_external = [row for row in ctx[:-1] if not _from_me(row) and _text(row)]
        if len(previous_external) >= 2 and any(QUESTION.search(_text(row)) or REQUEST.search(_text(row)) for row in previous_external[-3:]):
            score += 10
            reasons.append("repeated_external_prompt")

    score = max(0, min(100, score))
    important = score >= 60
    confidence = min(0.98, 0.45 + score / 180 + (0.08 if hard else 0))
    label = CATEGORY_LABELS.get(category, CATEGORY_LABELS["general"])
    title = f"{label}｜{sender or '微信'}"
    if category == "my_promise":
        action = "按承诺完成事项，完成后再回复对方"
    elif category == "chase":
        action = "核对前情并尽快回复处理进展"
    elif category == "deadline":
        action = "确认截止时间并安排处理"
    elif category == "meeting":
        action = "确认会议时间与所需准备"
    elif category == "payment":
        action = "核对付款/结算状态并处理"
    elif category == "risk":
        action = "立即核实风险并处理"
    else:
        action = "查看上下文并处理或回复"
    return Decision(important, score, category, title, text[:140], action, _deadline_hint(text), round(confidence, 2), reasons, hard)


def reminder_key(message: dict[str, Any]) -> str:
    import hashlib
    raw = "|".join(
        [
            str(message.get("chat") or message.get("talker") or ""),
            str(message.get("server_id") or ""),
            str(message.get("local_id") or ""),
            str(message.get("create_time") or message.get("time") or ""),
            _text(message),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
