#!/usr/bin/env python3
"""Windows launcher for the existing read-only Rion WeChat Reader.

It adds Windows data-root discovery and NTFS ACL permission checks while all
parsing/decryption/query logic remains in rion_wechat_reader.py. It never
acquires keys, hooks WeChat, or writes to WeChat databases.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
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


def _sddl(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-Acl -LiteralPath '{escaped}').Sddl",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def windows_safe_mode(path: Path) -> bool:
    """Equivalent of POSIX 0600 using Windows ACLs.

    Broad allow ACEs for Everyone, Authenticated Users or Builtin Users are
    rejected. The current user, SYSTEM and Administrators may retain access.
    """
    if not path.exists():
        return True
    sddl = _sddl(path)
    if not sddl:
        return False
    broad = r"(?:WD|AU|BU|S-1-1-0|S-1-5-11|S-1-5-32-545)"
    return re.search(rf"\(A;[^)]*;;;{broad}\)", sddl, re.I) is None


if os.name == "nt":
    core.default_wechat_root = default_windows_root
    core.safe_mode = windows_safe_mode

if __name__ == "__main__":
    raise SystemExit(core.main())
