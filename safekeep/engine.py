"""Backup, restore, verification and pruning."""

import fnmatch
import logging
import logging.handlers
import os
import platform
import secrets
import sys
import time
from datetime import datetime, timezone

from . import config, crypto, keystore, paths
from .remote import ConnectError, Remote, RemoteError

CHUNK = 4 * 1024 * 1024
VERIFY_BATCH = 200

log = logging.getLogger("safekeep")


class BackupError(Exception):
    """A problem worth showing to the user as-is."""


class AlreadyRunning(BackupError):
    pass


# -- small helpers --------------------------------------------------------
def setup_logging() -> None:
    if log.handlers:
        return
    handler = logging.handlers.RotatingFileHandler(
        paths.log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lp(path: str) -> str:
    """Windows long-path form, so deep folders do not fail at 260 characters."""
    if sys.platform != "win32" or path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _excluded(path: str, name: str, patterns) -> bool:
    low_name, low_path = name.lower(), path.lower().replace("\\", "/")
    for pat in patterns:
        pat = pat.lower().replace("\\", "/")
        if fnmatch.fnmatch(low_path if "/" in pat else low_name, pat):
            return True
    return False


class _Lock:
    """One Safekeep operation at a time on this PC. Released if we crash."""

    def __init__(self, wait: float = 0):
        self.wait = wait  # seconds to wait for a running operation to finish

    def __enter__(self):
        self.fh = open(paths.lock_file(), "a+")
        deadline = time.monotonic() + self.wait
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.fh.close()
                    raise AlreadyRunning("Another Safekeep backup or restore is already running.")
                time.sleep(min(5, max(0.1, self.wait / 20)))

    def __exit__(self, *exc):
        self.fh.close()


def _progress(cb, phase, done=0, total=0, text=""):
    if cb:
        try:
            cb(phase, done, total, text)
        except Exception:
            pass


# -- repository access ----------------------------------------------------
class Repo:
    """An open connection to the backup repository plus the keys for it."""

    def __init__(self, cfg: dict, create: bool = False):
        self.remote = Remote(cfg["destination"])
        self.remote.connect()
        try:
            info = self.remote.open(create=create)
            meta = info.get("meta")
            if meta is None:
                if not create:
                    raise BackupError("There are no backups at the destination yet.")
                master = keystore.load_or_create()
                self.keys = crypto.Keys(master)
                meta = {"format": 1, "id": secrets.token_hex(8), "created": now_iso(),
                        "key_check": self.keys.check}
                self.remote.call("init", meta=meta)
                log.info("created new repository %s", meta["id"])
            else:
                master = keystore.load()
                if master is None:
                    raise BackupError(
                        "The destination already holds backups, but this PC has no key for "
                        "them. Import your recovery key on the Security tab.")
                self.keys = crypto.Keys(master)
                if meta.get("key_check") != self.keys.check:
                    raise BackupError(
                        "The key on this PC does not match the backups at the destination. "
                        "Import the right recovery key, or choose an empty destination folder.")
            self.id = meta["id"]
            self._manifests = {}
        except BaseException:
            self.remote.close()
            raise

    def close(self):
        self.remote.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def snapshot_names(self):
        return [s["name"] for s in self.remote.list_snapshots()]

    def manifest(self, name: str) -> dict:
        """Decrypted manifest, fetched once and kept in a local cache."""
        if name in self._manifests:
            return self._manifests[name]
        cached = paths.snapshot_cache(self.id) / name
        manifest = None
        if cached.exists():
            try:
                manifest = self.keys.open_snapshot(cached.read_bytes())
            except crypto.IntegrityError:
                manifest = None
        if manifest is None:
            raw = self.remote.get_snapshot(name)
            manifest = self.keys.open_snapshot(raw)
            cached.write_bytes(raw)
        self._manifests[name] = manifest
        return manifest

    def referenced_blobs(self, names):
        """blob name -> set of snapshot names that need it."""
        refs = {}
        for snap in names:
            for f in self.manifest(snap)["files"]:
                for blob in f["c"]:
                    refs.setdefault(blob, set()).add(snap)
        return refs


def _connect(cfg, create, attempts=1, wait=20, progress=None) -> Repo:
    last = None
    for i in range(attempts):
        if i:
            _progress(progress, "connect", 0, 0, f"Backup machine not reachable, retry {i + 1}…")
            time.sleep(wait)
        try:
            return Repo(cfg, create=create)
        except ConnectError as e:
            last = e
            log.warning("connect attempt %d failed: %s", i + 1, e)
    raise BackupError(str(last))


# -- scanning -------------------------------------------------------------
def _scan(sources, excludes, warnings):
    """Return [(path, size, mtime_ns)] for every file under the sources."""
    files, seen = [], set()

    def add(path, st):
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            files.append((path, st.st_size, st.st_mtime_ns))

    for src in sources:
        src = os.path.abspath(src)
        try:
            st = os.stat(_lp(src))
        except OSError:
            warnings.append(f"Not found, skipped: {src}")
            continue
        if os.path.isfile(_lp(src)):
            add(src, st)
            continue
        stack = [src]
        while stack:
            folder = stack.pop()
            try:
                entries = sorted(os.scandir(_lp(folder)), key=lambda e: e.name.lower())
            except OSError as e:
                warnings.append(f"Could not open folder {folder}: {e.strerror or e}")
                continue
            for entry in entries:
                path = os.path.join(folder, entry.name)
                if _excluded(path, entry.name, excludes):
                    continue
                try:
                    if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                        continue  # links are not followed
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        add(path, entry.stat(follow_symlinks=False))
                except OSError as e:
                    warnings.append(f"Could not read {path}: {e.strerror or e}")
    files.sort(key=lambda f: f[0].lower())
    return files


# -- backup ---------------------------------------------------------------
def run_backup(cfg: dict = None, progress=None, attempts: int = 1,
               only: str = None, wait: float = 0) -> dict:
    """Run one backup. Never raises: the outcome is in the returned dict.

    only: id of one group from config.groups() to back up just those items;
    None backs up everything. wait: seconds to wait for another running
    Safekeep operation instead of skipping.
    """
    setup_logging()
    cfg = cfg or config.load()
    result = {"kind": "backup", "started": now_iso(), "status": "failed", "warnings": [],
              "files": 0, "bytes": 0, "uploaded_bytes": 0, "new_files": 0}
    sources = [s for s in cfg.get("sources", []) if s]
    if only is not None:
        sources = next((g["sources"] for g in config.groups(cfg) if g["id"] == only), [])
        if not sources:  # a leftover schedule for an item that is no longer listed
            log.info("scheduled group %s has nothing to back up", only)
            result.update(status="skipped", finished=now_iso(),
                          error="Nothing is on this schedule any more.")
            return result
    result["sources"] = sources
    log.info("backup starting (%s)", "everything" if only is None else "; ".join(sources))
    try:
        with _Lock(wait):
            _backup(cfg, sources, result, progress, attempts)
    except AlreadyRunning as e:
        result.update(status="skipped", error=str(e))
    except (BackupError, RemoteError, crypto.IntegrityError) as e:
        result["error"] = str(e)
    except Exception as e:
        log.exception("unexpected failure")
        result["error"] = f"Unexpected error: {type(e).__name__}: {e}"
    result["finished"] = now_iso()
    result["warnings"] = result["warnings"][:50]
    if result["status"] != "skipped":
        config.record_run(result)
    log.info("backup finished: %s %s", result["status"], result.get("error", ""))
    for w in result["warnings"]:
        log.warning("%s", w)
    return result


def _under(path: str, roots) -> bool:
    p = os.path.normcase(path)
    return any(p == r or p.startswith(r.rstrip("\\/") + os.sep) for r in roots)


def _backup(cfg, sources, result, progress, attempts):
    warnings = result["warnings"]
    if not sources:
        raise BackupError("Nothing is selected for backup yet.")

    _progress(progress, "scan", 0, 0, "Looking for files…")
    files = _scan(sources, cfg.get("excludes", []), warnings)
    if not files:
        raise BackupError("None of the selected files or folders could be read. "
                          + (warnings[0] if warnings else ""))
    total_bytes = sum(f[1] for f in files)

    _progress(progress, "connect", 0, 0, "Connecting to the backup machine…")
    with _connect(cfg, create=True, attempts=attempts, progress=progress) as repo:
        remote, keys = repo.remote, repo.keys
        blobs = remote.list_blobs()
        free = remote.call("stat")[0]["free"]

        cache = config.read_json(paths.cache_file(), {})
        if cache.get("repo") != repo.id:
            cache = {"repo": repo.id, "files": {}}
        # Keep what is known about items that are not part of this run.
        roots = [os.path.normcase(os.path.abspath(s)) for s in sources]
        old = cache["files"]
        new_cache = {p: v for p, v in old.items() if not _under(p, roots)}

        entries, done = [], 0
        for path, size, mtime in files:
            _progress(progress, "backup", done, total_bytes, path)
            hit = old.get(path)
            if hit and hit[0] == size and hit[1] == mtime and all(b in blobs for b in hit[2]):
                chunks = hit[2]
            else:
                if size > free:
                    raise BackupError("The backup machine is out of disk space.")
                try:
                    chunks, read = [], 0
                    with open(_lp(path), "rb") as f:
                        while True:
                            data = f.read(CHUNK)
                            if not data:
                                break
                            read += len(data)
                            name, blob = keys.seal_chunk(data)
                            if name not in blobs:
                                remote.put_blob(name, blob)
                                blobs[name] = len(blob)
                                free -= len(blob)
                                result["uploaded_bytes"] += len(blob)
                            chunks.append(name)
                            _progress(progress, "backup", done + read, total_bytes, path)
                    if read != size:
                        warnings.append(f"Changed while being backed up: {path}")
                        size = read
                    result["new_files"] += 1
                except OSError as e:
                    warnings.append(f"Could not read {path}: {e.strerror or e}")
                    done += size
                    continue
            new_cache[path] = [size, mtime, chunks]
            entries.append({"p": path, "s": size, "m": mtime, "c": chunks})
            done += size
            result["files"] += 1
            result["bytes"] += size

        if not entries:
            raise BackupError("No file could be read. " + (warnings[0] if warnings else ""))

        # Names sort chronologically; microseconds keep quick successive runs in order.
        snap_name = (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                     + "-" + secrets.token_hex(3) + ".snap")
        manifest = {"v": 1, "created": now_iso(), "host": platform.node(), "sources": sources,
                    "files": entries, "stats": {"files": result["files"], "bytes": result["bytes"]},
                    "warnings": warnings[:50]}
        raw = keys.seal_snapshot(manifest)
        remote.put_snapshot(snap_name, raw)
        (paths.snapshot_cache(repo.id) / snap_name).write_bytes(raw)
        result["snapshot"] = snap_name
        config.write_json(paths.cache_file(), {"repo": repo.id, "files": new_cache})

        # Everything this snapshot needs must really be on the other side.
        _progress(progress, "verify", 0, 0, "Checking the new backup…")
        wanted = sorted({b for e in entries for b in e["c"]})
        bad = _check(remote, wanted, full=False)
        if bad:
            raise BackupError(f"{len(bad)} pieces of the new backup are missing on the backup "
                              "machine. The backup is not complete.")

        problems = None
        if _scrub_due(cfg):
            problems = _scrub(repo, progress)
            result["scrubbed"] = True

        _prune(repo, int(cfg.get("keep_last") or 0), progress)

        if problems:
            raise BackupError(problems)
    result["status"] = "warning" if warnings else "success"


def _check(remote, names, full, progress=None, label="Verifying stored data…"):
    """Return the blob names that are missing or damaged."""
    bad = []
    for i in range(0, len(names), VERIFY_BATCH):
        _progress(progress, "verify", i, len(names), label)
        resp = remote.verify_blobs(names[i:i + VERIFY_BATCH], full=full)
        bad += resp["missing"] + resp["corrupt"]
    return bad


def _scrub_due(cfg) -> bool:
    days = cfg.get("verify_every_days")
    if not days:
        return False
    last = config.load_state().get("last_scrub")
    if not last:
        return True
    try:
        then = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - then).total_seconds() >= days * 86400 - 3600


