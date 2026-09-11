from __future__ import annotations

import json
import platform
import subprocess
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from xml.sax.saxutils import escape as xml_escape


class NotifyError(RuntimeError):
    pass


def _powershell() -> str:
    for name in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        from shutil import which
        found = which(name)
        if found:
            return found
    return ""


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def _windows_balloon(exe: str, title: str, body: str) -> None:
    title_e = _ps_escape(title[:120])
    body_e = _ps_escape(body[:300])
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;"
        "$n.Visible=$true;"
        f"$n.BalloonTipTitle='{title_e}';$n.BalloonTipText='{body_e}';"
        "$n.ShowBalloonTip(5000);Start-Sleep -Seconds 6;$n.Dispose()"
    )
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise NotifyError((proc.stderr or proc.stdout or "Windows notification failed")[:240])


def windows_toast(title: str, body: str, reminder_id: int, *, actions: bool = False, uri_scheme: str = "wechatreminder") -> None:
    exe = _powershell()
    if not exe:
        raise NotifyError("找不到 PowerShell，无法发送 Windows Toast")
    title_xml = xml_escape(title[:120])
    body_xml = xml_escape(body[:300])
    if actions and reminder_id:
        launch_ack = xml_escape(f"{uri_scheme}://ack?id={reminder_id}", {'"': '&quot;'})
        launch_done = xml_escape(f"{uri_scheme}://done?id={reminder_id}", {'"': '&quot;'})
        launch_snooze = xml_escape(f"{uri_scheme}://snooze?id={reminder_id}&minutes=30", {'"': '&quot;'})
        xml = (
            "<toast><visual><binding template=\"ToastGeneric\">"
            f"<text>{title_xml}</text><text>{body_xml}</text></binding></visual><actions>"
            f"<action content=\"已处理\" arguments=\"{launch_done}\" activationType=\"protocol\"/>"
            f"<action content=\"30分钟后\" arguments=\"{launch_snooze}\" activationType=\"protocol\"/>"
            f"<action content=\"已看到\" arguments=\"{launch_ack}\" activationType=\"protocol\"/>"
            "</actions></toast>"
        )
    else:
        xml = f"<toast><visual><binding template=\"ToastGeneric\"><text>{title_xml}</text><text>{body_xml}</text></binding></visual></toast>"
    xml_e = _ps_escape(xml)
    script = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null;"
        "$xml=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$xml.LoadXml('{xml_e}');"
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($xml);"
        "$notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('WeChat Intelligence Hub');"
        "$notifier.Show($toast)"
    )
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        # Some Windows desktop environments reject unpackaged WinRT toasts.
        # A balloon is less interactive but still preserves the core reminder.
        _windows_balloon(exe, title, body)


def macos_notification(title: str, body: str) -> None:
    script = f'display notification {json.dumps(body[:300])} with title {json.dumps(title[:120])}'
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=8, check=False)
    if proc.returncode != 0:
        raise NotifyError((proc.stderr or "macOS notification failed")[:240])


def linux_notification(title: str, body: str) -> None:
    from shutil import which
    exe = which("notify-send")
    if not exe:
        raise NotifyError("找不到 notify-send")
    proc = subprocess.run([exe, title[:120], body[:300]], capture_output=True, text=True, timeout=8, check=False)
    if proc.returncode != 0:
        raise NotifyError((proc.stderr or "notify-send failed")[:240])


def desktop_notify(item: dict[str, Any], config: dict[str, Any]) -> None:
    if not config.get("enabled", True):
        return
    title = str(item.get("title") or "微信重要信息")
    body = str(item.get("summary") or "")
    action = str(item.get("action") or "")
    if action:
        body = f"{body}\n下一步：{action}"
    system = platform.system()
    if system == "Windows":
        windows_toast(title, body, int(item.get("id") or 0), actions=bool(config.get("actions_enabled")), uri_scheme=str(config.get("action_uri_scheme") or "wechatreminder"))
    elif system == "Darwin":
        macos_notification(title, body)
    else:
        linux_notification(title, body)


def mobile_notify(item: dict[str, Any], config: dict[str, Any]) -> None:
    if not config.get("enabled"):
        return
    provider = str(config.get("provider") or "ntfy").casefold()
    if provider != "ntfy":
        raise NotifyError(f"不支持的手机通知 provider：{provider}")
    url = str(config.get("url") or "").strip()
    if not url:
        raise NotifyError("手机通知已启用但未配置 url")
    message = str(item.get("summary") or "")
    if config.get("include_chat_name", True) and item.get("source_chat"):
        message = f"{item.get('source_chat')}｜{message}"
    if item.get("action"):
        message += f"\n下一步：{item.get('action')}"
    payload: dict[str, Any] = {
        "title": str(item.get("title") or "微信重要信息"),
        "message": message,
        "priority": 5 if int(item.get("score") or 0) >= 88 else 4,
    }
    parsed = urlparse(url)
    if parsed.path in {"", "/"}:
        payload["topic"] = str(config.get("topic") or "wechat-reminders")
    token = str(config.get("token") or "").strip()
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=max(2, int(config.get("timeout_seconds") or 8))) as response:
            response.read(1024)
    except (URLError, HTTPError, TimeoutError, OSError) as exc:
        raise NotifyError(f"手机通知失败：{type(exc).__name__}") from exc


def notify(item: dict[str, Any], config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        desktop_notify(item, config.get("desktop") or {})
    except NotifyError as exc:
        errors.append(str(exc))
    try:
        mobile_notify(item, config.get("mobile") or {})
    except NotifyError as exc:
        errors.append(str(exc))
    return errors
