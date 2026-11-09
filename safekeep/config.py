"""Settings, run history and small JSON helpers."""

import copy
import json
import os
from pathlib import Path

from . import paths

WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
FREQUENCIES = ["hourly", "daily", "weekly", "monthly"]

DEFAULTS = {
    "sources": [],
    "excludes": ["Thumbs.db", "desktop.ini", "~$*", "*.tmp"],
    "destination": {"type": "ssh", "host": "laptop", "path": "safekeep-repo"},
    "schedule": {
        "enabled": True,
        "frequency": "weekly",
        "interval_hours": 6,
        "weekday": "SUN",
        "monthday": 1,
        "time": "20:00",
    },
    "keep_last": 30,
    "verify_every_days": 7,
    "recovery_key_saved": False,
}


def read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load() -> dict:
    return _merge(copy.deepcopy(DEFAULTS), read_json(paths.config_file(), {}))


def save(cfg: dict) -> None:
    write_json(paths.config_file(), cfg)


def load_state() -> dict:
    st = read_json(paths.state_file(), {})
    st.setdefault("history", [])
    return st


def record_run(result: dict) -> None:
    st = load_state()
    st["last_run"] = result
    if result.get("status") in ("success", "warning") and result.get("kind") == "backup":
        st["last_success"] = result.get("finished")
    st["history"] = ([result] + st["history"])[:100]
    write_json(paths.state_file(), st)


def update_state(**kv) -> None:
    st = load_state()
    st.update(kv)
    write_json(paths.state_file(), st)
