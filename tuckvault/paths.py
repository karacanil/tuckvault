"""Where Tuckvault keeps its own files on this PC."""

import os
import re
import sys
from pathlib import Path


def app_dir() -> Path:
    override = os.environ.get("TUCKVAULT_HOME")
    if override:
        d = Path(override)
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "Tuckvault"
        _adopt(d.with_name("Safekeep"), d)
    else:
        d = Path.home() / ".config" / "tuckvault"
        _adopt(d.with_name("safekeep"), d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _adopt(old: Path, new: Path) -> None:
    """Take over the settings, key and history from before the program was renamed (1.2 and older)."""
    if new.exists() or not old.is_dir():
        return
    try:
        old.rename(new)
    except OSError:
        return  # in use or not allowed: start fresh rather than fail; the old folder stays untouched
    try:
        (new / "safekeep.log").rename(new / "tuckvault.log")
    except OSError:
        pass


def config_file() -> Path:
    return app_dir() / "config.json"


def state_file() -> Path:
    return app_dir() / "state.json"


def cache_file() -> Path:
    return app_dir() / "cache.json"


def key_file() -> Path:
    return app_dir() / "key.bin"


def log_file() -> Path:
    return app_dir() / "tuckvault.log"


def lock_file() -> Path:
    return app_dir() / "run.lock"


def snapshot_cache(repo_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", repo_id or ""):  # it becomes a folder name
        raise ValueError("bad repository id")
    d = app_dir() / "snapshots" / repo_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_frozen() -> bool:
    """True in the packaged Windows build (Tuckvault.exe), False when run from source."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def trusted_exe(name: str) -> str:
    """Full path of a system program, so a look-alike earlier on PATH is never run.

    `name` is given without ".exe". Returns the bare name if it is not found
    in the system folders; callers decide whether that is acceptable.
    """
    if sys.platform == "win32":
        return system_exe(name + ".exe")
    for folder in ("/usr/bin", "/bin"):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return name


def system_exe(name: str) -> str:
    """Full path of a Windows system program, so a look-alike earlier on PATH is never run."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    for sub in ("System32", r"System32\WindowsPowerShell\v1.0", r"System32\OpenSSH"):
        candidate = os.path.join(root, sub, name)
        if os.path.isfile(candidate):
            return candidate
    return name


def icon_file() -> Path:
    return Path(__file__).resolve().parent / "assets" / "tuckvault.ico"


def icon_png() -> Path:
    return Path(__file__).resolve().parent / "assets" / "tuckvault.png"
