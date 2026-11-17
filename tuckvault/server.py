"""Receives backups from phones running the Tuckvault mobile app.

`tuckvault serve` is a small HTTP service a phone talks to, normally over
Tailscale. It is a doorway to the same storage agent the PC backups use:

* The phone encrypts everything itself, in the same format as the PC. This
  service, like the storage agent behind it, only ever sees opaque blobs
  named by their SHA-256 and cannot read file names or content.
* Every phone vault is a normal Tuckvault repository in its own folder
  next to the PC's backups ("<destination>-phones/<vault>"), on a local
  folder or on another computer over SSH.
* A phone is paired once with a short code shown on this computer. After
  that every request is signed with the secret agreed at pairing (HMAC),
  so the secret itself never travels again.

Standard library only, like the agent.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__, config, paths
from .remote import BLOB_NAME, SNAP_NAME, ConnectError, Remote, RemoteError, clean_text, folder_of

API = 1
DEFAULT_PORT = 8743
MAX_BLOB = 64 * 1024 * 1024      # the storage agent's own limit
MAX_JSON = 4 * 1024 * 1024
MAX_SKEW = 15 * 60               # seconds a phone's clock may differ from ours
PAIR_MINUTES = 10
PAIR_TRIES = 5
IDLE_CLOSE = 20                  # seconds before an unused vault connection is released
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no I, L, O, 0, 1
_VAULT = re.compile(r"[0-9a-f]{24}")
_DEVICE = re.compile(r"[0-9a-f]{12}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

log = logging.getLogger("tuckvault")


class ServeError(Exception):
    """A problem worth showing to the user as-is."""


class _Reply(Exception):
    """Ends a request with an error status the phone app understands."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# -- paired phones (phones.json) -------------------------------------------
def load() -> dict:
    st = config.read_json(paths.phones_file(), {})
    if not isinstance(st, dict):
        st = {}
    if not isinstance(st.get("devices"), dict):
        st["devices"] = {}
    return st


def save(st: dict) -> None:
    config.write_json(paths.phones_file(), st)
    if sys.platform != "win32":
        try:
            os.chmod(paths.phones_file(), 0o600)
        except OSError:
            pass


def _code_hash(code: str) -> str:
    clean = "".join(c for c in str(code).upper() if c.isalnum())
    return hashlib.sha256(b"tuckvault-pairing/" + clean.encode("ascii", "replace")).hexdigest()


def new_pairing_code() -> str:
    """Start pairing: returns the code to type into the phone (valid for a few minutes, one use)."""
    code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    st = load()
    st["pairing"] = {"hash": _code_hash(code), "expires": time.time() + PAIR_MINUTES * 60, "tries": 0}
    save(st)
    return code[:4] + "-" + code[4:]


def pair(code: str, name: str, platform: str = "") -> dict:
    """Trade a pairing code for a device id and secret. Raises _Reply."""
    st = load()
    pending = st.get("pairing")
    if not isinstance(pending, dict) or time.time() > float(pending.get("expires") or 0):
        st.pop("pairing", None)
        save(st)
        raise _Reply(403, "no_pairing", "No pairing is in progress on the computer. "
                                        "Start it there and use the new code.")
    if not hmac.compare_digest(str(pending.get("hash")), _code_hash(code)):
        pending["tries"] = int(pending.get("tries") or 0) + 1
        if pending["tries"] >= PAIR_TRIES:
            st.pop("pairing", None)
        save(st)
        raise _Reply(403, "bad_code", "That code is not right." if "pairing" in st else
                     "Too many wrong codes. Start pairing again on the computer.")
    st.pop("pairing", None)
    device = secrets.token_hex(6)
    secret = secrets.token_hex(32)
    st["devices"][device] = {"name": clean_text(name or "Phone", 60), "platform": clean_text(platform, 20),
                             "secret": secret, "paired": _now()}
    save(st)
    log.info("paired phone %s (%s)", device, st["devices"][device]["name"])
    return {"device": device, "secret": secret}


def remove_device(device: str) -> bool:
    st = load()
    if st["devices"].pop(device, None) is None:
        return False
    save(st)
    return True


