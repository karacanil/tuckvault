"""Talks to the storage agent, over SSH or on a local folder."""

import collections
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

MARKER = b"SAFEKEEP-AGENT 1\n"
# Runs on the far side: read the agent's source from stdin, then execute it.
# Nothing has to be installed there except python3.
_BOOT = "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class RemoteError(Exception):
    """The storage side reported a problem."""


class ConnectError(RemoteError):
    """The storage machine could not be reached at all."""


def _ssh_exe() -> str:
    found = shutil.which("ssh")
    if found:
        return found
    fallback = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
    if fallback.exists():
        return str(fallback)
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
        return [_ssh_exe(), "-T",
                "-o", "BatchMode=yes",
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
                line = self.proc.stdout.readline()
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
            for line in self.proc.stderr:
                self._stderr.append(line.decode("utf-8", "replace").strip())
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
        resp = json.loads(self._read_exact(struct.unpack(">I", self._read_exact(4))[0]))
        data = self._read_exact(resp["size"]) if resp.get("size") else b""
        if not resp.get("ok"):
            raise RemoteError(resp.get("error") or "the backup machine reported an error")
        return resp, data

    # -- convenience -----------------------------------------------------
    def open(self, create: bool):
        if self.dest.get("type") == "local":
            return self.call("open", repo=folder_of(self.dest), create=create)[0]
        return self.call("open", repo=self.dest.get("path") or "safekeep-repo", create=create)[0]

    def list_blobs(self) -> dict:
        _, data = self.call("list_blobs")
        out = {}
        for line in data.decode("ascii").split("\n"):
            if line:
                name, size = line.split(" ")
                out[name] = int(size)
        return out

    def put_blob(self, name: str, blob: bytes):
        self.call("put_blob", blob, name=name)

    def get_blob(self, name: str) -> bytes:
        return self.call("get_blob", name=name)[1]

    def verify_blobs(self, names, full: bool):
        resp, _ = self.call("verify_blobs", "\n".join(names).encode("ascii"),
                            mode="full" if full else "quick")
        return resp

    def delete_blobs(self, names) -> int:
        return self.call("delete_blobs", "\n".join(names).encode("ascii"))[0]["deleted"]

    def list_snapshots(self):
        return self.call("list_snapshots")[0]["snapshots"]

    def put_snapshot(self, name: str, raw: bytes):
        self.call("put_snapshot", raw, name=name, sha256=hashlib.sha256(raw).hexdigest())

    def get_snapshot(self, name: str) -> bytes:
        return self.call("get_snapshot", name=name)[1]

    def delete_snapshot(self, name: str):
        self.call("delete_snapshot", name=name)