def _scrub(repo: Repo, progress=None):
    """Re-hash every stored blob. Returns a problem description or None.

    Damaged blobs are deleted on purpose: they are useless, and removing
    them makes the next backup upload a fresh copy if the file still exists.
    """
    names = repo.snapshot_names()
    unreadable = []
    for n in names:
        try:
            repo.manifest(n)
        except crypto.IntegrityError:
            unreadable.append(n)
    good = [n for n in names if n not in unreadable]
    refs = repo.referenced_blobs(good)
    bad = _check(repo.remote, sorted(refs), full=True, progress=progress)
    config.update_state(last_scrub=now_iso())
    if bad:
        repo.remote.delete_blobs(bad)
        log.error("scrub found %d damaged/missing blobs: %s", len(bad), ", ".join(b[:12] for b in bad))
    log.info("scrub: %d blobs checked, %d bad, %d unreadable snapshots",
             len(refs), len(bad), len(unreadable))
    if not bad and not unreadable:
        return None
    hit = set()
    for b in bad:
        hit |= refs[b]
    parts = []
    if bad:
        parts.append(f"{len(bad)} stored pieces were damaged or missing on the backup machine, "
                     f"affecting {len(hit)} of {len(good)} backups. Run a backup now to re-send "
                     "the files that still exist on this PC.")
    if unreadable:
        parts.append(f"{len(unreadable)} backups could not be read at all (damaged index).")
    return " ".join(parts)