def _note_vault(vault: str, device: dict) -> None:
    """Remember which vaults exist, so `tuckvault phone vaults` can list them."""
    st = load()
    vaults = st.setdefault("vaults", {})
    if vault not in vaults:
        vaults[vault] = {"name": device.get("name"), "device": device.get("id"), "first_used": _now()}
        save(st)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def signature(secret_hex: str, method: str, target: str, when, body_hash: str) -> str:
    """What the phone puts in X-Tuckvault-Sign. target is the path with its query, as sent."""
    msg = "\n".join([method.upper(), target, str(when), body_hash]).encode("utf-8")
    return hmac.new(bytes.fromhex(secret_hex), msg, hashlib.sha256).hexdigest()


# -- where phone backups are stored ----------------------------------------
def phones_root(cfg: dict = None, st: dict = None) -> dict:
    """The destination all phone vaults live under."""
    st = st or load()
    own = st.get("destination")
    if isinstance(own, dict) and own.get("type"):
        return own
    dest = (cfg or config.load())["destination"]
    if dest.get("type") == "local":
        folder = folder_of(dest).strip()
        if not folder:
            raise ServeError("Choose where backups go in Tuckvault first; phone backups are kept next to them.")
        return {"type": "local", "folder": folder.rstrip("\\/") + "-phones"}
    host = (dest.get("host") or "").strip()
    if not host and dest.get("type") != "process":
        raise ServeError("Choose where backups go in Tuckvault first; phone backups are kept next to them.")
    return dict(dest, path=(dest.get("path") or "tuckvault-repo").rstrip("/") + "-phones")


def vault_destination(root: dict, vault: str) -> dict:
    if not _VAULT.fullmatch(vault or ""):
        raise ValueError("bad vault name")
    if root.get("type") == "local":
        return {"type": "local", "folder": os.path.join(folder_of(root), vault)}
    return dict(root, path=root["path"].rstrip("/") + "/" + vault)


def describe_root(root: dict) -> str:
    if root.get("type") == "local":
        return folder_of(root)
    return "%s:%s" % (root.get("host") or "this computer", root.get("path"))


class _Vault:
    """One phone vault: a connection to its storage agent, used by one request at a time."""

    def __init__(self, dest: dict):
        self.dest = dest
        self.lock = threading.Lock()
        self.remote = None
        self.meta = None
        self.opened = False
        self.used = time.monotonic()

    def _connect(self, create: bool):
        if self.remote is None:
            if create and self.dest.get("type") == "local":
                # The folder all phone vaults share. Only made where its own parent
                # exists, so an unplugged disk is never quietly replaced by a new folder.
                root = os.path.dirname(folder_of(self.dest))
                if not os.path.isdir(root) and os.path.isdir(os.path.dirname(root)):
                    os.mkdir(root)
            remote = Remote(self.dest)
            remote.connect()
            self.remote, self.opened, self.meta = remote, False, None
        if not self.opened:
            info = self.remote.open(create=create)
            self.meta = info.get("meta")
            self.opened = self.meta is not None or create

    def drop(self):
        if self.remote is not None:
            try:
                self.remote.close()
            except Exception:
                pass
        self.remote, self.opened, self.meta = None, False, None

    def run(self, fn, create: bool = False, need_open: bool = True):
        """Call fn(remote) with the vault open. Reconnects once if the connection had died."""
        with self.lock:
            self.used = time.monotonic()
            for attempt in (0, 1):
                try:
                    self._connect(create)
                    if need_open and not self.opened:
                        raise _Reply(404, "no_vault", "There are no backups from this phone here yet.")
                    return fn(self.remote)
                except _Reply:
                    raise
                except ConnectError as e:
                    self.drop()
                    raise _Reply(503, "storage_unreachable", str(e))
                except RemoteError as e:
                    dead = self.remote is None or self.remote.proc is None or self.remote.proc.poll() is not None
                    self.drop()
                    if "in use by another backup" in str(e):
                        raise _Reply(423, "busy", "These backups are in use by another Tuckvault right now. "
                                                  "Try again in a moment.")
                    if dead and attempt == 0:
                        continue
                    raise _Reply(502, "storage_error", str(e))
                finally:
                    self.used = time.monotonic()


