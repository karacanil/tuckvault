"""Command line entry point. With no arguments the window opens."""

import argparse
import sys

from . import __version__


def _out(text=""):
    if sys.stdout:  # pythonw has no console
        print(text)


def _print_result(result: dict) -> int:
    _out(f"{result['kind']}: {result['status']}")
    for key in ("snapshot", "files", "bytes", "uploaded_bytes", "error"):
        if result.get(key) not in (None, ""):
            _out(f"  {key}: {result[key]}")
    for w in result.get("warnings", []):
        _out(f"  warning: {w}")
    return 0 if result["status"] in ("success", "skipped") else (2 if result["status"] == "warning" else 1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tuckvault", description="Encrypted, verified backups over SSH.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the window (default)")
    run = sub.add_parser("run", help="back up now")
    run.add_argument("--scheduled", action="store_true",
                     help="unattended run: retry the connection and notify on failure")
    run.add_argument("--only", metavar="GROUP",
                     help="back up only the items on one schedule (used by the scheduled tasks)")
    sub.add_parser("verify", help="re-check every stored piece of every backup")
    sub.add_parser("snapshots", help="list the backups at the destination")
    restore = sub.add_parser("restore", help="restore a backup into a folder")
    restore.add_argument("snapshot", help="snapshot name, or 'latest'")
    restore.add_argument("dest", help="folder to restore into")
    restore.add_argument("--only", action="append", metavar="PATH",
                         help="restore just this file or folder, named as in the backup (repeatable)")
    files = sub.add_parser("files", help="list the files in a backup")
    files.add_argument("snapshot", help="snapshot name, or 'latest'")
    sched = sub.add_parser("schedule", help="install or remove the automatic run")
    sched.add_argument("action", choices=["apply", "remove", "status"])
    key = sub.add_parser("key", help="show or import the recovery key")
    key.add_argument("action", choices=["show", "import"])
    key.add_argument("value", nargs="?")
    sub.add_parser("test", help="test the connection to the backup machine")
    sub.add_parser("watch", help="notify if automatic backups have stopped happening "
                                 "(started daily by Windows)")
    args = parser.parse_args(argv)

    if args.cmd in (None, "gui"):
        from .gui import main as gui_main
        gui_main()
        return 0

    from . import config, engine, keystore, notify, scheduler, watch
    if args.cmd == "watch" or (args.cmd == "run" and args.scheduled):
        if config.is_damaged():
            notify.notify("Tuckvault cannot read its settings",
                          "The settings file is damaged, so automatic backups are not running. "
                          "Open Tuckvault and choose what to back up again.")
            return 1
    cfg = config.load()

    if args.cmd == "watch":
        late = watch.overdue(cfg)
        if late:
            notify.notify("Backups have stopped", watch.describe(late)
                          + " Open Tuckvault to see what is wrong.")
        _out(watch.describe(late) or "Automatic backups are up to date.")
        return 1 if late else 0

    if args.cmd == "run":
        if args.scheduled and args.only is None:
            args.only = config.DEFAULT_GROUP  # tasks made by 1.0 carry no group
        # Two schedules can fall on the same minute: unattended runs queue up.
        result = engine.run_backup(cfg, attempts=3 if args.scheduled else 1, only=args.only,
                                   wait=2 * 3600 if args.scheduled else 0)
        if args.scheduled:
            if result["status"] == "skipped" and "already running" in result.get("error", ""):
                result = dict(result, status="failed", error="A scheduled backup did not run "
                              "because another Tuckvault operation was still busy after two hours.")
            notify.notify_result(result)
        return _print_result(result)
    if args.cmd == "verify":
        return _print_result(engine.run_verify(cfg))
    if args.cmd in ("restore", "files"):
        name = args.snapshot
        try:
            if name == "latest":
                snaps = [s for s in engine.list_snapshots(cfg) if "error" not in s]
                if not snaps:
                    _out("no backups found")
                    return 1
                name = snaps[0]["name"]
        except engine.BackupError as e:
            _out(str(e))
            return 1
        if args.cmd == "files":
            try:
                for f in engine.snapshot_files(name, cfg):
                    _out(f"{f['s']:>14}  {f['p']}")
            except engine.BackupError as e:
                _out(str(e))
                return 1
            return 0
        return _print_result(engine.run_restore(name, args.dest, cfg, only=args.only))
    if args.cmd == "snapshots":
        try:
            for s in engine.list_snapshots(cfg):
                if "error" in s:
                    _out(f"{s['name']}  UNREADABLE: {s['error']}")
                else:
                    _out(f"{s['name']}  {s['created']}  {s['files']} files  {s['bytes']} bytes")
        except engine.BackupError as e:
            _out(str(e))
            return 1
        return 0
    if args.cmd == "schedule":
        if args.action == "apply":
            scheduler.apply(cfg)
        elif args.action == "remove":
            scheduler.remove()
        installed = scheduler.installed()
        for g in config.groups(cfg):
            if g["sources"]:
                state = "installed" if scheduler.task_for(g["id"]) in installed else "not installed"
                _out(f"{scheduler.describe(g['schedule'])} ({state}): {'; '.join(g['sources'])}")
        return 0
    if args.cmd == "key":
        if args.action == "show":
            _out(keystore.recovery_key() or "This PC has no backup key yet.")
            return 0
        if not args.value:
            _out("usage: tuckvault key import <recovery key>")
            return 1
        try:
            keystore.import_recovery_key(args.value)
        except ValueError as e:
            _out(str(e))
            return 1
        _out("Key imported.")
        return 0
    if args.cmd == "test":
        try:
            _out(engine.test_connection(cfg))
            return 0
        except engine.BackupError as e:
            _out(str(e))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
