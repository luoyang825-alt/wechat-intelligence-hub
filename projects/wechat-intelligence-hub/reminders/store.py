from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any

OPEN_STATES = {"NEW", "NOTIFIED", "SNOOZED", "ACKNOWLEDGED"}
CLOSED_STATES = {"DONE", "CANCELLED", "SUPPRESSED", "EXPIRED"}
ALL_STATES = OPEN_STATES | CLOSED_STATES


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ReminderStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS reminders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              reminder_key TEXT NOT NULL UNIQUE,
              correlation_key TEXT NOT NULL DEFAULT '',
              source_kind TEXT NOT NULL DEFAULT 'message',
              source_chat TEXT NOT NULL DEFAULT '',
              source_username TEXT NOT NULL DEFAULT '',
              source_message_id TEXT NOT NULL DEFAULT '',
              source_local_id INTEGER,
              source_time TEXT NOT NULL DEFAULT '',
              source_sender TEXT NOT NULL DEFAULT '',
              category TEXT NOT NULL,
              title TEXT NOT NULL,
              summary TEXT NOT NULL,
              action TEXT NOT NULL DEFAULT '',
              deadline TEXT NOT NULL DEFAULT '',
              score INTEGER NOT NULL,
              confidence REAL NOT NULL,
              reasons TEXT NOT NULL DEFAULT '[]',
              state TEXT NOT NULL DEFAULT 'NEW',
              next_notify_at TEXT NOT NULL DEFAULT '',
              notify_count INTEGER NOT NULL DEFAULT 0,
              last_notified_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              payload TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(state,next_notify_at,score);
            CREATE INDEX IF NOT EXISTS idx_reminders_chat ON reminders(source_chat,state);
            CREATE TABLE IF NOT EXISTS cursors (
              chat_key TEXT PRIMARY KEY,
              cursor INTEGER NOT NULL DEFAULT 0,
              last_seen_time TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def upsert_candidate(self, item: dict[str, Any]) -> tuple[int, bool]:
        key = str(item["reminder_key"])
        existing = self.conn.execute("SELECT id,state FROM reminders WHERE reminder_key=?", (key,)).fetchone()
        if existing:
            return int(existing["id"]), False
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO reminders(
              reminder_key,correlation_key,source_kind,source_chat,source_username,source_message_id,source_local_id,
              source_time,source_sender,category,title,summary,action,deadline,score,confidence,reasons,state,
              next_notify_at,created_at,updated_at,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'NEW',?,?,?,?)
            """,
            (
                key, str(item.get("correlation_key") or ""), str(item.get("source_kind") or "message"),
                str(item.get("source_chat") or ""), str(item.get("source_username") or ""), str(item.get("source_message_id") or ""),
                item.get("source_local_id"), str(item.get("source_time") or ""), str(item.get("source_sender") or ""),
                str(item.get("category") or "general"), str(item.get("title") or "微信提醒"), str(item.get("summary") or ""),
                str(item.get("action") or ""), str(item.get("deadline") or ""), int(item.get("score") or 0), float(item.get("confidence") or 0),
                json.dumps(item.get("reasons") or [], ensure_ascii=False), str(item.get("next_notify_at") or now), now, now,
                json.dumps(item.get("payload") or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM reminders WHERE reminder_key=?", (key,)).fetchone()
        return int(row["id"]), True

    def due(self, *, limit: int = 20) -> list[dict[str, Any]]:
        now = now_iso()
        rows = self.conn.execute(
            """SELECT * FROM reminders WHERE state IN ('NEW','SNOOZED','NOTIFIED')
               AND (next_notify_at='' OR next_notify_at<=?) ORDER BY score DESC, created_at ASC LIMIT ?""",
            (now, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_notified(self, reminder_id: int, *, repeat_minutes: int = 0) -> None:
        now = datetime.now().astimezone()
        next_at = (now + timedelta(minutes=repeat_minutes)).isoformat(timespec="seconds") if repeat_minutes else ""
        self.conn.execute(
            "UPDATE reminders SET state='NOTIFIED',notify_count=notify_count+1,last_notified_at=?,next_notify_at=?,updated_at=? WHERE id=?",
            (now.isoformat(timespec="seconds"), next_at, now.isoformat(timespec="seconds"), reminder_id),
        )
        self.conn.commit()

    def transition(self, reminder_id: int, state: str, *, snooze_minutes: int = 30) -> dict[str, Any]:
        state = state.upper()
        if state not in ALL_STATES:
            raise ValueError(f"不支持的提醒状态：{state}")
        now = datetime.now().astimezone()
        next_at = (now + timedelta(minutes=max(1, snooze_minutes))).isoformat(timespec="seconds") if state == "SNOOZED" else ""
        self.conn.execute("UPDATE reminders SET state=?,next_notify_at=?,updated_at=? WHERE id=?", (state, next_at, now.isoformat(timespec="seconds"), reminder_id))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        if not row:
            raise ValueError(f"找不到提醒：{reminder_id}")
        return dict(row)

    def list(self, *, state: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if state:
            rows = self.conn.execute("SELECT * FROM reminders WHERE state=? ORDER BY id DESC LIMIT ?", (state.upper(), max(1, limit))).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM reminders ORDER BY id DESC LIMIT ?", (max(1, limit),)).fetchall()
        return [dict(row) for row in rows]

    def get_cursor(self, chat_key: str) -> int:
        row = self.conn.execute("SELECT cursor FROM cursors WHERE chat_key=?", (chat_key,)).fetchone()
        return int(row["cursor"]) if row else 0

    def has_cursor(self, chat_key: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM cursors WHERE chat_key=?", (chat_key,)).fetchone())

    def set_cursor(self, chat_key: str, cursor: int, last_seen_time: str = "") -> None:
        now = now_iso()
        self.conn.execute(
            """INSERT INTO cursors(chat_key,cursor,last_seen_time,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(chat_key) DO UPDATE SET cursor=excluded.cursor,last_seen_time=excluded.last_seen_time,updated_at=excluded.updated_at""",
            (chat_key, max(0, int(cursor)), last_seen_time, now),
        )
        self.conn.commit()

    def get_health(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM health WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_health(self, key: str, value: str) -> None:
        now = now_iso()
        self.conn.execute(
            """INSERT INTO health(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (key, value, now),
        )
        self.conn.commit()
