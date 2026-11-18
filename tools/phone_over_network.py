"""Plays a phone from another computer, to try the phone service across a real network link.

Run on a second machine that can reach the computer running `tuckvault serve`:

    python3 tools/phone_over_network.py http://<address>:8743 <pairing code>

It pairs, makes a vault with a new key, uploads a few files as one backup the way
the app does (signed requests, encrypted pieces), and prints the recovery key and
the SHA-256 of every file, so the receiving computer can restore the backup with
`tuckvault phone restore` and compare. Needs only this repository and the
`cryptography` package; nothing is installed or left running.
"""

import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuckvault import crypto, engine, server  # noqa: E402


class Phone:
    def __init__(self, url):
        self.url, self.device, self.secret = url.rstrip("/"), None, None

    def raw(self, method, path, body=b"", headers=None):
        req = urllib.request.Request(self.url + path, data=body or None, method=method, headers=headers or {})
        try:
            # The address is given by the person running this test.
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def call(self, method, path, body=b""):
        when = str(int(time.time()))
        sign = server.signature(self.secret, method, path, when, hashlib.sha256(body).hexdigest())
        status, data = self.raw(method, path, body, {"X-Tuckvault-Device": self.device, "X-Tuckvault-Time": when,
                                                     "X-Tuckvault-Sign": sign})
        if status != 200:
            raise SystemExit("%s %s -> %d %s" % (method, path, status, data[:300]))
        return data


def main():
    url, code = sys.argv[1], sys.argv[2]
    phone = Phone(url)
    status, data = phone.raw("GET", "/v1/hello")
    print("hello:", status, data.decode())
    status, data = phone.raw("POST", "/v1/pair", json.dumps(
        {"code": code, "name": "Network test from " + os.uname().nodename, "platform": "test"}).encode())
    if status != 200:
        raise SystemExit("pairing failed: %d %s" % (status, data))
    reply = json.loads(data)
    phone.device, phone.secret = reply["device"], reply["secret"]

    master = crypto.new_master_key()
    keys = crypto.Keys(master)
    base = "/v1/vaults/" + keys.check[:24]
    phone.call("POST", base + "/open", json.dumps({"create": True}).encode())
    meta = {"format": 1, "id": secrets.token_hex(8), "created": engine.now_iso(), "key_check": keys.check}
    phone.call("POST", base + "/init", json.dumps({"meta": meta}).encode())

    files = {"Photos/2026/11/IMG_0001.jpg": os.urandom(400_000),
             "Photos/2026/11/VID_0002.mp4": os.urandom(engine.CHUNK * 2 + 12345),
             "Files/Notes/note.txt": ("gizli not ✓\n" * 500).encode("utf-8")}
    entries, sent, started = [], 0, time.time()
    for path, content in files.items():
        chunks = []
        for i in range(0, len(content), engine.CHUNK):
            name, blob = keys.seal_chunk(content[i:i + engine.CHUNK])
            phone.call("PUT", base + "/blobs/" + name, blob)
            sent += len(blob)
            chunks.append(name)
        entries.append({"p": path, "s": len(content), "m": time.time_ns(), "c": chunks})
    name = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + ".000000Z-" + secrets.token_hex(3) + ".snap"
    manifest = {"v": 1, "name": name, "created": engine.now_iso(), "host": "network-test", "sources": ["Photos"],
                "files": entries, "stats": {"files": len(entries), "bytes": sum(len(c) for c in files.values())},
                "warnings": []}
    phone.call("PUT", base + "/snapshots/" + name, keys.seal_snapshot(manifest))
    wanted = sorted({b for e in entries for b in e["c"]})
    verdict = json.loads(phone.call("POST", base + "/verify", json.dumps({"names": wanted, "full": True}).encode()))
    assert not (verdict["missing"] or verdict["corrupt"] or verdict["unreadable"]), verdict
    seconds = time.time() - started
    print("sent %d bytes in %.1f s (%.1f MB/s), all pieces verified on the other side"
          % (sent, seconds, sent / 1e6 / max(seconds, 0.01)))
    print("RESULT " + json.dumps({"device": phone.device, "recovery_key": crypto.encode_recovery_key(master),
                                  "sha256": {p: hashlib.sha256(c).hexdigest() for p, c in files.items()}}))


if __name__ == "__main__":
    main()
