"""Automatic runs through Windows Task Scheduler.

Nothing of Safekeep stays running between backups: Windows starts
`pythonw -m safekeep run --scheduled --only <group>` at the chosen times,
and starts it as soon as possible afterwards if the PC was off at that
moment. There is one task for the items on the general schedule and one
for each item that has a schedule of its own.
"""

import csv
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, paths
from .config import WEEKDAYS

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_DAY_XML = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
            "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_EPOCH = datetime(2025, 1, 1)


def task_name() -> str:
    return os.environ.get("SAFEKEEP_TASK_NAME", r"Safekeep\Backup")


def task_for(group_id: str) -> str:
    """Task Scheduler name for one group from config.groups()."""
    return task_name() if group_id == config.DEFAULT_GROUP else f"{task_name()}-{group_id}"


def _hm(schedule: dict):
    try:
        h, m = str(schedule.get("time", "20:00")).split(":")
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except ValueError:
        return 20, 0


def _interval(schedule: dict) -> int:
    return max(1, min(24, int(schedule.get("interval_hours") or 6)))


def _monthday(schedule: dict) -> int:
    return max(1, min(28, int(schedule.get("monthday") or 1)))


def next_run(schedule: dict, now: datetime = None):
    """When the next automatic backup is due (local time), or None if off."""
    if not schedule.get("enabled"):
        return None
    now = now or datetime.now()
    h, m = _hm(schedule)
    freq = schedule.get("frequency", "weekly")
    if freq == "hourly":
        step = timedelta(hours=_interval(schedule))
        return _EPOCH + step * (int((now - _EPOCH) / step) + 1)
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if freq == "daily":
        return today if today > now else today + timedelta(days=1)
    if freq == "weekly":
        target = WEEKDAYS.index(schedule.get("weekday", "SUN"))
        cand = today + timedelta(days=(target - now.weekday()) % 7)
        return cand if cand > now else cand + timedelta(days=7)
    day = _monthday(schedule)
    cand = today.replace(day=day)
    if cand > now:
        return cand
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return cand.replace(year=year, month=month)


def describe(schedule: dict) -> str:
    if not schedule.get("enabled"):
        return "Automatic backups are off"
    h, m = _hm(schedule)
    at = f"{h:02d}:{m:02d}"
    freq = schedule.get("frequency", "weekly")
    if freq == "hourly":
        n = _interval(schedule)
        return "Every hour" if n == 1 else f"Every {n} hours"
    if freq == "daily":
        return f"Every day at {at}"
    if freq == "weekly":
        return f"Every {_DAY_XML[schedule.get('weekday', 'SUN')]} at {at}"
    return f"On day {_monthday(schedule)} of every month at {at}"


def _trigger_xml(schedule: dict) -> str:
    h, m = _hm(schedule)
    start = f"2025-01-01T{h:02d}:{m:02d}:00"
    freq = schedule.get("frequency", "weekly")
    if freq == "hourly":
        return (f"<TimeTrigger><Repetition><Interval>PT{_interval(schedule)}H</Interval>"
                "<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
                "<StartBoundary>2025-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
    if freq == "daily":
        body = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    elif freq == "weekly":
        day = _DAY_XML[schedule.get("weekday", "SUN")]
        body = (f"<ScheduleByWeek><DaysOfWeek><{day} /></DaysOfWeek>"
                "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    else:
        months = "".join(f"<{mo} />" for mo in _MONTHS)
        body = (f"<ScheduleByMonth><DaysOfMonth><Day>{_monthday(schedule)}</Day></DaysOfMonth>"
                f"<Months>{months}</Months></ScheduleByMonth>")
    return (f"<CalendarTrigger><StartBoundary>{start}</StartBoundary>"
            f"<Enabled>true</Enabled>{body}</CalendarTrigger>")


def _runner():
    """(command, arguments, working directory) for the scheduled run."""
    exe = Path(sys.executable)
    if paths.is_frozen():  # the installed app: Safekeep.exe itself, which has no console window
        gui_exe = exe.with_name("Safekeep.exe")
        return str(gui_exe if gui_exe.exists() else exe), "run --scheduled", str(paths.project_root())
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe), "-m safekeep run --scheduled", str(paths.project_root())


def task_xml(schedule: dict, group_id: str = config.DEFAULT_GROUP) -> str:
    command, arguments, workdir = _runner()
    arguments += " --only " + group_id
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    user_id = f"{domain}\\{user}" if domain else user
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Safekeep automatic backup. Managed by the Safekeep app.</Description>
  </RegistrationInfo>
  <Triggers>{_trigger_xml(schedule)}</Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user_id)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args):
    return subprocess.run([paths.system_exe("schtasks.exe"), *args], capture_output=True, text=True,
                          errors="replace", creationflags=_NO_WINDOW)


def installed() -> set:
    """Names of the Safekeep backup tasks Windows currently has."""
    if sys.platform != "win32":
        return set()
    res = _schtasks("/Query", "/FO", "CSV", "/NH")
    base, found = "\\" + task_name(), set()
    for row in csv.reader(res.stdout.splitlines()):
        if row and (row[0] == base or row[0].startswith(base + "-")):
            found.add(row[0][1:])
    return found


def is_active(cfg: dict) -> bool:
    """True if every schedule in the settings is registered with Windows."""
    wanted = {task_for(g["id"]) for g in config.active_groups(cfg)}
    return bool(wanted) and wanted <= installed()


def next_due(cfg: dict, now: datetime = None):
    """The earliest upcoming automatic backup over all schedules, or None."""
    times = [next_run(g["schedule"], now) for g in config.active_groups(cfg)]
    return min(times) if times else None


def _delete(name: str) -> None:
    res = _schtasks("/Delete", "/TN", name, "/F")
    if res.returncode != 0:
        raise OSError("Windows refused to remove the scheduled backup: " + res.stderr.strip())


def remove() -> None:
    for name in installed():
        _delete(name)


def refresh(cfg: dict) -> None:
    """Re-register existing tasks, so they follow the app if its folder moved or it was updated."""
    try:
        if installed():
            apply(cfg)
    except OSError:
        pass


def apply(cfg: dict) -> None:
    """Make Windows Task Scheduler match the schedules in the settings."""
    if sys.platform != "win32":
        raise OSError("Automatic scheduling is only implemented for Windows.")
    wanted = {task_for(g["id"]): g for g in config.active_groups(cfg)}
    for name in installed() - set(wanted):
        _delete(name)
    for name, group in wanted.items():
        fd, tmp = tempfile.mkstemp(suffix=".xml")
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as f:
                f.write(task_xml(group["schedule"], group["id"]))
            res = _schtasks("/Create", "/TN", name, "/XML", tmp, "/F")
        finally:
            os.remove(tmp)
        if res.returncode != 0:
            raise OSError("Windows refused to schedule the backup: " + (res.stderr or res.stdout).strip())