def _prune(repo: Repo, keep_last: int, progress=None):
    """Drop old snapshots, then delete data nothing refers to.

    The newest keep_last backups of every item are kept. Items can be on
    different schedules, so counting per item stops a frequently backed-up
    item from pushing out the backups of a rarely backed-up one.
    """
    names = sorted(repo.snapshot_names())
    if keep_last > 0:
        keep, count = set(), {}
        for pos, name in enumerate(reversed(names)):
            try:
                items = repo.manifest(name).get("sources") or [""]
            except crypto.IntegrityError:
                if pos < keep_last:  # unreadable ones only age out
                    keep.add(name)
                continue
            for item in items:
                key = os.path.normcase(item)
                if count.get(key, 0) < keep_last:
                    count[key] = count.get(key, 0) + 1
                    keep.add(name)
        for old in [n for n in names if n not in keep]:
            repo.remote.delete_snapshot(old)
            try:
                (paths.snapshot_cache(repo.id) / old).unlink()
            except OSError:
                pass
            log.info("pruned snapshot %s", old)
        names = [n for n in names if n in keep]
    try:
        refs = repo.referenced_blobs(names)
    except crypto.IntegrityError:
        log.warning("skipping cleanup: a snapshot could not be read")
        return
    unused = [b for b in repo.remote.list_blobs() if b not in refs]
    if unused:
        _progress(progress, "prune", 0, 0, "Removing data no backup needs any more…")
        repo.remote.delete_blobs(unused)
        log.info("removed %d unreferenced blobs", len(unused))


