"""Encryption and integrity for everything that leaves this PC.

* Every chunk of file data is AES-256-GCM encrypted before upload. The
  stored blob is named by the SHA-256 of its ciphertext, so the storage
  machine can prove a blob is undamaged without being able to read it.
* The nonce is derived from the data with a keyed HMAC (a synthetic IV).
  Identical chunks therefore produce identical blobs, which gives
  de-duplication without ever reusing a nonce for different data.
* Snapshot manifests (the list of files and the blobs they are made of)
  are encrypted and authenticated too, so nobody without the key can
  read file names or swap one file's content for another's.
"""

import base64
import hashlib
import hmac
import json
import os
import zlib

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BLOB_MAGIC = b"SKB1"
SNAP_MAGIC = b"SKS1"
KEY_LEN = 32


class IntegrityError(Exception):
    """Stored data is damaged, tampered with, or was written with another key."""


def _derive(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, b"safekeep/v1/" + label, hashlib.sha256).digest()


def new_master_key() -> bytes:
    return os.urandom(KEY_LEN)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Keys:
    def __init__(self, master: bytes):
        if len(master) != KEY_LEN:
            raise ValueError("master key must be 32 bytes")
        self._chunk = AESGCM(_derive(master, b"chunk-encryption"))
        self._siv = _derive(master, b"chunk-nonce")
        self._snap = AESGCM(_derive(master, b"snapshot-encryption"))
        # Stored in the repository so a wrong key is detected up front.
        self.check = _derive(master, b"key-check").hex()

    # -- file data -------------------------------------------------------
    def seal_chunk(self, data: bytes):
        """Return (blob_name, blob) for one chunk of file content."""
        flag, body = 0, data
        if len(data) >= 512:
            sample = data[:65536]
            if len(zlib.compress(sample, 1)) < 0.9 * len(sample):
                comp = zlib.compress(data, 3)
                if len(comp) < len(data):
                    flag, body = 1, comp
        header = BLOB_MAGIC + bytes([flag])
        nonce = hmac.new(self._siv, header + body, hashlib.sha256).digest()[:12]
        blob = header + nonce + self._chunk.encrypt(nonce, body, header)
        return sha256_hex(blob), blob

    def open_chunk(self, name: str, blob: bytes) -> bytes:
        if sha256_hex(blob) != name:
            raise IntegrityError(f"blob {name[:12]}… does not match its hash")
        if len(blob) < 5 + 12 + 16 or blob[:4] != BLOB_MAGIC:
            raise IntegrityError(f"blob {name[:12]}… is not a Safekeep blob")
        header, nonce, ct = blob[:5], blob[5:17], blob[17:]
        try:
            body = self._chunk.decrypt(nonce, ct, header)
        except InvalidTag:
            raise IntegrityError(f"blob {name[:12]}… failed authentication (wrong key?)")
        return zlib.decompress(body) if header[4] == 1 else body

    # -- manifests -------------------------------------------------------
    def seal_snapshot(self, manifest: dict) -> bytes:
        body = zlib.compress(json.dumps(manifest, separators=(",", ":")).encode("utf-8"), 6)
        nonce = os.urandom(12)
        return SNAP_MAGIC + nonce + self._snap.encrypt(nonce, body, SNAP_MAGIC)

    def open_snapshot(self, raw: bytes) -> dict:
        if len(raw) < 4 + 12 + 16 or raw[:4] != SNAP_MAGIC:
            raise IntegrityError("not a Safekeep snapshot")
        try:
            body = self._snap.decrypt(raw[4:16], raw[16:], SNAP_MAGIC)
        except InvalidTag:
            raise IntegrityError("snapshot failed authentication (damaged, or wrong key)")
        return json.loads(zlib.decompress(body))


# -- recovery key (what the user writes down) -----------------------------
def encode_recovery_key(master: bytes) -> str:
    raw = master + hashlib.sha256(master).digest()[:3]
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def decode_recovery_key(text: str) -> bytes:
    clean = "".join(c for c in text.upper() if c.isalnum())
    try:
        raw = base64.b32decode(clean + "=" * (-len(clean) % 8))
    except Exception:
        raise ValueError("That is not a valid recovery key.")
    if len(raw) != KEY_LEN + 3 or hashlib.sha256(raw[:KEY_LEN]).digest()[:3] != raw[KEY_LEN:]:
        raise ValueError("That recovery key has a typo in it (checksum mismatch).")
    return raw[:KEY_LEN]
