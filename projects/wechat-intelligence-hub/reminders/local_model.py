from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .importance import Decision


class LocalModelError(RuntimeError):
    pass


def _is_loopback_host(host: str) -> bool:
    host = (host or "").strip().strip("[]").casefold()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = []
    for info in infos:
        candidate = info[4][0]
        try:
            addresses.append(ipaddress.ip_address(candidate))
        except ValueError:
            return False
    return bool(addresses) and all(address.is_loopback for address in addresses)


def validate_local_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise LocalModelError("本地模型地址只允许 http/https")
    if not parsed.hostname or not _is_loopback_host(parsed.hostname):
        raise LocalModelError("本地模型只允许连接 localhost/127.0.0.0/8/::1，禁止把微信原文发送到远程模型")
    return value.rstrip("/")


def _extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, re.S)
        if not match:
            raise LocalModelError("本地模型没有返回 JSON")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LocalModelError("本地模型 JSON 无法解析") from exc
    if not isinstance(parsed, dict):
        raise LocalModelError("本地模型必须返回 JSON 对象")
    return parsed


class LocalSemanticClassifier:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.enabled = bool(config.get("enabled")) and bool(str(config.get("model") or "").strip())
        self.provider = str(config.get("provider") or "ollama").strip().casefold()
        self.base_url = validate_local_url(str(config.get("base_url") or "http://127.0.0.1:11434"))
        self.model = str(config.get("model") or "").strip()
        self.timeout = max(1.0, float(config.get("timeout_seconds") or 8))
        self.min_rule_score = int(config.get("min_rule_score") or 35)
        self.max_rule_score = int(config.get("max_rule_score") or 84)

    def should_run(self, decision: Decision) -> bool:
        return self.enabled and self.min_rule_score <= decision.score <= self.max_rule_score

    def _prompt(self, message: dict[str, Any], context: list[dict[str, Any]], decision: Decision) -> str:
        def content(row: dict[str, Any]) -> str:
            return str(row.get("text") or row.get("content") or "").strip()

        context_lines = []
        for row in context[-8:]:
            text = content(row)
            if not text:
                continue
            sender = str(row.get("sender") or "")
            context_lines.append(f"{sender}: {text[:240]}")
        message_text = content(message)[:800]
        return (
            "你是本地运行的微信行动提醒分类器。只判断是否值得提醒本人，不扩写聊天内容。\n"
            "优先提醒：明确要求本人处理/回复；明确截止时间；本人做出的承诺；对方催办；@本人；会议/审批/付款/异常风险。\n"
            "不要因为普通聊天、转发、公众号通知、泛泛讨论而提醒。\n"
            "返回严格 JSON："
            '{"important":true,"score_adjustment":0,"category":"direct_request",'
            '"summary":"不超过60字","action":"不超过40字","deadline":"原文可确定才填写",'
            '"confidence":0.8}. '
            "score_adjustment 只能是 -15 到 15；不能凭空编造日期、金额、身份或承诺。\n"
            f"规则初判：score={decision.score}, category={decision.category}, reasons={','.join(decision.reasons)}\n"
            f"当前消息：{message_text}\n"
            f"最近上下文：\n" + "\n".join(context_lines)
        )

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as response:
                body = response.read(512_000).decode("utf-8", errors="replace")
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            raise LocalModelError(f"本地模型调用失败：{type(exc).__name__}") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LocalModelError("本地模型接口没有返回 JSON") from exc
        if not isinstance(parsed, dict):
            raise LocalModelError("本地模型接口响应格式不正确")
        return parsed

    def classify(self, message: dict[str, Any], context: list[dict[str, Any]], decision: Decision) -> dict[str, Any]:
        prompt = self._prompt(message, context, decision)
        if self.provider == "ollama":
            raw = self._post_json(
                self.base_url + "/api/chat",
                {
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": 0},
                },
            )
            content = str((raw.get("message") or {}).get("content") or "")
            return _extract_json(content)
        if self.provider in {"openai", "openai_compatible", "openai-compatible"}:
            base = self.base_url
            endpoint = base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"
            raw = self._post_json(
                endpoint,
                {
                    "model": self.model,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )
            choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
            content = ""
            if choices and isinstance(choices[0], dict):
                content = str((choices[0].get("message") or {}).get("content") or "")
            return _extract_json(content)
        raise LocalModelError(f"不支持的本地模型 provider：{self.provider}")

    def enhance(self, message: dict[str, Any], context: list[dict[str, Any]], decision: Decision) -> Decision:
        if not self.should_run(decision):
            return decision
        result = self.classify(message, context, decision)
        try:
            adjustment = max(-15, min(15, int(result.get("score_adjustment") or 0)))
        except (TypeError, ValueError):
            adjustment = 0
        score = max(0, min(100, decision.score + adjustment))
        important = bool(result.get("important")) or decision.hard_signal
        if not important and not decision.hard_signal:
            score = min(score, 59)
        category = str(result.get("category") or decision.category).strip()[:40] or decision.category
        summary = str(result.get("summary") or decision.summary).strip()[:140] or decision.summary
        action = str(result.get("action") or decision.action).strip()[:120] or decision.action
        deadline = str(result.get("deadline") or decision.deadline).strip()[:40]
        try:
            model_conf = float(result.get("confidence") or decision.confidence)
        except (TypeError, ValueError):
            model_conf = decision.confidence
        confidence = max(decision.confidence if decision.hard_signal else 0.0, min(0.99, max(0.0, model_conf)))
        reasons = list(dict.fromkeys([*decision.reasons, "local_model_reviewed"]))
        return Decision(
            important=score >= 60,
            score=score,
            category=category,
            title=decision.title,
            summary=summary,
            action=action,
            deadline=deadline,
            confidence=round(confidence, 2),
            reasons=reasons,
            hard_signal=decision.hard_signal,
        )
