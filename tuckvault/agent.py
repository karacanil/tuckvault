"""Tuckvault storage agent.

This file runs on the machine that *stores* the backups. The client sends
its source over SSH and executes it with the system python3, so it must
stay standard-library only and must not import anything from tuckvault.

The agent never sees plaintext or keys. It stores opaque blobs named by
the SHA-256 of their content, and refuses any blob whose content does not
hash to its name. That lets it detect corruption on its own disk without
being able to read the data.

Wire format, both directions:
    4-byte big-endian length, JSON header, then header["size"] payload bytes.
"""

import hashlib
import json
import os
import re
import shutil
import struct
import sys

try:
    import fcntl
except ImportError:  # Windows (folder destinations)
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

MARKER = b"TUCKVAULT-AGENT 1\n"
MAX_PAYLOAD = 64 * 1024 * 1024
BLOB_RE = re.compile(r"^[0-9a-f]{64}$")
SNAP_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,99}$")
TMP_RE = re.compile(r"^[0-9]+\..+\.tmp$")  # only ever remove temp files this program made


class Err(Exception):
    pass


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(path, data, tmpdir):
    tmp = os.path.join(tmpdir, "%d.%s.tmp" % (os.getpid(), os.path.basename(path)))
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


class Agent:
    def __init__(self, inp, out):
        self.inp = inp
        self.out = out
        self.root = None
        self.lock_fh = None

    # -- framing ---------------------------------------------------------
    def read_exact(self, n):
        chunks = []
        left = n
        while left:
            buf = self.inp.read(left)
            if not buf:
                raise EOFError
            chunks.append(buf)
            left -= len(buf)
        return b"".join(chunks)

    def reply(self, hdr, payload=None):
        if payload is not None:
            hdr["size"] = len(payload)
        raw = json.dumps(hdr).encode("utf-8")
        self.out.write(struct.pack(">I", len(raw)) + raw)
        if payload:
            self.out.write(payload)
        self.out.flush()

    def serve(self):
        try:
            self._serve()
        finally:
            if self.lock_fh is not None:  # matters when the agent runs inside the client process
                try:
                    self.lock_fh.close()
                except OSError:
                    pass
                self.lock_fh = None

    def _serve(self):
        self.out.write(MARKER)
        self.out.flush()
        while True:
            raw = self.inp.read(4)
            if len(raw) < 4:
                return
            try:
                hdr = json.loads(self.read_exact(struct.unpack(">I", raw)[0]))
                size = int(hdr.get("size") or 0)
                if size > MAX_PAYLOAD:
                    self.reply({"ok": False, "error": "payload too large"})
                    return
                payload = self.read_exact(size) if size else b""
            except EOFError:
                return
            op = hdr.get("op")
            if op == "quit":
                self.reply({"ok": True})
                return
            try:
                fn = getattr(self, "op_" + str(op), None)
                if fn is None:
                    raise Err("unknown operation: %s" % op)
                if self.root is None and op != "open":
                    raise Err("repository not opened")
                res = fn(hdr, payload)
                if isinstance(res, tuple):
                    out_hdr, out_payload = res
                else:
                    out_hdr, out_payload = res, None
                out_hdr = dict(out_hdr or {})
                out_hdr["ok"] = True
                self.reply(out_hdr, out_payload)
            except Err as e:
                self.reply({"ok": False, "error": str(e)})
            except Exception as e:  # keep the stream in sync whatever happens
                self.reply({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    # -- paths -----------------------------------------------------------
    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def blob_path(self, name):
        if not BLOB_RE.fullmatch(name or ""):
            raise Err("bad blob name")
        return self.p("blobs", name[:2], name)

    def snap_path(self, name):
        if not SNAP_RE.fullmatch(name or ""):
            raise Err("bad snapshot name")
        return self.p("snapshots", name)

    def names(self, payload):
        return [n for n in payload.decode("ascii").split("\n") if n]

    # -- operations ------------------------------------------------------
    def op_open(self, hdr, payload):
        root = os.path.abspath(os.path.expanduser(hdr["repo"]))
        exists = os.path.isfile(os.path.join(root, "repo.json"))
        if not exists and not hdr.get("create"):
            return {"meta": None}
        for d in (root, os.path.join(root, "blobs"), os.path.join(root, "snapshots"),
                  os.path.join(root, "tmp")):
            if not os.path.isdir(d):
                os.makedirs(d, 0o700)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        # One user of the repository at a time, whoever and wherever they are.
        fh = open(os.path.join(root, "lock"), "a+")
        try:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            raise Err("the backup repository is in use by another backup")
        self.lock_fh = fh
        self.root = root
        # Leftovers from an interrupted upload are never referenced; drop them.
        # Only in a folder that already was a repository, and only our own files.
        if exists:
            for name in os.listdir(self.p("tmp")):
                if TMP_RE.fullmatch(name):
                    try:
                        os.remove(self.p("tmp", name))
                    except OSError:
                        pass
        meta = None
        if exists:
            with open(self.p("repo.json"), "r") as f:
                meta = json.load(f)
        return {"meta": meta, "root": root}

    def op_init(self, hdr, payload):
        if os.path.exists(self.p("repo.json")):
            raise Err("repository already initialised")
        _write_atomic(self.p("repo.json"),
                      json.dumps(hdr["meta"], indent=1).encode("utf-8"), self.p("tmp"))
        return {}

    def op_stat(self, hdr, payload):
        du = shutil.disk_usage(self.root)
        return {"free": du.free, "total": du.total}

    def op_list_blobs(self, hdr, payload):
        lines = []
        base = self.p("blobs")
        for sub in sorted(os.listdir(base)):
            d = os.path.join(base, sub)
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if BLOB_RE.fullmatch(name):
                    try:
                        lines.append("%s %d" % (name, os.path.getsize(os.path.join(d, name))))
                    except OSError:
                        pass
        return {"count": len(lines)}, "\n".join(lines).encode("ascii")

    def op_put_blob(self, hdr, payload):
        name = hdr.get("name")
        path = self.blob_path(name)
        if hashlib.sha256(payload).hexdigest() != name:
            raise Err("blob %s arrived damaged (hash mismatch)" % name)
        if os.path.exists(path) and os.path.getsize(path) == len(payload):
            return {"stored": False}
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d, 0o700)
        _write_atomic(path, payload, self.p("tmp"))
        return {"stored": True}

    def op_get_blob(self, hdr, payload):
        path = self.blob_path(hdr.get("name"))
        try:
            with open(path, "rb") as f:
                return {}, f.read()
        except FileNotFoundError:
            raise Err("blob %s is missing" % hdr.get("name"))

    def op_verify_blobs(self, hdr, payload):
        """mode=quick: present and non-empty. mode=full: re-hash content.

        "unreadable" means the disk refused to give the data this time. That
        can be temporary (loose cable, busy share), so it is never treated
        as proof of damage.
        """
        full = hdr.get("mode") == "full"
        missing, corrupt, unreadable, nbytes = [], [], [], 0
        for name in self.names(payload):
            path = self.blob_path(name)
            try:
                size = os.path.getsize(path)
            except FileNotFoundError:
                missing.append(name)
                continue
            except OSError:
                unreadable.append(name)
                continue
            nbytes += size
            if size == 0:
                corrupt.append(name)
            elif full:
                try:
                    if _sha256_file(path) != name:
                        corrupt.append(name)
                except OSError:
                    unreadable.append(name)
        return {"missing": missing, "corrupt": corrupt, "unreadable": unreadable, "bytes": nbytes}

    def op_quarantine_blobs(self, hdr, payload):
        """Move damaged blobs aside instead of destroying them."""
        qdir = self.p("quarantine")
        if not os.path.isdir(qdir):
            os.makedirs(qdir, 0o700)
        n = 0
        for name in self.names(payload):
            src = self.blob_path(name)
            for i in range(1000):
                dst = os.path.join(qdir, "%s.%d" % (name, i))
                if not os.path.exists(dst):
                    break
            try:
                os.replace(src, dst)
                n += 1
            except FileNotFoundError:
                pass
        return {"moved": n}

    def op_delete_blobs(self, hdr, payload):
        n = 0
        for name in self.names(payload):
            try:
                os.remove(self.blob_path(name))
                n += 1
            except FileNotFoundError:
                pass
        return {"deleted": n}

    def op_list_snapshots(self, hdr, payload):
        out = []
        for name in sorted(os.listdir(self.p("snapshots"))):
            if SNAP_RE.fullmatch(name) and name.endswith(".snap"):
                out.append({"name": name, "size": os.path.getsize(self.p("snapshots", name))})
        return {"snapshots": out}

    def op_hash_snapshots(self, hdr, payload):
        """SHA-256 of every stored snapshot file, so the client can compare with its copies."""
        out = {}
        for name in sorted(os.listdir(self.p("snapshots"))):
            if SNAP_RE.fullmatch(name) and name.endswith(".snap"):
                try:
                    out[name] = _sha256_file(self.p("snapshots", name))
                except OSError:
                    out[name] = None
        return {"hashes": out}

    def op_put_snapshot(self, hdr, payload):
        path = self.snap_path(hdr.get("name"))
        if os.path.exists(path) and not hdr.get("replace"):
            raise Err("snapshot already exists")
        if hashlib.sha256(payload).hexdigest() != hdr.get("sha256"):
            raise Err("snapshot arrived damaged (hash mismatch)")
        _write_atomic(path, payload, self.p("tmp"))
        return {}

    def op_get_snapshot(self, hdr, payload):
        try:
            with open(self.snap_path(hdr.get("name")), "rb") as f:
                return {}, f.read()
        except FileNotFoundError:
            raise Err("snapshot %s is missing" % hdr.get("name"))

    def op_delete_snapshot(self, hdr, payload):
        try:
            os.remove(self.snap_path(hdr.get("name")))
        except FileNotFoundError:
            pass
        return {}


if __name__ == "__main__":
    Agent(sys.stdin.buffer, sys.stdout.buffer).serve()
