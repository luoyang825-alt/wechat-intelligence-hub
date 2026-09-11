from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any


class ReaderClientError(RuntimeError):
    pass


def _coerce_command(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(part) for part in value if str(part).strip()]
    if isinstance(value, str) and value.strip():
        return shlex.split(value, posix=os.name != "nt")
    return []


def auto_reader_command() -> list[str]:
    configured = os.environ.get("WECHAT_READER_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return [sys.executable, str(path)] if path.suffix.casefold() == ".py" else [configured]
    package_file = Path(__file__).resolve()
    projects_dir = package_file.parents[2] if len(package_file.parents) >= 3 else package_file.parent
    reader_dir = projects_dir / "rion-wechat-reader"
    candidates = []
    if os.name == "nt":
        candidates.extend([reader_dir / "rion_wechat_reader_windows.py", reader_dir / "rion_wechat_reader.py"])
    else:
        candidates.append(reader_dir / "rion_wechat_reader.py")
    for candidate in candidates:
        if candidate.is_file():
            return [sys.executable, str(candidate)]
    installed = shutil.which("rion-wechat-cli") or shutil.which("rion-wechat-cli.exe")
    if installed:
        return [installed]
    raise ReaderClientError("找不到 Rion WeChat Reader；请在 reminders.json 配置 reader_command")


class ReaderClient:
    def __init__(self, config: dict[str, Any]):
        command = _coerce_command(config.get("reader_command"))
        self.command = command or auto_reader_command()
        self.reader_config = str(config.get("reader_config") or "").strip()
        self.timeout = max(5.0, float(config.get("reader_timeout_seconds") or 25))

    def _base(self) -> list[str]:
        command = list(self.command)
        if self.reader_config:
            command.extend(["--config", str(Path(self.reader_config).expanduser())])
        return command

    def run(self, args: list[str], *, timeout: float | None = None) -> dict[str, Any]:
        command = [*self._base(), *args]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReaderClientError(f"Reader 启动失败：{type(exc).__name__}") from exc
        output = (proc.stdout or "").strip()
        if not output:
            detail = (proc.stderr or "").strip()[:240]
            raise ReaderClientError(f"Reader 没有返回 JSON：{detail}")
        # JSONL/follow is intentionally not used by the worker; each call returns one JSON object.
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ReaderClientError("Reader 返回了无法解析的 JSON") from exc
        if not isinstance(payload, dict):
            raise ReaderClientError("Reader 响应顶层不是 JSON 对象")
        if proc.returncode != 0 or payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = str(error.get("code") or "reader_error")
            message = str(error.get("message") or "Reader 调用失败")
            raise ReaderClientError(f"{code}: {message[:220]}")
        return payload

    @staticmethod
    def _data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def status(self) -> dict[str, Any]:
        data = self._data(self.run(["status"]))
        status = data.get("status") if isinstance(data.get("status"), dict) else data
        return status if isinstance(status, dict) else {}

    def sessions(self, limit: int = 150) -> list[dict[str, Any]]:
        data = self._data(self.run(["sessions", "--limit", str(max(1, limit))]))
        rows = data.get("sessions")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def timeline(self, chat: str, *, since: str = "", limit: int = 200) -> list[dict[str, Any]]:
        args = ["timeline", chat, "--limit", str(max(1, limit)), "--display-order", "asc"]
        if since:
            args.extend(["--since", since])
        data = self._data(self.run(args))
        rows = data.get("messages")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def tail(self, chat: str, *, cursor: int, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
        data = self._data(
            self.run(["tail", chat, "--cursor", str(max(0, cursor)), "--limit", str(max(1, limit))])
        )
        rows = data.get("events")
        events = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        try:
            next_cursor = int(data.get("cursor") if data.get("cursor") is not None else cursor)
        except (TypeError, ValueError):
            next_cursor = cursor
        return events, next_cursor

    def context(self, chat: str, *, local_id: int, before: int = 4, after: int = 1) -> list[dict[str, Any]]:
        data = self._data(
            self.run(
                [
                    "context",
                    chat,
                    "--local-id",
                    str(local_id),
                    "--before-count",
                    str(max(0, before)),
                    "--after-count",
                    str(max(0, after)),
                ]
            )
        )
        rows = data.get("messages")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def access_plan(self, database_root: str = "") -> dict[str, Any]:
        args = ["access-plan"]
        if database_root:
            args.extend(["--database-root", database_root])
        return self._data(self.run(args))
