"""The `tuckvault serve` and `tuckvault phone ...` commands."""

import getpass
import os
import sys
import time

from . import crypto, engine, scheduler, server


def status_lines(cfg: dict) -> list:
    """A few plain sentences on whether phones can back up to this computer right now."""
    lines = []
    try:
        root = server.phones_root(cfg)
    except server.ServeError as e:
        return [str(e)]
    running = server.is_running()
    lines.append("Accepting backups from phones: " + ("yes" if running else "no")
                 + (" (starts by itself when you sign in)" if scheduler.phones_enabled() else ""))
    lines.append("Phone backups are stored in: " + server.describe_root(root))
    addresses = server.phone_addresses()
    if addresses:
        lines.append("Address to enter in the phone app: " + addresses[0])
        for extra in addresses[1:]:
            lines.append("  or: " + extra)
    else:
        lines.append("Tailscale is not running on this computer, so phones cannot reach it. "
                     "Start Tailscale and sign in.")
    return lines


def restore(cfg: dict, dest: str, recovery_key: str, snapshot: str = "latest") -> dict:
    """Restore a phone's backup into a folder on this computer, with that phone's recovery key."""
    master = crypto.decode_recovery_key(recovery_key)  # raises ValueError with a readable message
    vault = crypto.Keys(master).check[:24]
    phone_cfg = dict(cfg, destination=server.vault_destination(server.phones_root(cfg), vault), _key=master)
    if snapshot == "latest":
        # Right after a backup the phone service still holds these backups for a few seconds.
        for attempt in range(15):
            try:
                found = engine.list_snapshots(phone_cfg)
                break
            except engine.BackupError as e:
                if "in use by another backup" not in str(e) or attempt == 14:
                    raise
                time.sleep(3)
        snaps = [s for s in found if "error" not in s]
        if not snaps:
            raise engine.BackupError("There are no readable backups from that phone.")
        snapshot = snaps[0]["name"]
    return engine.run_restore(snapshot, dest, phone_cfg)


def command(args, cfg: dict, out) -> int:
    try:
        return _command(args, cfg, out)
    except (server.ServeError, engine.BackupError, OSError, ValueError) as e:
        out(str(e))
        return 1


def _command(args, cfg, out) -> int:
    if args.cmd == "serve":
        engine.setup_logging()
        service = server.Service(cfg, listen=args.listen, port_number=args.port).start()
        out("Accepting backups from phones on port %d. Stored in: %s"
            % (service.port, server.describe_root(service.root)))
        for line in status_lines(cfg)[2:]:
            out(line)
        out("Pair a phone with:  tuckvault phone pair")
        service.wait()
        return 0

    action = args.action
    if action == "pair":
        server.phones_root(cfg)  # fail early if there is nowhere to store
        if not server.is_running():
            out("Tuckvault is not accepting phone backups yet. Start it first:")
            out("  tuckvault phone enable     (keeps running, also after a restart)")
            out("  tuckvault serve            (only while this window stays open)")
            return 1
        out("In the Tuckvault app on your phone, choose Connect and enter:")
        addresses = server.phone_addresses()
        out("  Address: " + (addresses[0] if addresses else
                             "(Tailscale is not running on this computer; start it first)"))
        out("  Code:    " + server.new_pairing_code())
        out("The code works once and for %d minutes." % server.PAIR_MINUTES)
        return 0
    if action == "list":
        devices = server.load()["devices"]
        if not devices:
            out("No phone is paired with this computer.")
        for device, info in sorted(devices.items()):
            out("%s  %s  (%s, paired %s)" % (device, info.get("name"), info.get("platform") or "?",
                                             info.get("paired")))
        return 0
    if action == "vaults":
        vaults = server.load().get("vaults") or {}
        if not vaults:
            out("No phone has backed up to this computer yet.")
        for vault, info in sorted(vaults.items()):
            out("%s  first used by %s on %s" % (vault, info.get("name") or "?", info.get("first_used")))
        return 0
    if action == "remove":
        if not args.value or not server.remove_device(args.value):
            out("No such phone. See: tuckvault phone list")
            return 1
        out("Removed. That phone can no longer reach its backups here; the backups themselves are kept.")
        return 0
    if action == "enable":
        server.phones_root(cfg)
        scheduler.phones_enable()
        out("Tuckvault now accepts backups from phones, and will after every sign-in.")
        for line in status_lines(cfg)[1:]:
            out(line)
        return 0
    if action == "disable":
        scheduler.phones_disable()
        out("Tuckvault no longer accepts backups from phones. Existing phone backups are kept.")
        return 0
    if action == "status":
        for line in status_lines(cfg):
            out(line)
        return 0
    if action == "restore":
        if not args.value:
            out("usage: tuckvault phone restore <folder to restore into>")
            return 1
        key = os.environ.get("TUCKVAULT_PHONE_KEY")
        if not key:
            if not sys.stdin or not sys.stdin.isatty():
                out("Run this in a terminal: it asks for the phone's recovery key.")
                return 1
            key = getpass.getpass("Recovery key shown in the phone app (not displayed while typing): ")
        result = restore(cfg, args.value, key, args.snapshot)
        out("%s: %s" % (result["kind"], result["status"]))
        for k in ("snapshot", "files", "bytes", "error"):
            if result.get(k) not in (None, ""):
                out("  %s: %s" % (k, result[k]))
        for w in result.get("warnings", []):
            out("  warning: " + w)
        return 0 if result["status"] == "success" else 1
    return 1
