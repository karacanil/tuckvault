"""Automatic runs through Windows Task Scheduler, or systemd timers on Linux.

Nothing of Tuckvault stays running between backups: the system starts
`tuckvault run --scheduled --only <group>` at the chosen times, and starts
it as soon as possible afterwards if the computer was off at that moment.
There is one task (on Linux, one timer) for the items on the general
schedule and one for each item that has a schedule of its own, plus a daily
check that backups are still happening.
"""

import csv
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, paths, watch
from .config import WEEKDAYS

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_DAY_XML = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
            "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_EPOCH = datetime(2025, 1, 1)
_LINUX = sys.platform.startswith("linux")
# Shown after the next backup time: systemd user timers stop when the user logs out.
RUNS_WHEN = " (while you are logged in)" if _LINUX else ""


def task_name() -> str:
    return os.environ.get("TUCKVAULT_TASK_NAME", r"Tuckvault\Backup")


LEGACY_TASKS = r"Safekeep\Backup"  # what the tasks were called before the program was renamed
WATCH = "watch"  # not a group: the daily check that backups are still happening


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
        if _LINUX:  # systemd counts the hours afresh each day, see _calendar
            later = [hh for hh in _hours(schedule) if hh > now.hour]
            day = now.replace(minute=0, second=0, microsecond=0)
            return day.replace(hour=later[0]) if later else day.replace(hour=0) + timedelta(days=1)
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


def _hours(schedule: dict) -> list:
    return list(range(0, 24, _interval(schedule)))


def _calendar(schedule: dict) -> str:
    """The systemd OnCalendar= expression for a schedule.

    Every N hours starts again at midnight each day (00, N, 2N, ...), so when
    N does not divide 24 the last gap of the day is shorter.
    """
    h, m = _hm(schedule)
    freq = schedule.get("frequency", "weekly")
    if freq == "hourly":
        return "*-*-* " + ",".join(f"{hh:02d}" for hh in _hours(schedule)) + ":00:00"
    if freq == "daily":
        return f"*-*-* {h:02d}:{m:02d}:00"
    if freq == "weekly":
        return f"{_DAY_XML[schedule.get('weekday', 'SUN')][:3]} *-*-* {h:02d}:{m:02d}:00"
    return f"*-*-{_monthday(schedule):02d} {h:02d}:{m:02d}:00"


def _runner():
    """(command, arguments, working directory) for the scheduled run."""
    command, prefix, workdir = _program()
    return command, prefix + "run --scheduled", workdir


def _program():
    """(command, argument prefix, working directory) to start Tuckvault without a console."""
    exe = Path(sys.executable)
    if paths.is_frozen():  # the installed app: Tuckvault.exe itself, which has no console window
        gui_exe = exe.with_name("Tuckvault.exe")
        return str(gui_exe if gui_exe.exists() else exe), "", str(paths.project_root())
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe), "-m tuckvault ", str(paths.project_root())


def watch_xml() -> str:
    """The daily check for backups that stopped happening.

    Deliberately not caught up after the PC was off: by the next noon the
    backups that were due have had their chance to run first.
    """
    command, prefix, workdir = _program()
    schedule = {"enabled": True, "frequency": "daily", "time": "12:00"}
    return _task_xml(schedule, command, prefix + "watch", workdir, catch_up=False,
                     description="Tuckvault check that automatic backups are still happening.")


def task_xml(schedule: dict, group_id: str = config.DEFAULT_GROUP) -> str:
    command, arguments, workdir = _runner()
    return _task_xml(schedule, command, arguments + " --only " + group_id, workdir)


def _task_xml(schedule, command, arguments, workdir, catch_up=True,
              description="Tuckvault automatic backup. Managed by the Tuckvault app.") -> str:
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    user_id = f"{domain}\\{user}" if domain else user
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
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
    <StartWhenAvailable>{"true" if catch_up else "false"}</StartWhenAvailable>
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


# -- Linux: systemd user timers ------------------------------------------------