# -- verify ---------------------------------------------------------------
def run_verify(cfg: dict = None, progress=None) -> dict:
    """Full integrity check of everything stored. Never raises."""
    setup_logging()
    cfg = cfg or config.load()
    result = {"kind": "verify", "started": now_iso(), "status": "failed", "warnings": []}
    try:
        with _Lock():
            _progress(progress, "connect", 0, 0, "Connecting to the backup machine…")
            with _connect(cfg, create=False) as repo:
                problem = _scrub(repo, progress)
                result["snapshots"] = len(repo.snapshot_names())
                if problem:
                    result["error"] = problem
                else:
                    result["status"] = "success"
    except (BackupError, RemoteError, crypto.IntegrityError) as e:
        result["error"] = str(e)
    except Exception as e:
        log.exception("unexpected failure")
        result["error"] = f"Unexpected error: {type(e).__name__}: {e}"
    result["finished"] = now_iso()
    config.record_run(result)
    return result


# -- listing / restore ----------------------------------------------------
def list_snapshots(cfg: dict = None):
    """Newest first: [{name, created, files, bytes, warnings, error}]."""
    setup_logging()
    cfg = cfg or config.load()
    out = []
    with _connect(cfg, create=False) as repo:
        for name in sorted(repo.snapshot_names(), reverse=True):
            try:
                m = repo.manifest(name)
                out.append({"name": name, "created": m["created"], "files": m["stats"]["files"],
                            "bytes": m["stats"]["bytes"], "warnings": len(m.get("warnings", [])),
                            "sources": m.get("sources", [])})
            except crypto.IntegrityError as e:
                out.append({"name": name, "error": str(e)})
    return out