class Vaults:
    def __init__(self, root: dict):
        self.root = root
        self._all = {}
        self._lock = threading.Lock()

    def get(self, vault: str) -> _Vault:
        with self._lock:
            if vault not in self._all:
                self._all[vault] = _Vault(vault_destination(self.root, vault))
            return self._all[vault]

    def release_idle(self, older_than: float = IDLE_CLOSE):
        """Let go of connections nobody used lately, so other programs can use those backups."""
        with self._lock:
            vaults = list(self._all.values())
        for v in vaults:
            if v.remote is not None and time.monotonic() - v.used >= older_than and v.lock.acquire(blocking=False):
                try:
                    if time.monotonic() - v.used >= older_than:
                        v.drop()
                finally:
                    v.lock.release()

    def close(self):
        with self._lock:
            vaults = list(self._all.values())
        for v in vaults:
            with v.lock:
                v.drop()


# -- HTTP ------------------------------------------------------------------
_ROUTE = re.compile(r"/v1/vaults/([0-9a-f]{24})/([a-z-]+)(?:/([0-9A-Za-z._-]{1,100}))?")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Tuckvault"
    sys_version = ""
    timeout = 120

    def log_message(self, fmt, *args):  # nothing on stderr: pythonw has none
        log.debug("phone %s " + fmt, self.client_address[0], *args)

    # -- plumbing --------------------------------------------------------
    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    def _body(self, limit: int) -> bytes:
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0 or size > limit:
            self.close_connection = True  # the unread body would be taken for the next request
            raise _Reply(413, "too_large", "That is larger than this service accepts.")
        data = self.rfile.read(size) if size else b""
        if len(data) != size:
            self.close_connection = True
            raise _Reply(400, "cut_short", "The upload was cut short.")
        return data

    def _device(self, body: bytes) -> dict:
        device = self.headers.get("X-Tuckvault-Device") or ""
        sign = self.headers.get("X-Tuckvault-Sign") or ""
        when = self.headers.get("X-Tuckvault-Time") or ""
        known = load()["devices"].get(device) if _DEVICE.fullmatch(device) else None
        if known is None or not _HEX64.fullmatch(sign) or not when.isdigit():
            raise _Reply(401, "not_paired", "This phone is not paired with this computer (any more).")
        if abs(time.time() - int(when)) > MAX_SKEW:
            raise _Reply(401, "clock", "The clock on the phone or on the computer is wrong; "
                                       "they differ by more than a few minutes.")
        want = signature(known["secret"], self.command, self.path, when, hashlib.sha256(body).hexdigest())
        if not hmac.compare_digest(want, sign):
            raise _Reply(401, "not_paired", "This phone is not paired with this computer (any more).")
        return dict(known, id=device)

    def _handle(self):
        try:
            try:
                self._route()
            except _Reply as e:
                self._json({"error": e.code, "message": e.message}, e.status)
            except (BrokenPipeError, ConnectionError, socket.timeout):
                self.close_connection = True
            except Exception as e:  # never take the service down over one request
                log.exception("phone request failed")
                self.close_connection = True
                self._json({"error": "internal", "message": "%s: %s" % (type(e).__name__, e)}, 500)
        except OSError:
            self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    # -- routes ----------------------------------------------------------
    def _route(self):
        url = urlsplit(self.path)
        method = self.command
        if url.path == "/v1/hello" and method == "GET":
            return self._json({"app": "tuckvault", "api": API, "version": __version__,
                               "name": socket.gethostname()})
        if url.path == "/v1/pair" and method == "POST":
            try:
                req = json.loads(self._body(4096))
                code, name, platform = str(req["code"]), str(req.get("name") or ""), str(req.get("platform") or "")
            except (ValueError, KeyError, TypeError):
                raise _Reply(400, "bad_request", "Bad request.")
            with self.server.pair_lock:
                return self._json(dict(pair(code, name, platform), name=socket.gethostname()))

        m = _ROUTE.fullmatch(url.path)
        blob_upload = bool(m and m.group(2) == "blobs" and method == "PUT")
        body = self._body(MAX_BLOB if blob_upload else MAX_JSON) if method in ("POST", "PUT") else b""
        device = self._device(body)
        if url.path == "/v1/whoami" and method == "GET":
            return self._json({"ok": True, "name": socket.gethostname(), "version": __version__})
        if not m:
            raise _Reply(404, "not_found", "Not found.")
        vault, what, name = self.server.vaults.get(m.group(1)), m.group(2), m.group(3)
        query = parse_qs(url.query)

        def data():
            try:
                parsed = json.loads(body or b"{}")
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                raise _Reply(400, "bad_request", "Bad request.")
            return parsed

        def names():
            got = data().get("names")
            if not isinstance(got, list) or not all(isinstance(n, str) and BLOB_NAME.fullmatch(n) for n in got):
                raise _Reply(400, "bad_request", "Bad request.")
            return got

        if what == "open" and method == "POST" and name is None:
            create = bool(data().get("create"))

            def opened(remote):
                stat = remote.call("stat")[0] if vault.opened else {}
                return {"meta": vault.meta, "free": stat.get("free"), "total": stat.get("total")}
            info = vault.run(opened, create=create, need_open=False)
            if info["meta"] is not None:
                _note_vault(m.group(1), device)
            return self._json(info)
        if what == "init" and method == "POST" and name is None:
            meta = data().get("meta")
            if not (isinstance(meta, dict) and isinstance(meta.get("id"), str)
                    and re.fullmatch(r"[0-9a-f]{16}", meta["id"]) and isinstance(meta.get("key_check"), str)
                    and _HEX64.fullmatch(meta["key_check"]) and meta["key_check"][:24] == m.group(1)
                    and len(json.dumps(meta)) < 2000):
                raise _Reply(400, "bad_request", "Bad request.")

            def init(remote):
                remote.call("init", meta=meta)
                vault.meta = meta
                return {}
            vault.run(init, create=True)
            _note_vault(m.group(1), device)
            return self._json({})
        if what == "blobs" and name is None:
            if method == "GET":
                return self._send(200, vault.run(lambda r: r.call("list_blobs")[1]), "text/plain")
        if what == "blobs" and name is not None:
            if not BLOB_NAME.fullmatch(name):
                raise _Reply(400, "bad_request", "Bad request.")
            if method == "PUT":
                if hashlib.sha256(body).hexdigest() != name:
                    raise _Reply(400, "damaged", "A piece arrived damaged; it will be sent again.")
                vault.run(lambda r: r.put_blob(name, body))
                return self._json({})
            if method == "GET":
                try:
                    return self._send(200, vault.run(lambda r: r.get_blob(name)), "application/octet-stream")
                except _Reply as e:
                    if "is missing" in e.message:
                        raise _Reply(404, "missing", e.message)
                    raise
        if what == "verify" and method == "POST" and name is None:
            wanted, full = names(), bool(data().get("full"))
            return self._json(vault.run(lambda r: r.verify_blobs(wanted, full=full)))
        if what == "delete-blobs" and method == "POST" and name is None:
            wanted = names()
            return self._json({"deleted": vault.run(lambda r: r.delete_blobs(wanted))})
        if what == "snapshots" and name is None and method == "GET":
            return self._json({"snapshots": vault.run(lambda r: r.list_snapshots())})
        if what == "snapshots" and name is not None:
            if not SNAP_NAME.fullmatch(name):
                raise _Reply(400, "bad_request", "Bad request.")
            if method == "PUT":
                replace = query.get("replace") == ["1"]
                try:
                    vault.run(lambda r: r.put_snapshot(name, body, replace=replace))
                except _Reply as e:
                    if "already exists" in e.message:
                        raise _Reply(409, "exists", e.message)
                    raise
                return self._json({})
            if method == "GET":
                try:
                    return self._send(200, vault.run(lambda r: r.get_snapshot(name)), "application/octet-stream")
                except _Reply as e:
                    if "is missing" in e.message:
                        raise _Reply(404, "missing", e.message)
                    raise
            if method == "DELETE":
                vault.run(lambda r: r.delete_snapshot(name))
                return self._json({})
        raise _Reply(404, "not_found", "Not found.")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address):  # a dropped connection is not news
        log.debug("phone connection from %s ended with an error", client_address[0])


