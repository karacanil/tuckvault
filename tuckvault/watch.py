"""Notices when automatic backups have quietly stopped happening.

A failed backup announces itself. A backup that never starts does not:
the scheduled task was disabled, the settings file got damaged, a run is
stuck. Once a day Windows starts `tuckvault watch`, which compares the last
successful backup of every automatically backed-up item with how often it
is supposed to run, and raises a notification when one is long overdue.
"""

import os
from datetime import datetime, timedelta, timezone

from . import config

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def allowed_gap(schedule: dict) -> timedelta:
    """How long an item may go without a successful backup before it counts as overdue.

    Twice its interval: one missed run is normal life (PC off, laptop
    asleep), two in a row is worth a word.
    """
    freq = schedule.get("frequency", "weekly")
    if freq == "hourly":
        hours = max(1, min(24, int(schedule.get("interval_hours") or 6)))
        return timedelta(hours=max(2 * hours, 6))
    return {"daily": timedelta(days=2), "weekly": timedelta(days=14)}.get(freq, timedelta(days=62))


def _parse(text):
    try:
        return datetime.strptime(text, _ISO).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def note_schedules(cfg: dict, now: datetime = None) -> None:
    """Remember since when each item is expected to be backed up automatically.

    Called whenever the schedules are applied. An item that is new on a
    schedule starts its clock now; one that left its schedule is forgotten,
    so switching automatic backups off and on again never raises a false alarm.
    """
    now = (now or datetime.now(timezone.utc)).strftime(_ISO)
    st = config.load_state()
    before = st.get("expected_since") or {}
    active = {_key(s) for g in config.active_groups(cfg) for s in g["sources"]}
    after = {k: before.get(k, now) for k in sorted(active)}
    if after != before:
        st["expected_since"] = after
        config.write_json(config.paths.state_file(), st)


def note_success(sources, finished: str) -> None:
    st = config.load_state()
    table = dict(st.get("last_success_by_source") or {})
    for s in sources or []:
        table[_key(s)] = finished
    st["last_success_by_source"] = table
    config.write_json(config.paths.state_file(), st)


def overdue(cfg: dict, now: datetime = None) -> list:
    """[{"path", "days", "never"}] for every automatically backed-up item that is overdue."""
    now = now or datetime.now(timezone.utc)
    st = config.load_state()
    since = st.get("expected_since") or {}
    last = st.get("last_success_by_source") or {}
    late = []
    for group in config.active_groups(cfg):
        gap = allowed_gap(group["schedule"])
        for src in group["sources"]:
            expected = _parse(since.get(_key(src)))
            if expected is None:
                continue  # the schedule was never registered with Windows
            done = _parse(last.get(_key(src)))
            reference = max(expected, done) if done else expected
            if now - reference > gap:
                late.append({"path": src, "days": (now - reference).days, "never": done is None})
    return late


def describe(late: list) -> str:
    if not late:
        return ""
    worst = max(late, key=lambda x: x["days"])
    name = os.path.basename(worst["path"].rstrip("\\/")) or worst["path"]
    what = name if len(late) == 1 else f"{len(late)} items (for example {name})"
    days = worst["days"]
    span = f"{days} days" if days >= 2 else "more than a day" if days == 1 else "longer than it should be"
    return f"No successful backup of {what} for {span}."
