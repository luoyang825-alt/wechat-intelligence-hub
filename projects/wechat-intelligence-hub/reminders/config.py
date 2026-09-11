from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any

SCHEMA_VERSION = 1


def _windows_base() -> Path:
    value = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Local"


def default_config_path() -> Path:
    if platform.system() == "Windows":
        return _windows_base() / "WeChatIntelligenceHub" / "reminders.json"
    return Path.home() / ".config" / "wechat-intelligence-hub" / "reminders.json"


def default_data_dir() -> Path:
    if platform.system() == "Windows":
        return _windows_base() / "WeChatIntelligenceHub"
    return Path.home() / ".wechat-intelligence-hub"


def default_state_db() -> Path:
    return default_data_dir() / "reminders.db"


def default_log_path() -> Path:
    return default_data_dir() / "reminder-worker.log"


def default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "poll_interval_seconds": 15,
        "reader_timeout_seconds": 25,
        "session_limit": 150,
        "bootstrap_lookback_minutes": 120,
        "context_before": 4,
        "context_after": 1,
        "critical_repeat_minutes": 30,
        "critical_max_notifications": 3,
        "reader_command": [],
        "reader_config": "",
        "profile_path": "",
        "label_contacts_csv": "",
        "state_db": str(default_state_db()),
        "log_path": str(default_log_path()),
        "priority_chats": [],
        "thresholds": {
            "context_candidate": 35,
            "store": 60,
            "notify": 70,
            "critical": 88,
        },
        "health": {
            "failure_threshold": 3,
            "failure_notice_cooldown_minutes": 60,
        },
        "local_model": {
            "enabled": False,
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "",
            "timeout_seconds": 8,
            "min_rule_score": 35,
            "max_rule_score": 84,
        },
        "desktop": {
            "enabled": True,
            "actions_enabled": False,
            "action_uri_scheme": "wechatreminder",
        },
        "mobile": {
            "enabled": False,
            "provider": "ntfy",
            "url": "",
            "token": "",
            "timeout_seconds": 8,
            "include_chat_name": True,
        },
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取提醒配置：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"提醒配置顶层必须是 JSON 对象：{path}")
    return value


def load_config(path: Path | None = None) -> dict[str, Any]:
    selected = (path or default_config_path()).expanduser()
    if not selected.exists():
        config = default_config()
    else:
        config = deep_merge(default_config(), load_json(selected))
    if int(config.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError(f"不支持的提醒配置版本：{config.get('schema_version')}")
    return config


def _detect_ollama_model() -> str:
    exe = shutil.which("ollama")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    rows = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(rows) < 2:
        return ""
    for line in rows[1:]:
        name = line.split()[0].strip()
        if name:
            return name
    return ""


def _first_existing(paths: list[Path]) -> str:
    for path in paths:
        if path.is_file():
            return str(path.resolve())
    return ""


def initialize_config(
    path: Path | None = None,
    *,
    project_root: Path | None = None,
    overwrite: bool = False,
    auto_local_model: bool = True,
) -> tuple[Path, dict[str, Any]]:
    selected = (path or default_config_path()).expanduser()
    if selected.exists() and not overwrite:
        return selected, load_config(selected)

    config = default_config()
    root = project_root.resolve() if project_root and project_root.exists() else None
    if root:
        profile = _first_existing(
            [
                root / "projects" / "wechat-intelligence-hub" / "config" / "profile.local.json",
                root / "config" / "profile.local.json",
            ]
        )
        contacts = _first_existing(
            [
                root / "projects" / "wechat-intelligence-hub" / "contacts" / "微信标签联系人.csv",
                root / "contacts" / "微信标签联系人.csv",
            ]
        )
        if profile:
            config["profile_path"] = profile
        if contacts:
            config["label_contacts_csv"] = contacts

    env_mobile = os.environ.get("WECHAT_REMINDER_MOBILE_URL", "").strip()
    if env_mobile:
        config["mobile"]["enabled"] = True
        config["mobile"]["url"] = env_mobile

    if auto_local_model:
        model = os.environ.get("WECHAT_REMINDER_MODEL", "").strip() or _detect_ollama_model()
        if model:
            config["local_model"]["enabled"] = True
            config["local_model"]["model"] = model

    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(selected, 0o600)
    except OSError:
        pass
    return selected, config


def save_config(config: dict[str, Any], path: Path | None = None) -> Path:
    selected = (path or default_config_path()).expanduser()
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(selected, 0o600)
    except OSError:
        pass
    return selected
