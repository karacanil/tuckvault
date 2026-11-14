"""Tell the user when an unattended backup went wrong."""

import base64
import os
import subprocess
import sys
from xml.sax.saxutils import escape

from . import paths

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# Toasts must be sent on behalf of an app Windows already knows; Windows
# PowerShell is always registered, so the toast is attributed to it.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:TUCKVAULT_TOAST_XML)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:TUCKVAULT_TOAST_APP).Show($toast)
"""


def _toast(title: str, body: str) -> bool:
    xml = ('<toast scenario="reminder"><visual><binding template="ToastGeneric">'
           f"<text>{escape(title)}</text><text>{escape(body)}</text>"
           '<text placement="attribution">Tuckvault</text></binding></visual>'
           '<actions><action content="Dismiss" arguments="dismiss" activationType="system"/></actions>'
           "</toast>")
    env = dict(os.environ, TUCKVAULT_TOAST_XML=xml, TUCKVAULT_TOAST_APP=_APP_ID)
    encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        res = subprocess.run(
            [paths.trusted_exe("powershell"), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            env=env, capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _message_box(title: str, body: str) -> None:
    import ctypes
    # MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
    ctypes.windll.user32.MessageBoxW(None, body, title, 0x30 | 0x10000 | 0x40000)


def _notify_send(title: str, body: str) -> bool:
    """A desktop notification on Linux, through the standard notify-send."""
    exe = paths.trusted_exe("notify-send")
    if not os.path.isabs(exe):
        return False
    try:
        res = subprocess.run([exe, "-a", "Tuckvault", "-i", str(paths.icon_png()), "--", title, body],
                             capture_output=True, timeout=30)
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify(title: str, body: str) -> None:
    """Show a desktop notification; on Windows fall back to a message box if toasts fail."""
    # control characters would make Windows reject the notification
    body = "".join(c if c.isprintable() else " " for c in body)
    body = body if len(body) <= 300 else body[:297] + "…"
    if sys.platform != "win32":
        if not _notify_send(title, body):
            print(f"{title}: {body}", file=sys.stderr)
        return
    if not _toast(title, body):
        _message_box(title, body)


def notify_result(result: dict) -> None:
    """Notify only when a run needs the user's attention."""
    status = result.get("status")
    if status == "failed":
        notify("Backup failed", result.get("error") or "Open Tuckvault to see what went wrong.")
    elif status == "warning":
        warnings = result.get("warnings") or []
        first = warnings[0] if warnings else ""
        more = f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else ""
        notify("Backup finished with problems", f"{first}{more}")
