"""The service that accepts backups from phones, driven the way the mobile app drives it.

    .venv\\Scripts\\python -m unittest tests.test_server -v
"""

import copy
import hashlib
import http.client
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuckvault import __main__ as cli  # noqa: E402
from tuckvault import config, crypto, engine, phones, scheduler, server  # noqa: E402


class Phone:
    """What the mobile app does on the wire: pair once, then sign every request."""

    def __init__(self, port):
        self.port, self.device, self.secret = port, None, None
        self.skew = 0

    def raw(self, method, path, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body or None, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def pair(self, code, name="Test phone"):
        status, data = self.raw("POST", "/v1/pair", json.dumps(
            {"code": code, "name": name, "platform": "android"}).encode())
        reply = json.loads(data)
        if status == 200:
            self.device, self.secret = reply["device"], reply["secret"]
        return status, reply

    def call(self, method, path, body=b"", sign_path=None, secret=None):
        when = str(int(time.time()) + self.skew)
        sign = server.signature(secret or self.secret, method, sign_path or path, when,
                                hashlib.sha256(body).hexdigest())
        return self.raw(method, path, body, {"X-Tuckvault-Device": self.device, "X-Tuckvault-Time": when,
                                             "X-Tuckvault-Sign": sign})

    def json(self, method, path, obj=None):
        status, data = self.call(method, path, json.dumps(obj).encode() if obj is not None else b"")
        return status, json.loads(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tuckvault-test-"))
        os.environ["TUCKVAULT_HOME"] = str(self.tmp / "home")
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["destination"] = {"type": "local", "folder": str(self.tmp / "repo")}
        self.service = server.Service(self.cfg, listen="127.0.0.1", port_number=self._free_port()).start()
        self.phone = Phone(self.service.port)

    def tearDown(self):
        self.service.stop()
        os.environ.pop("TUCKVAULT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _free_port():
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def paired(self):
        status, _ = self.phone.pair(server.new_pairing_code())
        self.assertEqual(status, 200)
        return self.phone

    def new_vault(self, phone=None, master=None):
        """Create a vault the way the app does. Returns (keys, base path, master)."""
        phone = phone or self.paired()
        master = master or crypto.new_master_key()
        keys = crypto.Keys(master)
        base = "/v1/vaults/" + keys.check[:24]
        status, info = phone.json("POST", base + "/open", {"create": False})
        self.assertEqual((status, info["meta"]), (200, None))
        status, _ = phone.json("POST", base + "/open", {"create": True})
        self.assertEqual(status, 200)
        meta = {"format": 1, "id": secrets.token_hex(8), "created": engine.now_iso(), "key_check": keys.check}
        status, _ = phone.json("POST", base + "/init", {"meta": meta})
        self.assertEqual(status, 200)
        return keys, base, master

    def backup(self, phone, keys, base, files: dict, source="Photos"):
        """Upload files {path: bytes} as one snapshot, as the app does. Returns the snapshot name."""
        entries = []
        status, listing = phone.call("GET", base + "/blobs")
        self.assertEqual(status, 200)
        have = {line.split(b" ")[0].decode() for line in listing.split(b"\n") if line}
        for path, data in files.items():
            chunks = []
            for i in range(0, len(data), engine.CHUNK):
                name, blob = keys.seal_chunk(data[i:i + engine.CHUNK])
                if name not in have:
                    status, _ = phone.call("PUT", base + "/blobs/" + name, blob)
                    self.assertEqual(status, 200)
                    have.add(name)
                chunks.append(name)
            entries.append({"p": path, "s": len(data), "m": 1_700_000_000_000_000_000, "c": chunks})
        name = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + ".%06dZ-%s.snap" % (
            time.time_ns() // 1000 % 1_000_000, secrets.token_hex(3))
        manifest = {"v": 1, "name": name, "created": engine.now_iso(), "host": "Test phone",
                    "sources": [source], "files": entries,
                    "stats": {"files": len(entries), "bytes": sum(len(d) for d in files.values())},
                    "warnings": []}
        status, _ = phone.call("PUT", base + "/snapshots/" + name, keys.seal_snapshot(manifest))
        self.assertEqual(status, 200)
        return name


class Pairing(Base):
    def test_hello_needs_no_pairing_and_says_who_answers(self):
        status, data = self.phone.raw("GET", "/v1/hello")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["app"], "tuckvault")

    def test_code_pairs_once(self):
        code = server.new_pairing_code()
        status, reply = self.phone.pair(code.lower().replace("-", " "))  # typed sloppily
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.phone.json("GET", "/v1/whoami")[0], 200)
        other = Phone(self.service.port)
        status, reply = other.pair(code)
        self.assertEqual((status, reply["error"]), (403, "no_pairing"))

    def test_wrong_codes_end_the_pairing(self):
        code = server.new_pairing_code()
        for _ in range(server.PAIR_TRIES):
            status, _ = self.phone.pair("AAAA-AAAA")
            self.assertEqual(status, 403)
        status, reply = self.phone.pair(code)  # the right one is now too late
        self.assertEqual((status, reply["error"]), (403, "no_pairing"))

    def test_code_expires(self):
        code = server.new_pairing_code()
        with mock.patch("time.time", return_value=time.time() + server.PAIR_MINUTES * 60 + 5):
            status, reply = self.phone.pair(code)
        self.assertEqual((status, reply["error"]), (403, "no_pairing"))

    def test_without_pairing_nothing_is_served(self):
        self.phone.device, self.phone.secret = "0" * 12, "00" * 32
        status, reply = self.phone.json("GET", "/v1/whoami")
        self.assertEqual((status, reply["error"]), (401, "not_paired"))
        status, _ = self.phone.raw("GET", "/v1/vaults/" + "a" * 24 + "/blobs")
        self.assertEqual(status, 401)

    def test_signature_covers_path_body_and_time(self):
        phone = self.paired()
        self.assertEqual(phone.call("GET", "/v1/whoami", sign_path="/v1/other")[0], 401)
        self.assertEqual(phone.call("GET", "/v1/whoami", secret="11" * 32)[0], 401)
        # a body other than the signed one
        when = str(int(time.time()))
        sign = server.signature(phone.secret, "POST", "/v1/vaults/" + "a" * 24 + "/open", when,
                                hashlib.sha256(b'{"create":false}').hexdigest())
        status, _ = phone.raw("POST", "/v1/vaults/" + "a" * 24 + "/open", b'{"create":true }',
                              {"X-Tuckvault-Device": phone.device, "X-Tuckvault-Time": when,
                               "X-Tuckvault-Sign": sign})
        self.assertEqual(status, 401)
        phone.skew = server.MAX_SKEW + 60
        status, reply = phone.json("GET", "/v1/whoami")
        self.assertEqual((status, reply["error"]), (401, "clock"))

    def test_removed_phone_is_locked_out(self):
        phone = self.paired()
        self.assertTrue(server.remove_device(phone.device))
        self.assertEqual(phone.json("GET", "/v1/whoami")[0], 401)

    def test_secret_is_not_logged_or_listed(self):
        phone = self.paired()
        lines = []
        args = mock.Mock(cmd="phone", action="list", value=None)
        phones.command(args, self.cfg, lines.append)
        self.assertIn(phone.device, " ".join(lines))
        self.assertNotIn(phone.secret, " ".join(lines))


class Vault(Base):
    def test_backup_from_a_phone_restores_on_the_pc_with_its_recovery_key(self):
        keys, base, master = self.new_vault()
        big = os.urandom(engine.CHUNK + 12345)
        files = {"Photos/2026/11/IMG_0001.jpg": os.urandom(50_000), "Photos/2026/11/VID_0002.mp4": big,
                 "Files/Belgeler/şifre notları.txt": "gizli ünïcode".encode("utf-8"), "Files/empty.bin": b""}
        snap = self.backup(self.phone, keys, base, files)
        # what is stored is a normal Tuckvault repository, unreadable without the key
        vault_dir = self.tmp / "repo-phones" / keys.check[:24]
        self.assertTrue((vault_dir / "repo.json").is_file())
        stored = b"".join(p.read_bytes() for p in vault_dir.rglob("*") if p.is_file() and p.name != "lock")
        self.assertNotIn(b"IMG_0001", stored)
        self.assertNotIn("gizli".encode(), stored)

        self.service.vaults.release_idle(older_than=0)
        out = self.tmp / "out"
        result = phones.restore(self.cfg, str(out), crypto.encode_recovery_key(master))
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["snapshot"], snap)
        for path, data in files.items():
            self.assertEqual((out / path).read_bytes(), data, path)

    def test_wrong_recovery_key_finds_nothing(self):
        keys, base, _ = self.new_vault()
        self.backup(self.phone, keys, base, {"Photos/a.jpg": b"x" * 100})
        self.service.vaults.release_idle(older_than=0)
        with self.assertRaises(engine.BackupError):
            phones.restore(self.cfg, str(self.tmp / "out"),
                           crypto.encode_recovery_key(crypto.new_master_key()))

    def test_second_backup_only_uploads_what_is_new_and_download_works(self):
        keys, base, _ = self.new_vault()
        a, b = os.urandom(30_000), os.urandom(40_000)
        self.backup(self.phone, keys, base, {"Photos/a.jpg": a})
        before = len(self.phone.call("GET", base + "/blobs")[1].split(b"\n"))
        snap = self.backup(self.phone, keys, base, {"Photos/a.jpg": a, "Photos/b.jpg": b})
        after = len(self.phone.call("GET", base + "/blobs")[1].split(b"\n"))
        self.assertEqual(after, before + 1)
        status, listing = self.phone.json("GET", base + "/snapshots")
        self.assertEqual(len(listing["snapshots"]), 2)
        status, raw = self.phone.call("GET", base + "/snapshots/" + snap)
        manifest = keys.open_snapshot(raw)
        entry = next(f for f in manifest["files"] if f["p"] == "Photos/b.jpg")
        status, blob = self.phone.call("GET", base + "/blobs/" + entry["c"][0])
        self.assertEqual(keys.open_chunk(entry["c"][0], blob), b)

    def test_damaged_upload_is_refused(self):
        keys, base, _ = self.new_vault()
        name, blob = keys.seal_chunk(b"hello" * 1000)
        status, reply = self.phone.json("PUT", base + "/blobs/" + name, None)
        status, data = self.phone.call("PUT", base + "/blobs/" + name, blob[:-1] + b"\x00")
        self.assertEqual((status, json.loads(data)["error"]), (400, "damaged"))
        status, reply = self.phone.json("POST", base + "/verify", {"names": [name], "full": True})
        self.assertEqual(reply["missing"], [name])

    def test_verify_and_cleanup(self):
        keys, base, _ = self.new_vault()
        name, blob = keys.seal_chunk(os.urandom(1000))
        self.assertEqual(self.phone.call("PUT", base + "/blobs/" + name, blob)[0], 200)
        status, reply = self.phone.json("POST", base + "/verify", {"names": [name], "full": True})
        self.assertEqual((reply["missing"], reply["corrupt"]), ([], []))
        blob_file = self.tmp / "repo-phones" / keys.check[:24] / "blobs" / name[:2] / name
        blob_file.write_bytes(b"rotten")
        status, reply = self.phone.json("POST", base + "/verify", {"names": [name], "full": True})
        self.assertEqual(reply["corrupt"], [name])
        status, reply = self.phone.json("POST", base + "/delete-blobs", {"names": [name]})
        self.assertEqual(reply["deleted"], 1)
        self.assertFalse(blob_file.exists())

    def test_snapshot_cannot_be_replaced_by_accident_but_can_be_deleted(self):
        keys, base, _ = self.new_vault()
        snap = self.backup(self.phone, keys, base, {"Photos/a.jpg": b"abc" * 100})
        status, data = self.phone.call("PUT", base + "/snapshots/" + snap, b"other")
        self.assertEqual((status, json.loads(data)["error"]), (409, "exists"))
        self.assertEqual(self.phone.call("DELETE", base + "/snapshots/" + snap)[0], 200)
        self.assertEqual(self.phone.json("GET", base + "/snapshots")[1]["snapshots"], [])
        status, data = self.phone.call("GET", base + "/snapshots/" + snap)
        self.assertEqual((status, json.loads(data)["error"]), (404, "missing"))

    def test_vault_name_must_belong_to_the_key(self):
        phone = self.paired()
        keys = crypto.Keys(crypto.new_master_key())
        base = "/v1/vaults/" + "ab" * 12  # not this key's vault
        phone.json("POST", base + "/open", {"create": True})
        meta = {"format": 1, "id": secrets.token_hex(8), "created": engine.now_iso(), "key_check": keys.check}
        self.assertEqual(phone.json("POST", base + "/init", {"meta": meta})[0], 400)

    def test_names_cannot_leave_the_vault(self):
        keys, base, _ = self.new_vault()
        for path in (base + "/blobs/..%2F..%2Frepo.json", base + "/snapshots/..", base + "/blobs/" + "A" * 64,
                     "/v1/vaults/../x/open", base + "/snapshots/a%00.snap"):
            status, _ = self.phone.call("GET", path)
            self.assertIn(status, (400, 404), path)
        self.assertFalse((self.tmp / "repo.json").exists())

    def test_ops_on_a_vault_that_does_not_exist(self):
        phone = self.paired()
        status, reply = phone.json("GET", "/v1/vaults/" + "c" * 24 + "/snapshots")
        self.assertEqual((status, reply["error"]), (404, "no_vault"))
        self.assertFalse((self.tmp / "repo-phones" / ("c" * 24)).exists())

    def test_too_large_upload_is_refused_without_reading_it(self):
        keys, base, _ = self.new_vault()
        status, _ = self.phone.raw("PUT", base + "/blobs/" + "a" * 64, b"",
                                   {"Content-Length": str(server.MAX_BLOB + 1)})
        self.assertEqual(status, 413)

    def test_two_phones_share_a_vault_with_the_same_key(self):
        keys, base, master = self.new_vault()
        snap = self.backup(self.phone, keys, base, {"Photos/a.jpg": b"abc" * 100})
        new_phone = Phone(self.service.port)
        self.assertEqual(new_phone.pair(server.new_pairing_code(), "Replacement")[0], 200)
        status, info = new_phone.json("POST", base + "/open", {"create": False})
        self.assertEqual(info["meta"]["key_check"], keys.check)
        self.assertEqual(new_phone.json("GET", base + "/snapshots")[1]["snapshots"][0]["name"], snap)

    def test_vault_is_released_for_other_programs_when_idle(self):
        keys, base, master = self.new_vault()
        self.backup(self.phone, keys, base, {"Photos/a.jpg": b"abc" * 100})
        cfg = dict(self.cfg, _key=master, destination=server.vault_destination(
            server.phones_root(self.cfg), keys.check[:24]))
        with self.assertRaises(engine.BackupError):  # the service is still holding it
            engine.list_snapshots(cfg)
        self.service.vaults.release_idle(older_than=0)
        self.assertEqual(len(engine.list_snapshots(cfg)), 1)
        # and the service picks it up again afterwards
        self.assertEqual(self.phone.json("GET", base + "/snapshots")[0], 200)


class SshStyleStorage(Base):
    """The same service in front of the agent started as a separate process (the SSH code path)."""

    def setUp(self):
        super().setUp()
        self.service.stop()
        self.cfg["destination"] = {"type": "process", "path": str(self.tmp / "remote" / "vault")}
        self.service = server.Service(self.cfg, listen="127.0.0.1", port_number=self._free_port()).start()
        self.phone = Phone(self.service.port)

    def test_backup_and_restore_through_a_remote_agent(self):
        keys, base, master = self.new_vault()
        data = os.urandom(200_000)
        self.backup(self.phone, keys, base, {"Photos/x.jpg": data})
        self.assertTrue((self.tmp / "remote" / "vault-phones" / keys.check[:24] / "repo.json").is_file())
        self.service.vaults.release_idle(older_than=0)
        result = phones.restore(self.cfg, str(self.tmp / "out"), crypto.encode_recovery_key(master))
        self.assertEqual(result["status"], "success", result)
        self.assertEqual((self.tmp / "out" / "Photos" / "x.jpg").read_bytes(), data)

    def test_service_survives_the_storage_connection_dying(self):
        keys, base, _ = self.new_vault()
        vault = self.service.vaults.get(keys.check[:24])
        vault.remote.proc.kill()
        vault.remote.proc.wait()
        self.assertEqual(self.phone.json("GET", base + "/snapshots")[0], 200)


class Settings(Base):
    def test_where_phone_backups_go(self):
        self.assertEqual(server.phones_root({"destination": {"type": "ssh", "host": "laptop", "path": "vault"}}),
                         {"type": "ssh", "host": "laptop", "path": "vault-phones"})
        with self.assertRaises(server.ServeError):
            server.phones_root({"destination": {"type": "ssh", "host": "", "path": "x"}})
        with self.assertRaises(ValueError):
            server.vault_destination({"type": "local", "folder": "x"}, "../evil")

    def test_tailscale_address_range(self):
        self.assertTrue(server._is_tailscale("100.64.0.1"))
        self.assertTrue(server._is_tailscale("100.127.255.254"))
        self.assertFalse(server._is_tailscale("100.128.0.1"))
        self.assertFalse(server._is_tailscale("192.168.1.5"))

    def test_status_and_pair_commands(self):
        lines = []
        with mock.patch.object(server, "port", return_value=self.service.port), \
                mock.patch.object(server, "tailscale_addresses", return_value=["100.101.102.103"]), \
                mock.patch.object(server, "tailscale_name", return_value="pc.tail1234.ts.net"), \
                mock.patch.object(scheduler, "phones_enabled", return_value=False):
            code = phones.command(mock.Mock(cmd="phone", action="pair", value=None), self.cfg, lines.append)
        self.assertEqual(code, 0, lines)
        text = "\n".join(lines)
        self.assertIn("http://pc.tail1234.ts.net:%d" % self.service.port, text)
        shown = next(line.split()[-1] for line in lines if "Code:" in line)
        self.assertEqual(self.phone.pair(shown)[0], 200)

    def test_autostart_definitions(self):
        xml = scheduler.phones_xml()
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("serve", xml)
        self.assertNotIn("PT12H", xml)
        unit = scheduler.phones_unit()
        self.assertIn("Restart=on-failure", unit)
        self.assertIn('"serve"', unit)
        self.assertFalse(scheduler.phones_task().startswith(scheduler.task_name() + "-"))
        self.assertNotEqual(scheduler.phones_task(), scheduler.task_name())


if __name__ == "__main__":
    unittest.main()
