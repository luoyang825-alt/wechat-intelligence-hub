from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .importance import evaluate_message, reminder_key
from .local_model import LocalSemanticClassifier, LocalModelError
from .notifier import notify
from .opportunity_bridge import due_opportunity_reminders, default_radar_db
from .reader_client import ReaderClient, ReaderClientError
from .store import ReminderStore, now_iso


def _chat_key(session: dict[str, Any]) -> tuple[str, str]:
    username = str(session.get("username") or session.get("talker") or session.get("chatroom_id") or "").strip()
    display = str(session.get("display_name") or session.get("remark") or session.get("nick_name") or username).strip()
    return username or display, display or username


def _message_time(row: dict[str, Any]) -> str:
    return str(row.get("time") or row.get("create_time") or "")


def _message_local_id(row: dict[str, Any]) -> int:
    try:
        return int(row.get("local_id") or 0)
    except (TypeError, ValueError):
        return 0


def _reader_live(status: dict[str, Any]) -> bool:
    for key in ("live_database_read_ok", "live_read_ok"):
        if key in status:
            return bool(status.get(key))
    readiness = str(status.get("readiness") or status.get("summary") or "").casefold()
    return readiness in {"ready", "ok"}


def _build_item(chat: str, username: str, row: dict[str, Any], decision: Any) -> dict[str, Any]:
    return {
        "reminder_key": reminder_key({**row, "chat": chat}),
        "correlation_key": reminder_key({"chat": chat, "text": decision.summary})[:32],
        "source_kind": "message",
        "source_chat": chat,
        "source_username": username,
        "source_message_id": str(row.get("server_id") or row.get("local_id") or ""),
        "source_local_id": _message_local_id(row) or None,
        "source_time": _message_time(row),
        "source_sender": str(row.get("sender") or ""),
        "category": decision.category,
        "title": decision.title,
        "summary": decision.summary,
        "action": decision.action,
        "deadline": decision.deadline,
        "score": decision.score,
        "confidence": decision.confidence,
        "reasons": decision.reasons,
        "next_notify_at": now_iso(),
        "payload": {"hard_signal": decision.hard_signal},
    }


