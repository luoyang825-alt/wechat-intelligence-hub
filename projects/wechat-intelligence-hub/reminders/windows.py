from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def likely_windows_roots() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    documents = home / "Documents"
    roots = [
        home / "xwechat_files",
        documents / "xwechat_files",
        documents / "WeChat Files" / "xwechat_files",
        documents / "WeChat Files",
        home / "WeChat Files" / "xwechat_files",
        home / "WeChat Files",
        appdata / "Tencent" / "WeChat" / "WeChat Files" / "xwechat_files",
        appdata / "Tencent" / "WeChat" / "WeChat Files",
    ]
    custom = os.environ.get("WECHAT_DATA_DIR", "").strip()
    if custom:
        roots.insert(0, Path(custom).expanduser())
    result: list[Path] = []
    seen: set[str] = set()
    for path in roots:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def discover_windows_accounts() -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in likely_windows_roots():
        if not base.is_dir():
            continue
        candidates = []
        if (base / "db_storage").is_dir():
            candidates.append(base)
        try:
            children = list(base.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and (child / "db_storage").is_dir():
                candidates.append(child)
        for account in candidates:
            try:
                resolved = account.resolve()
            except OSError:
                resolved = account
            key = str(resolved).casefold()
            if key in seen:
                continue
            seen.add(key)
            accounts.append(
                {
                    "account_root": str(resolved),
                    "db_storage": str(resolved / "db_storage"),
                    "source_root": str(base),
                }
            )
    return accounts
