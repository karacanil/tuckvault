"""Tells the user when a newer Safekeep has been released.

Only ever asks GitHub for the newest version number and shows a notice.
Nothing is downloaded or installed; the user follows the link themselves.
Can be switched off on the Security tab.
"""

import json
import re
import time
import urllib.request

from . import __version__, config

LATEST_URL = "https://api.github.com/repos/karacanil/safekeep/releases/latest"
DOWNLOAD_PAGE = "https://github.com/karacanil/safekeep/releases/latest"
_TAG = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
_DAY = 24 * 3600


def _numbers(version: str):
    m = _TAG.fullmatch(version or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _fetch_latest(timeout: float):
    request = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Safekeep-update-check"})
    # Fixed https address, small bounded read, parsed as JSON only.
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read(200_000)).get("tag_name")


def newer_version(timeout: float = 5, now: float = None):
    """The newest released version if it is newer than this one, else None.

    Asks at most once a day; in between, the last answer is reused. Any
    problem (offline, GitHub unreachable, odd reply) simply means no notice.
    """
    now = time.time() if now is None else now
    st = config.load_state()
    seen = st.get("update_check") or {}
    latest = seen.get("latest")
    if now - float(seen.get("at") or 0) >= _DAY:
        try:
            latest = _fetch_latest(timeout)
        except Exception:  # deliberately broad: a version check must never get in the way
            latest = None
        if not isinstance(latest, str) or _numbers(latest) is None:
            latest = None
        try:
            config.update_state(update_check={"at": now, "latest": latest})
        except OSError:
            pass
    mine, theirs = _numbers(__version__), _numbers(latest)
    if mine and theirs and theirs > mine:
        return ".".join(str(x) for x in theirs)
    return None