# -- addresses -------------------------------------------------------------
def _is_tailscale(ip: str) -> bool:
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127


def _tailscale_exe():
    if sys.platform == "win32":
        candidates = [os.path.join(os.environ.get("ProgramFiles") or r"C:\Program Files",
                                   "Tailscale", "tailscale.exe")]
    else:
        candidates = ["/usr/bin/tailscale", "/usr/sbin/tailscale", "/usr/local/bin/tailscale"]
    return next((c for c in candidates if os.path.isfile(c)), None)


def tailscale_addresses() -> list:
    """This computer's Tailscale IPv4 addresses (empty if Tailscale is not running)."""
    found = set()
    exe = _tailscale_exe()
    if exe:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
                                 creationflags=_NO_WINDOW).stdout
            found.update(line.strip() for line in out.splitlines() if _is_tailscale(line.strip()))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if _is_tailscale(info[4][0]):
                found.add(info[4][0])
    except OSError:
        pass
    return sorted(found)


def tailscale_name():
    """This computer's Tailscale DNS name, if it has one."""
    exe = _tailscale_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5,
                             creationflags=_NO_WINDOW).stdout
        name = (json.loads(out).get("Self") or {}).get("DNSName") or ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    name = name.rstrip(".")
    return name if re.fullmatch(r"[A-Za-z0-9.-]{3,200}", name) else None


