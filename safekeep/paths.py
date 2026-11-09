"""Where Safekeep keeps its own files on this PC."""

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    override = os.environ.get("SAFEKEEP_HOME")
    if override:
        d = Path(override)
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "Safekeep"
    else:
        d = Path.home() / ".config" / "safekeep"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return app_dir() / "config.json"


def state_file() -> Path:
    return app_dir() / "state.json"


def cache_file() -> Path:
    return app_dir() / "cache.json"


def key_file() -> Path:
    return app_dir() / "key.bin"


def log_file() -> Path:
    return app_dir() / "safekeep.log"


def lock_file() -> Path:
    return app_dir() / "run.lock"


def snapshot_cache(repo_id: str) -> Path:
    d = app_dir() / "snapshots" / repo_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent
