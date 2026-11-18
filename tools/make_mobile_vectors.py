"""Writes the test vectors the mobile app's native code is checked against.

    python tools/make_mobile_vectors.py

The Kotlin (Android), Swift (iOS) and TypeScript tests all read
mobile/modules/tuckvault-core/test-vectors.json and must open every blob and
snapshot in it, so the phone and the PC stay able to read each other's backups.
tests/test_mobile_vectors.py checks that the file still matches this program.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from tuckvault import crypto, server  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "mobile" / "modules" / "tuckvault-core" / "test-vectors.json"


def _bytes(seed: str, n: int) -> bytes:
    """Reproducible bytes that do not compress."""
    out = b""
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}/{counter}".encode()).digest()
        counter += 1
    return out[:n]


def build() -> dict:
    master = bytes(range(32))
    keys = crypto.Keys(master)
    chunks = []
    for label, data in [
        ("one byte", b"x"),
        ("short text, stored as it is", "şifre: gizli ünïcode ✓".encode("utf-8")),
        ("511 bytes, below the size where compression is tried", b"A" * 511),
        ("text that compresses", b"Tuckvault keeps your files safe. " * 400),
        ("bytes that do not compress", _bytes("random", 3000)),
        ("compressible start, larger than the 64 KiB sample", b"abc" * 30000 + _bytes("tail", 5000)),
    ]:
        name, blob = keys.seal_chunk(data)
        chunks.append({"label": label, "data": data.hex(), "name": name, "blob": blob.hex(),
                       "compressed": blob[4] == 1})
    manifest = {"v": 1, "name": "20261117T101500.000000Z-abc123.snap", "created": "2026-11-17T10:15:00Z",
                "host": "Pixel 9", "sources": ["Photos"],
                "files": [{"p": "Photos/2026/11/IMG_0001.jpg", "s": 1, "m": 1763374500000000000,
                           "c": [chunks[0]["name"]]},
                          {"p": "Files/Belgeler/şifre notları.txt", "s": 26, "m": 1763374501000000000,
                           "c": [chunks[1]["name"]]}],
                "stats": {"files": 2, "bytes": 27}, "warnings": []}
    text = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False)
    nonce = bytes(range(100, 112))
    import zlib
    snap_key = crypto._derive(master, b"snapshot-encryption")
    raw = crypto.SNAP_MAGIC + nonce + AESGCM(snap_key).encrypt(nonce, zlib.compress(text.encode("utf-8"), 6),
                                                              crypto.SNAP_MAGIC)
    assert keys.open_snapshot(raw) == manifest
    secret = hashlib.sha256(b"pairing secret").hexdigest()
    body = b'{"create":false}'
    return {
        "about": "Made by tools/make_mobile_vectors.py. Do not edit by hand.",
        "master": master.hex(),
        "key_check": keys.check,
        "vault": keys.check[:24],
        "recovery_key": crypto.encode_recovery_key(master),
        "chunks": chunks,
        "snapshot": {"json": text, "nonce": nonce.hex(), "raw": raw.hex()},
        "signature": {"secret": secret, "method": "POST", "target": "/v1/vaults/%s/open" % keys.check[:24],
                      "time": 1763374500, "body": body.decode(), "body_sha256": hashlib.sha256(body).hexdigest(),
                      "sign": server.signature(secret, "POST", "/v1/vaults/%s/open" % keys.check[:24],
                                               1763374500, hashlib.sha256(body).hexdigest())},
        "bad_recovery_keys": {"typo": crypto.encode_recovery_key(master)[:-1] + "A",
                              "short": "ABCD-EFGH"},
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", OUT)
