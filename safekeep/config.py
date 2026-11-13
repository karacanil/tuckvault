"""Settings, run history and small JSON helpers."""

import copy
import hashlib
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
    # path -> its own schedule (same shape as "schedule"). Items not listed
    # here follow the general schedule above.
    "source_schedules": {},
    "keep_last": 30,
    "verify_every_days": 7,
    "recovery_key_saved": False,
}
DEFAULT_GROUP = "default"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def source_id(path: str) -> str:
    """Short stable name for one backed-up item, safe in a task name."""
    return hashlib.sha1(_norm(path).encode("utf-8")).hexdigest()[:10]


def own_schedule(cfg: dict, path: str):
    """The item's own schedule, or None if it follows the general one."""
    want = _norm(path)
    for p, sched in (cfg.get("source_schedules") or {}).items():
        if _norm(p) == want and isinstance(sched, dict):
            return dict(DEFAULTS["schedule"], **sched)
    return None


def groups(cfg: dict) -> list:
    """What gets backed up together: [{id, schedule, sources}].

    The first group holds every item on the general schedule; each item
    with its own schedule is a group of its own.
    """
    general, out = [], []
    for src in cfg.get("sources", []):
        if not src:
            continue
        own = own_schedule(cfg, src)
        if own is None:
            general.append(src)
        else:
            out.append({"id": source_id(src), "schedule": own, "sources": [src]})
    return [{"id": DEFAULT_GROUP, "schedule": cfg["schedule"], "sources": general}] + out


def active_groups(cfg: dict) -> list:
    """Groups that should run automatically."""
    return [g for g in groups(cfg) if g["sources"] and g["schedule"].get("enabled")]


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
    listed = {_norm(s) for s in cfg.get("sources", []) if s}
    cfg["source_schedules"] = {p: s for p, s in (cfg.get("source_schedules") or {}).items()
                               if _norm(p) in listed}
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
