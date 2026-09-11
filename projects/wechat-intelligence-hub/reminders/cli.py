from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys
from typing import Any

from .config import default_config_path, initialize_config, load_config, save_config
from .reader_client import ReaderClient, ReaderClientError
from .service import ReminderService
from .store import ReminderStore
from .windows import discover_windows_accounts


def _config(args: argparse.Namespace) -> dict[str, Any]:
    return load_config(Path(args.config).expanduser() if args.config else None)


def cmd_init(args: argparse.Namespace) -> int:
    path, config = initialize_config(Path(args.config).expanduser() if args.config else None, project_root=Path(args.project_root).expanduser() if args.project_root else None, overwrite=args.force, auto_local_model=not args.no_local_model)
    print(f"提醒配置：{path}")
    print(f"状态库：{config['state_db']}")
    print(f"本地模型：{'启用 ' + config['local_model']['model'] if config['local_model']['enabled'] else '未启用（规则引擎仍可工作）'}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    result: dict[str, Any] = {"platform": platform.system(), "config": str(Path(args.config).expanduser()) if args.config else str(default_config_path()), "reader": {}, "windows_accounts": []}
    if platform.system() == "Windows":
        result["windows_accounts"] = discover_windows_accounts()
    try:
        reader = ReaderClient(config)
        result["reader"]["command"] = reader.command
        result["reader"]["status"] = reader.status()
        result["reader"]["sessions"] = len(reader.sessions(3))
        result["ok"] = True
    except ReaderClientError as exc:
        result["ok"] = False
        result["reader"]["error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def cmd_once(args: argparse.Namespace) -> int:
    service = ReminderService(_config(args))
    try:
        result = service.run_once()
    finally:
        service.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    service = ReminderService(_config(args))
    try:
        service.run_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        service.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    config = _config(args)
    store = ReminderStore(config["state_db"])
    try:
        rows = store.list(state=args.state or "", limit=args.limit)
    finally:
        store.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        print(f"#{row['id']} [{row['state']}] {row['score']} {row['title']}｜{row['summary']}")
    return 0


def cmd_state(args: argparse.Namespace, state: str) -> int:
    config = _config(args)
    store = ReminderStore(config["state_db"])
    try:
        row = store.transition(args.id, state, snooze_minutes=getattr(args, "minutes", 30))
    finally:
        store.close()
    print(f"提醒 #{row['id']} → {row['state']}")
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    config = _config(args)
    from .notifier import notify
    item = {"id": 0, "title": "微信重要信息提醒测试", "summary": "桌面/手机提醒通道测试成功时，你会看到这条消息。", "action": "无需处理", "score": 90}
    errors = notify(item, config)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    print("提醒通道测试已发送。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WeChat Intelligence Hub 主动提醒子系统")
    p.add_argument("--config", help="reminders.json 路径；默认使用系统私有配置目录")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("init", help="创建提醒配置，并自动检测可用本地 Ollama 模型")
    s.add_argument("--project-root", help="仓库根目录，用于自动发现个人 Profile")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-local-model", action="store_true")
    s.set_defaults(func=cmd_init)
    s = sub.add_parser("doctor", help="检查 Reader、Windows 微信目录和提醒配置")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("once", help="运行一次增量扫描和提醒")
    s.set_defaults(func=cmd_once)
    s = sub.add_parser("run", help="持续运行提醒 Worker")
    s.set_defaults(func=cmd_run)
    s = sub.add_parser("list", help="查看提醒状态")
    s.add_argument("--state")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)
    for name, state in (("ack", "ACKNOWLEDGED"), ("done", "DONE"), ("suppress", "SUPPRESSED"), ("cancel", "CANCELLED")):
        s = sub.add_parser(name)
        s.add_argument("id", type=int)
        s.set_defaults(func=lambda a, value=state: cmd_state(a, value))
    s = sub.add_parser("snooze")
    s.add_argument("id", type=int)
    s.add_argument("--minutes", type=int, default=30)
    s.set_defaults(func=lambda a: cmd_state(a, "SNOOZED"))
    s = sub.add_parser("test-notify", help="发送一条测试通知")
    s.set_defaults(func=cmd_test_notify)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ValueError, ReaderClientError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
