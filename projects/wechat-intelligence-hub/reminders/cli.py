from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys
from urllib.parse import parse_qs, urlparse
from typing import Any

from .config import default_config_path, initialize_config, load_config
from .local_model import LocalModelError, LocalSemanticClassifier
from .reader_client import ReaderClient, ReaderClientError
from .service import ReminderService, _reader_live
from .store import ALL_STATES, ReminderStore
from .windows import discover_windows_accounts


def _config(args: argparse.Namespace) -> dict[str, Any]:
    return load_config(Path(args.config).expanduser() if args.config else None)


def cmd_init(args: argparse.Namespace) -> int:
    path, config = initialize_config(
        Path(args.config).expanduser() if args.config else None,
        project_root=Path(args.project_root).expanduser() if args.project_root else None,
        overwrite=args.force,
        auto_local_model=not args.no_local_model,
    )
    print(f"提醒配置：{path}")
    print(f"状态库：{config['state_db']}")
    print(f"本地模型：{'启用 ' + config['local_model']['model'] if config['local_model']['enabled'] else '未启用（规则引擎仍可工作）'}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    result: dict[str, Any] = {
        "platform": platform.system(),
        "config": str(Path(args.config).expanduser()) if args.config else str(default_config_path()),
        "reader": {},
        "local_model": {},
        "windows_accounts": [],
    }
    if platform.system() == "Windows":
        result["windows_accounts"] = discover_windows_accounts()
    reader_ok = False
    try:
        reader = ReaderClient(config)
        status = reader.status()
        result["reader"]["command"] = reader.command
        result["reader"]["status"] = status
        result["reader"]["sessions"] = len(reader.sessions(3)) if _reader_live(status) else 0
        reader_ok = _reader_live(status)
        if not reader_ok:
            result["reader"]["error"] = "Reader 尚未达到实时数据库可读状态"
    except ReaderClientError as exc:
        result["reader"]["error"] = str(exc)

    model_ok = True
    model_config = config.get("local_model") if isinstance(config.get("local_model"), dict) else {}
    try:
        model = LocalSemanticClassifier(model_config)
        result["local_model"] = {
            "enabled": model.enabled,
            "provider": model.provider,
            "model": model.model,
            "privacy_boundary": "loopback_only",
        }
    except LocalModelError as exc:
        model_ok = False
        result["local_model"] = {"enabled": bool(model_config.get("enabled")), "error": str(exc)}

    try:
        store = ReminderStore(config["state_db"])
        store.close()
        result["state_db"] = {"ok": True}
        store_ok = True
    except Exception as exc:
        result["state_db"] = {"ok": False, "error": str(exc)[:200]}
        store_ok = False
    result["ok"] = reader_ok and model_ok and store_ok
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def cmd_once(args: argparse.Namespace) -> int:
    service = ReminderService(_config(args))
    try:
        result = service.run_once()
    finally:
        service.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if int(result.get("reader_failures") or 0) == 0 else 2


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
    if not rows:
        print("没有匹配的提醒。")
        return 0
    for row in rows:
        print(f"#{row['id']} [{row['state']}] {row['score']} {row['title']}｜{row['summary']}")
        if row.get("action"):
            print(f"  下一步：{row['action']}")
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


def cmd_action_uri(args: argparse.Namespace) -> int:
    uri = args.uri.strip().strip('"')
    parsed = urlparse(uri)
    scheme = str((_config(args).get("desktop") or {}).get("action_uri_scheme") or "wechatreminder")
    if parsed.scheme.casefold() != scheme.casefold():
        raise ValueError(f"不是 {scheme} 提醒 URI")
    action = (parsed.netloc or parsed.path.strip("/")).casefold()
    values = parse_qs(parsed.query)
    try:
        args.id = int((values.get("id") or [""])[0])
    except ValueError as exc:
        raise ValueError("提醒 URI 缺少有效 id") from exc
    if action == "done":
        return cmd_state(args, "DONE")
    if action == "ack":
        return cmd_state(args, "ACKNOWLEDGED")
    if action == "snooze":
        try:
            args.minutes = max(1, int((values.get("minutes") or ["30"])[0]))
        except ValueError:
            args.minutes = 30
        return cmd_state(args, "SNOOZED")
    if action == "suppress":
        return cmd_state(args, "SUPPRESSED")
    raise ValueError(f"不支持的提醒 URI 动作：{action}")


def cmd_test_notify(args: argparse.Namespace) -> int:
    config = _config(args)
    from .notifier import notify
    item = {
        "id": 0,
        "title": "微信重要信息提醒测试",
        "summary": "桌面/手机提醒通道测试成功时，你会看到这条消息。",
        "action": "无需处理",
        "score": 90,
        "source_chat": "通知测试",
    }
    errors = notify(item, config)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    print("提醒通道测试已发送。")
    return 0


def cmd_windows_discover(args: argparse.Namespace) -> int:
    rows = discover_windows_accounts()
    if args.json:
        print(json.dumps({"accounts": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"检测到 {len(rows)} 个带 db_storage 的微信账号目录。")
        for index, row in enumerate(rows, 1):
            print(f"{index}. {row['db_storage']}")
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
    s = sub.add_parser("doctor", help="检查 Reader、Windows 微信目录、本地模型隐私边界和提醒配置")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("once", help="运行一次增量扫描和提醒")
    s.set_defaults(func=cmd_once)
    s = sub.add_parser("run", help="持续运行提醒 Worker")
    s.set_defaults(func=cmd_run)
    s = sub.add_parser("list", help="查看提醒状态")
    s.add_argument("--state", choices=sorted(ALL_STATES))
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
    s = sub.add_parser("action-uri", help="处理 Windows Toast 操作按钮 URI")
    s.add_argument("uri")
    s.set_defaults(func=cmd_action_uri)
    s = sub.add_parser("windows-discover", help="只读发现 Windows 微信数据目录")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_windows_discover)
    s = sub.add_parser("test-notify", help="发送一条测试通知")
    s.set_defaults(func=cmd_test_notify)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ValueError, ReaderClientError, LocalModelError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
