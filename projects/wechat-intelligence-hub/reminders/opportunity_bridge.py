from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
from typing import Any


def default_radar_db() -> Path:
    return Path("~/.wechat-intelligence-hub/radar.db").expanduser()


def _exists_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _score(priority: int, stage: str) -> int:
    base = {0: 60, 1: 62, 2: 66, 3: 72, 4: 84, 5: 94}.get(max(0, min(5, priority)), 72)
    if stage == "已发布待结算":
        base = max(base, 86)
    return base


def due_opportunity_reminders(db_path: str | Path, *, today: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    path = Path(db_path).expanduser()
    if not path.is_file():
        return []
    current_day = today or datetime.now().astimezone().date().isoformat()
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not _exists_table(conn, "opportunities"):
            return []
        rows = conn.execute(
            """
            SELECT id, chat, title, stage, status, priority, next_action, next_follow_up, last_signal_time
            FROM opportunities
            WHERE status IN ('active','waiting','paused')
              AND next_follow_up IS NOT NULL AND next_follow_up != ''
              AND substr(next_follow_up,1,10) <= ?
            ORDER BY substr(next_follow_up,1,10) ASC, priority DESC, id ASC
            LIMIT ?
            """,
            (current_day, max(1, limit)),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        opp_id = int(item["id"])
        follow = str(item.get("next_follow_up") or "")
        key = hashlib.sha256(f"opportunity|{opp_id}|{follow}".encode("utf-8")).hexdigest()
        correlation = hashlib.sha256(f"opportunity|{opp_id}".encode("utf-8")).hexdigest()[:32]
        action = str(item.get("next_action") or "到期跟进该事项")
        title = str(item.get("title") or item.get("chat") or f"商机 #{opp_id}")
        result.append(
            {
                "reminder_key": key,
                "correlation_key": correlation,
                "source_kind": "opportunity_follow_up",
                "source_chat": str(item.get("chat") or ""),
                "source_username": "",
                "source_message_id": f"opportunity:{opp_id}",
                "source_local_id": None,
                "source_time": str(item.get("last_signal_time") or ""),
                "source_sender": "",
                "category": "follow_up_due",
                "title": f"跟进到期｜{title}",
                "summary": f"已到跟进日期：{follow}",
                "action": action,
                "deadline": follow,
                "score": _score(int(item.get("priority") or 0), str(item.get("stage") or "")),
                "confidence": 1.0,
                "reasons": ["existing_opportunity_follow_up"],
                "next_notify_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "payload": {"opportunity_id": opp_id, "stage": item.get("stage"), "status": item.get("status")},
            }
        )
    return result