def unit_for(task: str) -> str:
    """systemd unit name (without suffix) for a task name, e.g. tuckvault-backup-1a2b3c4d5e."""
    return re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "systemd" / "user"


def _sd_check(value: str) -> str:
    if any(c in value for c in "\n\r\0"):
        raise OSError("A path contains a line break, which systemd cannot start.")
    return value.replace("%", "%%")  # % starts a systemd specifier


def _sd_quote(value: str, exec_line: bool = True) -> str:
    """One argument for ExecStart= (or Environment=), quoted the way systemd unquotes it."""
    value = _sd_check(value).replace("\\", "\\\\").replace('"', '\\"')
    if exec_line:
        value = value.replace("$", "$$")  # ExecStart= expands $VARS, Environment= does not
    return f'"{value}"'


def _units(schedule, args, description, catch_up=True):
    """(service text, timer text) for one scheduled job."""
    command, prefix, workdir = _program()
    argv = [command] + prefix.split() + args
    env = ""
    if os.environ.get("TUCKVAULT_HOME"):  # a trial or test setup keeps using its own settings
        env = "Environment=" + _sd_quote("TUCKVAULT_HOME=" + os.environ["TUCKVAULT_HOME"], False) + "\n"
    service = ("[Unit]\n"
               f"Description={description}\n\n"
               "[Service]\n"
               "Type=oneshot\n"
               f"WorkingDirectory={_sd_check(workdir)}\n"
               f"{env}"
               "ExecStart=" + " ".join(_sd_quote(a) for a in argv) + "\n"
               "TimeoutStartSec=12h\n"
               "Nice=10\n"
               # "watch" answers 1 after raising its alarm; that is not a broken service
               + ("" if catch_up else "SuccessExitStatus=1\n"))
    timer = ("[Unit]\n"
             f"Description={description}\n\n"
             "[Timer]\n"
             f"OnCalendar={_calendar(schedule)}\n"
             f"Persistent={'true' if catch_up else 'false'}\n\n"
             "[Install]\n"
             "WantedBy=timers.target\n")
    return service, timer


def systemd_units(schedule: dict, group_id: str = config.DEFAULT_GROUP):
    return _units(schedule, ["run", "--scheduled", "--only", group_id],
                  "Tuckvault automatic backup. Managed by the Tuckvault app.")


def systemd_watch_units():
    schedule = {"enabled": True, "frequency": "daily", "time": "12:00"}
    return _units(schedule, ["watch"], "Tuckvault check that automatic backups are still happening.",
                  catch_up=False)