class ReminderService:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.store = ReminderStore(config["state_db"])
        self.reader = ReaderClient(config)
        self.model = LocalSemanticClassifier(config.get("local_model") or {})

    def close(self) -> None:
        self.store.close()

    def _priority_chat(self, display: str, username: str) -> bool:
        configured = [str(x).casefold() for x in self.config.get("priority_chats") or []]
        haystack = f"{display} {username}".casefold()
        return any(value and value in haystack for value in configured)

    def _bootstrap_cursor(self, username: str, display: str) -> int:
        minutes = max(1, int(self.config.get("bootstrap_lookback_minutes") or 120))
        since = (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        rows = self.reader.timeline(username, since=since, limit=300)
        max_cursor = 0
        for row in rows:
            max_cursor = max(max_cursor, _message_local_id(row))
            self._process_message(display, username, row)
        self.store.set_cursor(username, max_cursor, _message_time(rows[-1]) if rows else "")
        return len(rows)

    def _process_message(self, display: str, username: str, row: dict[str, Any]) -> bool:
        local_id = _message_local_id(row)
        context: list[dict[str, Any]] = []
        decision = evaluate_message(row, priority_chat=self._priority_chat(display, username))
        candidate_threshold = int((self.config.get("thresholds") or {}).get("context_candidate") or 35)
        if decision.score >= candidate_threshold and local_id:
            try:
                context = self.reader.context(username, local_id=local_id, before=int(self.config.get("context_before") or 4), after=int(self.config.get("context_after") or 1))
                decision = evaluate_message(row, context, priority_chat=self._priority_chat(display, username))
            except ReaderClientError:
                context = []
        try:
            decision = self.model.enhance(row, context, decision)
        except LocalModelError:
            pass
        store_threshold = int((self.config.get("thresholds") or {}).get("store") or 60)
        if decision.score < store_threshold or not decision.important:
            return False
        _id, created = self.store.upsert_candidate(_build_item(display, username, row, decision))
        return created

    def ingest_messages(self) -> dict[str, int]:
        sessions = self.reader.sessions(int(self.config.get("session_limit") or 150))
        scanned = new_messages = created = 0
        for session in sessions:
            username, display = _chat_key(session)
            if not username:
                continue
            scanned += 1
            if not self.store.has_cursor(username):
                new_messages += self._bootstrap_cursor(username, display)
                continue
            cursor = self.store.get_cursor(username)
            rows, next_cursor = self.reader.tail(username, cursor=cursor, limit=120)
            for row in rows:
                new_messages += 1
                created += int(self._process_message(display, username, row))
            if next_cursor > cursor:
                self.store.set_cursor(username, next_cursor, _message_time(rows[-1]) if rows else "")
        return {"sessions": scanned, "messages": new_messages, "created": created}

    def ingest_followups(self) -> int:
        radar = Path(str(self.config.get("radar_db") or default_radar_db())).expanduser()
        count = 0
        for item in due_opportunity_reminders(radar):
            _, created = self.store.upsert_candidate(item)
            count += int(created)
        return count

    def _record_reader_failure(self, failures: int, error: Exception) -> int:
        health = self.config.get("health") or {}
        threshold = max(1, int(health.get("failure_threshold") or 3))
        if failures < threshold:
            return 0
        last_success = self.store.get_health("last_reader_success", "never")
        episode = hashlib.sha256(f"reader-health|{last_success}".encode("utf-8")).hexdigest()
        item = {
            "reminder_key": episode,
            "correlation_key": "reader-health",
            "source_kind": "health",
            "source_chat": "WeChat Reader",
            "source_username": "",
            "source_message_id": "",
            "source_local_id": None,
            "source_time": now_iso(),
            "source_sender": "system",
            "category": "risk",
            "title": "微信提醒读取异常",
            "summary": f"Reader 已连续失败 {failures} 次，当前提醒可能存在覆盖缺口。",
            "action": "运行 reminder_cli.py doctor，恢复 Reader 后再继续常驻任务",
            "deadline": "",
            "score": 92,
            "confidence": 1.0,
            "reasons": ["reader_repeated_failure"],
            "next_notify_at": now_iso(),
            "payload": {"error_type": type(error).__name__},
        }
        _, created = self.store.upsert_candidate(item)
        return int(created)

    def deliver(self) -> dict[str, int]:
        thresholds = self.config.get("thresholds") or {}
        notify_threshold = int(thresholds.get("notify") or 70)
        critical = int(thresholds.get("critical") or 88)
        repeat_minutes = max(1, int(self.config.get("critical_repeat_minutes") or 30))
        max_repeats = max(1, int(self.config.get("critical_max_notifications") or 3))
        sent = skipped = 0
        for row in self.store.due(limit=30):
            if int(row.get("score") or 0) < notify_threshold:
                skipped += 1
                continue
            count = int(row.get("notify_count") or 0)
            if count > 0 and int(row.get("score") or 0) < critical:
                skipped += 1
                continue
            if count >= max_repeats:
                skipped += 1
                continue
            errors = notify(row, self.config)
            if errors and bool((self.config.get("desktop") or {}).get("enabled", True)) and not bool((self.config.get("mobile") or {}).get("enabled", False)):
                skipped += 1
                continue
            self.store.mark_notified(int(row["id"]), repeat_minutes=repeat_minutes if int(row.get("score") or 0) >= critical and count + 1 < max_repeats else 0)
            sent += 1
        return {"sent": sent, "skipped": skipped}

    def run_once(self) -> dict[str, Any]:
        failures = int(self.store.get_health("reader_failures", "0") or 0)
        health_created = 0
        try:
            status = self.reader.status()
            if not _reader_live(status):
                raise ReaderClientError("Reader 尚未达到实时数据库可读状态")
            ingest = self.ingest_messages()
            failures = 0
            self.store.set_health("reader_failures", "0")
            self.store.set_health("last_reader_success", now_iso())
        except ReaderClientError as exc:
            failures += 1
            self.store.set_health("reader_failures", str(failures))
            self.store.set_health("last_reader_error", str(exc)[:240])
            health_created = self._record_reader_failure(failures, exc)
            ingest = {"sessions": 0, "messages": 0, "created": 0}
        followups = self.ingest_followups()
        delivered = self.deliver()
        return {"reader_failures": failures, "health_created": health_created, "ingest": ingest, "followups": followups, "delivery": delivered}

    def run_forever(self) -> None:
        interval = max(5, int(self.config.get("poll_interval_seconds") or 15))
        while True:
            result = self.run_once()
            print(json.dumps(result, ensure_ascii=False), flush=True)
            time.sleep(interval)
