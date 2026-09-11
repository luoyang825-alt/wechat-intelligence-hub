from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reminders.cli import main as reminder_main
from reminders.config import deep_merge, default_config
from reminders.importance import evaluate_message
from reminders.local_model import LocalModelError, validate_local_url
from reminders.opportunity_bridge import due_opportunity_reminders
from reminders.service import ReminderService, _reader_live, _session_may_have_newer
from reminders.store import ReminderStore
from reminders.windows import likely_windows_roots


class ReminderImportanceTests(unittest.TestCase):
    def test_direct_request_is_actionable(self):
        row = {"sender": "同事A", "text": "麻烦今天下班前确认一下方案，可以吗？", "from_me": False}
        decision = evaluate_message(row)
        self.assertTrue(decision.important)
        self.assertGreaterEqual(decision.score, 70)
        self.assertEqual(decision.category, "direct_request")
        self.assertTrue(decision.deadline)

    def test_self_promise_wins_over_generic_deadline(self):
        row = {"sender": "我", "text": "我明天把最终方案发给你", "from_me": True}
        decision = evaluate_message(row)
        self.assertTrue(decision.important)
        self.assertEqual(decision.category, "my_promise")
        self.assertIn("explicit_self_promise", decision.reasons)

    def test_chase_is_actionable_without_extra_keywords(self):
        decision = evaluate_message({"sender": "同事A", "text": "昨天说的方案怎么样了", "from_me": False})
        self.assertTrue(decision.important)
        self.assertEqual(decision.category, "chase")

    def test_mention_is_actionable_without_extra_keywords(self):
        decision = evaluate_message({"sender": "同事A", "text": "@我 看一下", "from_me": False})
        self.assertTrue(decision.important)
        self.assertEqual(decision.category, "mention")

    def test_closure_is_not_reminded(self):
        decision = evaluate_message({"sender": "同事A", "text": "收到", "from_me": False})
        self.assertFalse(decision.important)
        self.assertEqual(decision.score, 0)

    def test_operational_risk_is_critical(self):
        decision = evaluate_message({"sender": "同事A", "text": "生产服务无法登录，紧急处理", "from_me": False})
        self.assertTrue(decision.important)
        self.assertGreaterEqual(decision.score, 88)
        self.assertEqual(decision.category, "risk")

    def test_generic_failure_word_is_not_operational_risk(self):
        decision = evaluate_message({"sender": "同事A", "text": "这个方案整理了很多失败案例", "from_me": False})
        self.assertFalse(decision.important)
        self.assertNotEqual(decision.category, "risk")


class ReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "reminders.db"
        self.store = ReminderStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def sample(self, *, key="k1", correlation="c1", score=88, summary="麻烦确认方案"):
        return {
            "reminder_key": key, "correlation_key": correlation, "source_kind": "message",
            "source_chat": "项目群", "source_username": "chat-1", "source_message_id": key,
            "source_local_id": 10, "source_time": "2026-09-11T08:00:00+08:00", "source_sender": "同事A",
            "category": "direct_request", "title": "待我处理｜同事A", "summary": summary,
            "action": "确认方案", "deadline": "今天", "score": score, "confidence": 0.9,
            "reasons": ["direct_request"], "payload": {}
        }

    def test_upsert_is_idempotent(self):
        first, created1 = self.store.upsert_candidate(self.sample())
        second, created2 = self.store.upsert_candidate(self.sample())
        self.assertEqual(first, second)
        self.assertTrue(created1)
        self.assertFalse(created2)

    def test_same_correlation_merges_open_reminder(self):
        first, created1 = self.store.upsert_candidate(self.sample(key="k1", correlation="same", score=72))
        second, created2 = self.store.upsert_candidate(self.sample(key="k2", correlation="same", score=95, summary="再次麻烦确认方案"))
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first, second)
        rows = self.store.list(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 95)
        self.assertEqual(rows[0]["summary"], "再次麻烦确认方案")

    def test_snooze_and_done(self):
        reminder_id, _ = self.store.upsert_candidate(self.sample())
        row = self.store.transition(reminder_id, "SNOOZED", snooze_minutes=5)
        self.assertEqual(row["state"], "SNOOZED")
        self.assertTrue(row["next_notify_at"])
        row = self.store.transition(reminder_id, "DONE")
        self.assertEqual(row["state"], "DONE")
        self.assertEqual(row["next_notify_at"], "")

    def test_notified_without_repeat_is_not_due(self):
        reminder_id, _ = self.store.upsert_candidate(self.sample())
        self.store.mark_notified(reminder_id, repeat_minutes=0)
        self.assertEqual(self.store.due(), [])

    def test_cursor_roundtrip_and_metadata(self):
        self.assertFalse(self.store.has_cursor("chat"))
        self.store.set_cursor("chat", 9, "2026-09-11T08:00:00+08:00")
        self.assertTrue(self.store.has_cursor("chat"))
        self.assertEqual(self.store.get_cursor("chat"), 9)
        self.assertEqual(self.store.cursor_row("chat")["last_seen_time"], "2026-09-11T08:00:00+08:00")