def _systemctl(*args):
    exe = paths.trusted_exe("systemctl")
    if not os.path.isabs(exe):
        raise OSError("Automatic backups need systemd, which was not found on this computer.")
    try:
        return subprocess.run([exe, "--user", *args], capture_output=True, text=True,
                              errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OSError(f"systemctl did not respond: {e}")


def _check_systemd() -> None:
    res = _systemctl("show-environment")
    if res.returncode != 0:
        raise OSError("Automatic backups need systemd user services, which are not running for "
                      "this user: " + (res.stderr or res.stdout).strip())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tuckvault-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _linux_installed() -> set:
    base = unit_for(task_name())
    try:
        names = os.listdir(unit_dir())
    except OSError:
        return set()
    return {n[:-6] for n in names
            if n.endswith(".timer") and (n[:-6] == base or n[:-6].startswith(base + "-"))}


def _linux_drop(units) -> None:
    units = sorted(units)
    if not units:
        return
    _systemctl("disable", "--now", *(u + ".timer" for u in units))
    for u in units:
        for suffix in (".timer", ".service"):
            try:
                os.remove(unit_dir() / (u + suffix))
            except FileNotFoundError:
                pass


def _linux_apply(cfg: dict) -> None:
    wanted = {unit_for(task_for(g["id"])): systemd_units(g["schedule"], g["id"])
              for g in config.active_groups(cfg)}
    if wanted:
        wanted[unit_for(task_for(WATCH))] = systemd_watch_units()
    _check_systemd()
    _linux_drop(_linux_installed() - set(wanted))
    for unit, (service, timer) in wanted.items():
        _write(unit_dir() / (unit + ".service"), service)
        _write(unit_dir() / (unit + ".timer"), timer)
    res = _systemctl("daemon-reload")
    if res.returncode == 0 and wanted:
        res = _systemctl("enable", "--now", *(u + ".timer" for u in sorted(wanted)))
    if res.returncode != 0:
        raise OSError("systemd refused to schedule the backup: " + (res.stderr or res.stdout).strip())
    watch.note_schedules(cfg)


# -- Windows: Task Scheduler ---------------------------------------------------

def _schtasks(*args):
    return subprocess.run([paths.system_exe("schtasks.exe"), *args], capture_output=True, text=True,
                          errors="replace", creationflags=_NO_WINDOW)


def installed() -> set:
    """Names of the Tuckvault backup tasks the system currently has (systemd unit names on Linux)."""
    if _LINUX:
        return _linux_installed()
    if sys.platform != "win32":
        return set()
    return _tasks_named(task_name())


def registered_name(group_id: str) -> str:
    """What installed() calls the task of one group."""
    return unit_for(task_for(group_id)) if _LINUX else task_for(group_id)


def _tasks_named(name: str) -> set:
    res = _schtasks("/Query", "/FO", "CSV", "/NH")
    base, found = "\\" + name, set()
    for row in csv.reader(res.stdout.splitlines()):
        if row and (row[0] == base or row[0].startswith(base + "-")):
            found.add(row[0][1:])
    return found


def _legacy() -> set:
    """Tasks left by a version from before the rename. They would start a program that is gone."""
    if sys.platform != "win32" or "TUCKVAULT_TASK_NAME" in os.environ or "TUCKVAULT_HOME" in os.environ:
        return set()  # tests and trial runs with their own settings leave the real tasks alone
    return _tasks_named(LEGACY_TASKS)


def is_active(cfg: dict) -> bool:
    """True if every schedule in the settings is registered with the system."""
    wanted = {registered_name(g["id"]) for g in config.active_groups(cfg)}
    return bool(wanted) and (wanted | {registered_name(WATCH)}) <= installed()


def next_due(cfg: dict, now: datetime = None):
    """The earliest upcoming automatic backup over all schedules, or None."""
    times = [next_run(g["schedule"], now) for g in config.active_groups(cfg)]
    return min(times) if times else None


def _delete(name: str) -> None:
    res = _schtasks("/Delete", "/TN", name, "/F")
    if res.returncode != 0:
        raise OSError("Windows refused to remove the scheduled backup: " + res.stderr.strip())


def remove() -> None:
    if _LINUX:
        _linux_drop(installed())
        _systemctl("daemon-reload")
        return
    for name in installed() | _legacy():
        _delete(name)


def refresh(cfg: dict) -> None:
    """Re-register existing tasks, so they follow the app if its folder moved or it was updated."""
    try:
        old = _legacy()
        for name in old:
            _delete(name)
        if old or installed():
            apply(cfg)
    except OSError:
        pass


def apply(cfg: dict) -> None:
    """Make Windows Task Scheduler (or systemd on Linux) match the schedules in the settings."""
    if _LINUX:
        _linux_apply(cfg)
        return
    if sys.platform != "win32":
        raise OSError("Automatic scheduling is only implemented for Windows and Linux.")
    wanted = {task_for(g["id"]): task_xml(g["schedule"], g["id"]) for g in config.active_groups(cfg)}
    if wanted:
        wanted[task_for(WATCH)] = watch_xml()
    for name in installed() - set(wanted):
        _delete(name)
    for name, xml in wanted.items():
        fd, tmp = tempfile.mkstemp(suffix=".xml")
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as f:
                f.write(xml)
            res = _schtasks("/Create", "/TN", name, "/XML", tmp, "/F")
        finally:
            os.remove(tmp)
        if res.returncode != 0:
            raise OSError("Windows refused to schedule the backup: " + (res.stderr or res.stdout).strip())
    watch.note_schedules(cfg)
