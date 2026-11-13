"""Talks to the storage agent, over SSH or on a local folder."""

import collections
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

MARKER = b"SAFEKEEP-AGENT 1\n"
# The storage side is not trusted: never let a reply make this PC allocate without bound.
MAX_HEADER = 64 * 1024 * 1024
MAX_PAYLOAD = 512 * 1024 * 1024
# Runs on the far side: read the agent's source from stdin, then execute it.
# Nothing has to be installed there except python3.
_BOOT = "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# Names coming back from the storage side end up in local file paths, so
# they are checked here as well, not only by the agent.
BLOB_NAME = re.compile(r"[0-9a-f]{64}")
SNAP_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,95}\.snap")
REPO_ID = re.compile(r"[0-9a-f]{16}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_INVALID = "The backup destination sent an invalid reply."


def clean_text(text, limit=300) -> str:
    """Text from the other side, made safe to show: no control characters, bounded."""
    text = _CONTROL.sub(" ", str(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


class RemoteError(Exception):
    """The storage side reported a problem."""


class ConnectError(RemoteError):
    """The storage machine could not be reached at all."""


def _ssh_exe() -> str:
    # Windows' own OpenSSH first (the same one scheduled runs get), then PATH.
    from . import paths
    system = paths.system_exe("ssh.exe")
    if os.path.isabs(system):
        return system
    found = shutil.which("ssh")
    if found:
        return found
    raise ConnectError("The ssh program was not found on this PC.")


class _InProcessAgent:
    """The storage agent in a thread of this process, for folder destinations.

    Looks enough like a subprocess.Popen for Remote: the same protocol runs
    over two pipes, so a folder is handled exactly like a remote machine.
    """

    def __init__(self):
        from . import agent
        to_agent_r, to_agent_w = os.pipe()
        from_agent_r, from_agent_w = os.pipe()
        self.stdin = os.fdopen(to_agent_w, "wb")
        self.stdout = os.fdopen(from_agent_r, "rb")
        self.stderr = io.BytesIO()
        inp, out = os.fdopen(to_agent_r, "rb"), os.fdopen(from_agent_w, "wb")

        def serve():
            try:
                agent.Agent(inp, out).serve()
            except Exception:
                pass
            finally:
                for stream in (out, inp):
                    try:
                        stream.close()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()

    def poll(self):
        return None if self._thread.is_alive() else 0

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("agent", timeout)
        return 0

    def kill(self):
        try:
            self.stdin.close()
        except Exception:
            pass


def folder_of(destination: dict) -> str:
    """The folder a "local" destination stores into."""
    return destination.get("folder") or destination.get("path") or ""


class Remote:
    def __init__(self, destination: dict):
        self.dest = destination
        self.proc = None
        self._stderr = collections.deque(maxlen=40)

    # -- connection ------------------------------------------------------
    def _command(self):
        if self.dest.get("type") == "process":  # the SSH code path without SSH, for tests
            return [sys.executable, "-c", _BOOT]
        host = (self.dest.get("host") or "").strip()
        if not host or host.startswith("-"):
            raise ConnectError("No backup machine is set.")
        return [_ssh_exe(), "-T", "-a", "-x",
                "-o", "BatchMode=yes",
                # the backup computer gets no way back into this PC or its other logins
                "-o", "ForwardAgent=no",
                "-o", "ClearAllForwardings=yes",
                "-o", "PermitLocalCommand=no",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=4",
                host, "python3 -c '%s'" % _BOOT]

    def connect(self):
        local = self.dest.get("type") == "local"
        if local:
            folder = folder_of(self.dest).strip()
            if not folder:
                raise ConnectError("No backup folder is set.")
            anchor = Path(os.path.abspath(folder)).anchor
            if anchor and not os.path.exists(anchor):
                raise ConnectError(f'The backup drive "{anchor}" is not connected.')
            self.proc = _InProcessAgent()
        else:
            source = (Path(__file__).parent / "agent.py").read_bytes()
            try:
                self.proc = subprocess.Popen(self._command(), stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             creationflags=_NO_WINDOW)
            except OSError as e:
                raise ConnectError(f"Could not start the connection: {e}")
            threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            if not local:
                self.proc.stdin.write(b"%d\n" % len(source) + source)
                self.proc.stdin.flush()
            # Skip anything a login script printed before the agent started.
            seen = 0
            while True:
                line = self.proc.stdout.readline(4096)
                if line == MARKER:
                    return self
                seen += len(line)
                if not line or seen > 1 << 16:
                    break
        except OSError:
            pass
        where = self.dest.get("host") or folder_of(self.dest)
        raise ConnectError(self._failure(
            f'Could not reach the backup machine "{where}". Is it switched on and connected?'))

    def _drain_stderr(self):
        try:
            while True:
                line = self.proc.stderr.readline(4096)
                if not line:
                    break
                self._stderr.append(clean_text(line.decode("utf-8", "replace")))
        except (OSError, ValueError):
            pass

    def _failure(self, what: str) -> str:
        try:
            self.proc.wait(timeout=3)
        except Exception:
            pass
        detail = " ".join(s for s in self._stderr if s)[-300:]
        return f"{what} ({detail})" if detail else what

    def close(self):
        if not self.proc:
            return
        try:
            if self.proc.poll() is None:
                self.call("quit")
        except Exception:
            pass
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        self.proc = None

    def kill(self):
        """Drop the connection without the polite goodbye (the stream is out of sync)."""
        try:
            self.proc.kill()
        except Exception:
            pass

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # -- protocol --------------------------------------------------------
    def _read_exact(self, n: int) -> bytes:
        parts, left = [], n
        while left:
            buf = self.proc.stdout.read(left)
            if not buf:
                raise RemoteError(self._failure("Lost the connection to the backup machine"))
            parts.append(buf)
            left -= len(buf)
        return b"".join(parts)

    def call(self, op: str, payload: bytes = None, **args):
        hdr = dict(args, op=op)
        if payload is not None:
            hdr["size"] = len(payload)
        raw = json.dumps(hdr).encode("utf-8")
        try:
            self.proc.stdin.write(struct.pack(">I", len(raw)) + raw)
            if payload:
                self.proc.stdin.write(payload)
            self.proc.stdin.flush()
        except OSError:
            raise RemoteError(self._failure("Lost the connection to the backup machine"))
        hdr_len = struct.unpack(">I", self._read_exact(4))[0]
        if hdr_len > MAX_HEADER:
            self.kill()
            raise RemoteError(_INVALID)
        try:
            resp = json.loads(self._read_exact(hdr_len))
            size = int(resp.get("size") or 0) if isinstance(resp, dict) else -1
        except (ValueError, TypeError):
            size = -1
        if size < 0 or size > MAX_PAYLOAD:
            self.kill()
            raise RemoteError(_INVALID)
        data = self._read_exact(size) if size else b""
        if not resp.get("ok"):
            raise RemoteError("The backup destination reported: "
                              + (clean_text(resp.get("error") or "") or "an error"))
        return resp, data

    def _invalid(self):
        self.kill()
        return RemoteError(_INVALID)

    # -- convenience -----------------------------------------------------
    def open(self, create: bool):
        repo = folder_of(self.dest) if self.dest.get("type") == "local" else (
            self.dest.get("path") or "safekeep-repo")
        info = self.call("open", repo=repo, create=create)[0]
        meta = info.get("meta")
        if meta is not None and not (
                isinstance(meta, dict) and isinstance(meta.get("id"), str)
                and REPO_ID.fullmatch(meta["id"]) and isinstance(meta.get("key_check"), str)):
            raise self._invalid()
        return info

    def list_blobs(self) -> dict:
        _, data = self.call("list_blobs")
        out = {}
        try:
            for line in data.decode("ascii").split("\n"):
                if line:
                    name, size = line.split(" ")
                    if not BLOB_NAME.fullmatch(name):
                        raise ValueError(name)
                    out[name] = int(size)
        except ValueError:
            raise self._invalid()
        return out

    def put_blob(self, name: str, blob: bytes):
        self.call("put_blob", blob, name=name)

    def get_blob(self, name: str) -> bytes:
        return self.call("get_blob", name=name)[1]

    def verify_blobs(self, names, full: bool):
        resp, _ = self.call("verify_blobs", "\n".join(names).encode("ascii"),
                            mode="full" if full else "quick")
        asked, out = set(names), {}
        for key in ("missing", "corrupt", "unreadable"):
            got = resp.get(key) or []
            if not isinstance(got, list) or not all(isinstance(n, str) and n in asked for n in got):
                raise self._invalid()
            out[key] = got
        return out

    def delete_blobs(self, names) -> int:
        names, deleted = list(names), 0
        for i in range(0, len(names), 10000):
            deleted += self.call("delete_blobs",
                                 "\n".join(names[i:i + 10000]).encode("ascii"))[0]["deleted"]
        return deleted

    def quarantine_blobs(self, names) -> None:
        names = list(names)
        for i in range(0, len(names), 10000):
            self.call("quarantine_blobs", "\n".join(names[i:i + 10000]).encode("ascii"))

    def list_snapshots(self):
        snaps = self.call("list_snapshots")[0].get("snapshots")
        if not isinstance(snaps, list) or not all(
                isinstance(s, dict) and isinstance(s.get("name"), str)
                and SNAP_NAME.fullmatch(s["name"]) for s in snaps):
            raise self._invalid()
        return snaps

    def snapshot_hashes(self) -> dict:
        hashes = self.call("hash_snapshots")[0].get("hashes")
        if not isinstance(hashes, dict):
            raise self._invalid()
        return {n: h for n, h in hashes.items() if SNAP_NAME.fullmatch(n)}

    def put_snapshot(self, name: str, raw: bytes, replace: bool = False):
        self.call("put_snapshot", raw, name=name, sha256=hashlib.sha256(raw).hexdigest(),
                  replace=replace)

    def get_snapshot(self, name: str) -> bytes:
        if not SNAP_NAME.fullmatch(name):
            raise RemoteError("bad snapshot name")
        return self.call("get_snapshot", name=name)[1]

    def delete_snapshot(self, name: str):
        self.call("delete_snapshot", name=name)