class LocalModelTests(unittest.TestCase):
    def test_loopback_model_url_allowed(self):
        self.assertEqual(validate_local_url("http://127.0.0.1:11434"), "http://127.0.0.1:11434")
        self.assertEqual(validate_local_url("http://localhost:11434/"), "http://localhost:11434")

    def test_remote_model_url_rejected(self):
        with self.assertRaises(LocalModelError):
            validate_local_url("https://example.com/v1")

    def test_nonliteral_hostname_rejected_even_if_it_could_resolve_locally(self):
        with self.assertRaises(LocalModelError):
            validate_local_url("http://model.internal:11434")


class ReaderHealthTests(unittest.TestCase):
    def test_reader_live_supports_both_status_contracts(self):
        self.assertTrue(_reader_live({"live_database_read_ok": True}))
        self.assertTrue(_reader_live({"live_read_ok": True}))
        self.assertTrue(_reader_live({"readiness": "ready"}))
        self.assertFalse(_reader_live({"live_read_ok": False}))
        self.assertFalse(_reader_live({}))

    def test_session_newer_detects_rotation_recovery_need(self):
        last_seen = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertTrue(_session_may_have_newer({"last_timestamp": int(datetime.now().timestamp())}, last_seen))

    def test_ingest_recovers_when_local_id_resets(self):
        class RotatedReader:
            def sessions(self, limit):
                return [{"username": "chat-1", "display_name": "项目群", "last_timestamp": int(datetime.now().timestamp())}]
            def tail(self, chat, cursor, limit=120):
                self.tail_cursor = cursor
                return [], cursor
            def timeline(self, chat, since="", limit=200):
                return [{"local_id": 2, "server_id": 2002, "time": datetime.now().astimezone().isoformat(timespec="seconds"), "sender": "A", "text": "收到", "from_me": False}]

        with tempfile.TemporaryDirectory() as tmp:
            service = ReminderService.__new__(ReminderService)
            service.config = {"session_limit": 10}
            service.store = ReminderStore(Path(tmp) / "state.db")
            service.reader = RotatedReader()
            service.store.set_cursor("chat-1", 999, (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds"))
            service._process_message = lambda display, username, row: False
            try:
                result = service.ingest_messages()
                cursor = service.store.get_cursor("chat-1")
            finally:
                service.store.close()
            self.assertEqual(result["recovered_sessions"], 1)
            self.assertEqual(cursor, 2)


class OpportunityBridgeTests(unittest.TestCase):
    def test_due_followup_becomes_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            conn = sqlite3.connect(db)
            conn.execute("""CREATE TABLE opportunities(id INTEGER PRIMARY KEY, chat TEXT, title TEXT, stage TEXT, status TEXT, priority INTEGER, next_action TEXT, next_follow_up TEXT, last_signal_time TEXT)""")
            conn.execute("INSERT INTO opportunities VALUES(1,'客户A','项目A','待回复','active',5,'回复方案','2026-09-10','2026-09-09')")
            conn.commit(); conn.close()
            rows = due_opportunity_reminders(db, today="2026-09-11")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["category"], "follow_up_due")
            self.assertGreaterEqual(rows[0]["score"], 90)


class ActionUriTests(unittest.TestCase):
    def test_done_uri_updates_local_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "reminders.json"
            state_path = Path(tmp) / "state.db"
            cfg = default_config()
            cfg["state_db"] = str(state_path)
            cfg["desktop"]["action_uri_scheme"] = "wechatreminder"
            config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            store = ReminderStore(state_path)
            reminder_id, _ = store.upsert_candidate({
                "reminder_key": "uri-k", "correlation_key": "uri-c", "category": "direct_request",
                "title": "测试", "summary": "测试", "score": 80, "confidence": 1.0, "reasons": []
            })
            store.close()
            code = reminder_main(["--config", str(config_path), "action-uri", f"wechatreminder://done?id={reminder_id}"])
            self.assertEqual(code, 0)
            store = ReminderStore(state_path)
            try:
                self.assertEqual(store.list(limit=1)[0]["state"], "DONE")
            finally:
                store.close()


class ConfigurationTests(unittest.TestCase):
    def test_deep_merge_keeps_threshold_defaults(self):
        merged = deep_merge(default_config(), {"thresholds": {"notify": 80}})
        self.assertEqual(merged["thresholds"]["notify"], 80)
        self.assertIn("critical", merged["thresholds"])
        self.assertIn("radar_db", merged)

    def test_windows_roots_honor_explicit_environment(self):
        with patch.dict("os.environ", {"WECHAT_DATA_DIR": "C:/private-wechat-root"}, clear=False):
            roots = likely_windows_roots()
        self.assertEqual(str(roots[0]).replace("\\", "/"), "C:/private-wechat-root")


if __name__ == "__main__":
    unittest.main()
