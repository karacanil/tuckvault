"""Automatic runs through Windows Task Scheduler.

Nothing of Safekeep stays running between backups: Windows starts
`pythonw -m safekeep run --scheduled` at the chosen times, and starts it
as soon as possible afterwards if the PC was off at that moment.
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from . import paths
from .config import WEEKDAYS

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_DAY_XML = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
            "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_EPOCH = datetime(2025, 1, 1)


def task_name() -> str:
    return os.environ.get("SAFEKEEP_TASK_NAME", r"Safekeep\Backup")


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
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe), "-m safekeep run --scheduled", str(paths.project_root())


def task_xml(schedule: dict) -> str:
    command, arguments, workdir = _runner()
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
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          errors="replace", creationflags=_NO_WINDOW)


def is_installed() -> bool:
    if sys.platform != "win32":
        return False
    return _schtasks("/Query", "/TN", task_name()).returncode == 0


def remove() -> None:
    if is_installed():
        res = _schtasks("/Delete", "/TN", task_name(), "/F")
        if res.returncode != 0:
            raise OSError("Windows refused to remove the scheduled backup: " + res.stderr.strip())


def apply(schedule: dict) -> None:
    """Make Windows Task Scheduler match the schedule in the settings."""
    if sys.platform != "win32":
        raise OSError("Automatic scheduling is only implemented for Windows.")
    if not schedule.get("enabled"):
        remove()
        return
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(task_xml(schedule))
        res = _schtasks("/Create", "/TN", task_name(), "/XML", tmp, "/F")
    finally:
        os.remove(tmp)
    if res.returncode != 0:
        raise OSError("Windows refused to schedule the backup: " + (res.stderr or res.stdout).strip())
