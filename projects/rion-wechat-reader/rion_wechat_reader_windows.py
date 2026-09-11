#!/usr/bin/env python3
"""Windows launcher for the existing read-only Rion WeChat Reader.

It only adds safe local database-root discovery; all parsing/decryption/query logic
continues to live in rion_wechat_reader.py.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import rion_wechat_reader as core


def windows_roots() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    docs = home / "Documents"
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    roots = [
        home / "xwechat_files",
        docs / "xwechat_files",
        docs / "WeChat Files" / "xwechat_files",
        docs / "WeChat Files",
        home / "WeChat Files",
        roaming / "Tencent" / "WeChat" / "WeChat Files" / "xwechat_files",
        roaming / "Tencent" / "WeChat" / "WeChat Files",
    ]
    custom = os.environ.get("WECHAT_DATA_DIR", "").strip()
    if custom:
        roots.insert(0, Path(custom).expanduser())
    return roots


def default_windows_root() -> Path | None:
    candidates: list[Path] = []
    for root in windows_roots():
        if not root.is_dir():
            continue
        if (root / "db_storage").is_dir():
            candidates.append(root)
        try:
            for child in root.iterdir():
                if child.is_dir() and (child / "db_storage").is_dir():
                    candidates.append(child)
        except OSError:
            pass
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[0] if len(unique) == 1 else None


if os.name == "nt":
    core.default_wechat_root = default_windows_root

if __name__ == "__main__":
    raise SystemExit(core.main())