def port() -> int:
    try:
        p = int(load().get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        p = DEFAULT_PORT
    return p if 1024 <= p <= 65535 else DEFAULT_PORT


def phone_addresses() -> list:
    """What to type into the phone, best first."""
    p = port()
    out = []
    name = tailscale_name()
    ips = tailscale_addresses()
    if name and ips:
        out.append("http://%s:%d" % (name, p))
    out += ["http://%s:%d" % (ip, p) for ip in ips]
    return out


class Service:
    """The running service: one listener per address."""

    def __init__(self, cfg: dict = None, listen: str = None, port_number: int = None):
        self.root = phones_root(cfg)
        self.vaults = Vaults(self.root)
        self.listen = listen
        self.port = port_number or port()
        self.servers = {}
        self._stop = threading.Event()
        self._pair_lock = threading.Lock()

    def _bind(self, address: str) -> bool:
        if address in self.servers:
            return True
        try:
            server = _Server((address, self.port), Handler)
        except OSError as e:
            log.warning("cannot listen on %s:%d: %s", address, self.port, e)
            return False
        server.vaults, server.pair_lock = self.vaults, self._pair_lock
        if not self.port:
            self.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers[address] = server
        log.info("accepting phone backups on %s:%d", address, self.port)
        return True

    def refresh(self):
        """Listen on Tailscale addresses that appeared since the start (Tailscale may start after us)."""
        if self.listen is None:
            for ip in tailscale_addresses():
                self._bind(ip)

    def start(self):
        if self.listen is not None:
            if not self._bind(self.listen):
                raise ServeError("Cannot listen on %s port %d. Is Tuckvault already accepting phone "
                                 "backups?" % (self.listen, self.port))
        else:
            if not self._bind("127.0.0.1"):
                raise ServeError("Port %d on this computer is taken. Is Tuckvault already accepting "
                                 "phone backups?" % self.port)
            self.refresh()
        threading.Thread(target=self._housekeeping, daemon=True).start()
        return self

    def _housekeeping(self):
        ticks = 0
        while not self._stop.wait(5):
            ticks += 1
            self.vaults.release_idle()
            if ticks % 6 == 0:
                self.refresh()

    def stop(self):
        self._stop.set()
        for server in self.servers.values():
            server.shutdown()
            server.server_close()
        self.servers = {}
        self.vaults.close()

    def wait(self):
        try:
            while not self._stop.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        self.stop()


def is_running(port_number: int = None) -> bool:
    """True if a Tuckvault phone service answers on this computer."""
    import urllib.request
    try:
        # Fixed loopback address, small bounded read.
        with urllib.request.urlopen("http://127.0.0.1:%d/v1/hello" % (port_number or port()),
                                    timeout=2) as resp:  # nosec B310
            return json.loads(resp.read(2000)).get("app") == "tuckvault"
    except Exception:  # deliberately broad: anything else than our own answer means "no"
        return False