def _restore_target(dest: str, original: str) -> str:
    """Map an original absolute path to a safe path below dest."""
    p = original.replace("\\", "/")
    parts = [x.rstrip(":") for x in p.split("/") if x not in ("", ".", "..", "?")]
    parts = [x for x in parts if x]
    return os.path.join(dest, *parts)


def run_restore(snapshot: str, dest: str, cfg: dict = None, progress=None) -> dict:
    """Restore a whole snapshot below dest. Every byte is verified. Never raises."""
    setup_logging()
    cfg = cfg or config.load()
    result = {"kind": "restore", "started": now_iso(), "status": "failed", "warnings": [],
              "files": 0, "bytes": 0, "snapshot": snapshot, "dest": dest}
    try:
        with _Lock():
            _progress(progress, "connect", 0, 0, "Connecting to the backup machine…")
            with _connect(cfg, create=False) as repo:
                manifest = repo.manifest(snapshot)
                total = manifest["stats"]["bytes"]
                done = 0
                for f in manifest["files"]:
                    target = _restore_target(dest, f["p"])
                    _progress(progress, "restore", done, total, f["p"])
                    part = target + ".safekeep-part"
                    try:
                        os.makedirs(_lp(os.path.dirname(target)), exist_ok=True)
                        with open(_lp(part), "wb") as out:
                            for blob_name in f["c"]:
                                data = repo.keys.open_chunk(blob_name, repo.remote.get_blob(blob_name))
                                out.write(data)
                                done += len(data)
                                _progress(progress, "restore", done, total, f["p"])
                        os.replace(_lp(part), _lp(target))
                        os.utime(_lp(target), ns=(f["m"], f["m"]))
                        result["files"] += 1
                        result["bytes"] += f["s"]
                    except (crypto.IntegrityError, RemoteError, OSError) as e:
                        result["warnings"].append(f"{f['p']}: {e}")
                        try:
                            os.remove(_lp(part))
                        except OSError:
                            pass
                        if isinstance(e, RemoteError) and repo.remote.proc.poll() is not None:
                            raise
                if result["warnings"]:
                    result["error"] = f"{len(result['warnings'])} files could not be restored."
                    result["status"] = "warning" if result["files"] else "failed"
                else:
                    result["status"] = "success"
    except (BackupError, RemoteError, crypto.IntegrityError) as e:
        result["error"] = str(e)
    except Exception as e:
        log.exception("unexpected failure")
        result["error"] = f"Unexpected error: {type(e).__name__}: {e}"
    result["finished"] = now_iso()
    result["warnings"] = result["warnings"][:50]
    config.record_run(result)
    return result


def test_connection(cfg: dict = None) -> str:
    """Return a one-line description of the destination, or raise BackupError."""
    cfg = cfg or config.load()
    remote = Remote(cfg["destination"])
    try:
        remote.connect()
        info = remote.open(create=False)
        if info.get("meta") is None:
            return "Connected. No backups there yet; the folder will be created on the first backup."
        free = remote.call("stat")[0]["free"]
        count = len(remote.list_snapshots())
        return f"Connected. {count} backups stored, {free / 1e9:.1f} GB free."
    except RemoteError as e:
        raise BackupError(str(e))
    finally:
        remote.close()
