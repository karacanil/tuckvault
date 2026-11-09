"""Stores the master key on this PC.

On Windows the key file is wrapped with DPAPI, so it can only be unwrapped
by the same Windows user account. Scheduled backups can therefore run
unattended without a passphrase, while a copy of the file is useless to
anyone else. The recovery key (see crypto.encode_recovery_key) is the only
way to read the backups if this Windows account is lost.
"""

import os
import sys

from . import crypto, paths

_MAGIC_DPAPI = b"SKK1-DPAPI\n"
_MAGIC_RAW = b"SKK1-RAW\n"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):
        raise OSError("Windows could not %s the backup key" % ("protect" if protect else "unlock"))
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)


def load():
    """Return the master key, or None if this PC has none yet."""
    try:
        raw = paths.key_file().read_bytes()
    except FileNotFoundError:
        return None
    if raw.startswith(_MAGIC_DPAPI):
        return _dpapi(raw[len(_MAGIC_DPAPI):], protect=False)
    if raw.startswith(_MAGIC_RAW):
        return raw[len(_MAGIC_RAW):]
    raise OSError("The key file on this PC is not recognised.")


def store(master: bytes) -> None:
    if len(master) != crypto.KEY_LEN:
        raise ValueError("bad key length")
    if sys.platform == "win32":
        raw = _MAGIC_DPAPI + _dpapi(master, protect=True)
    else:
        raw = _MAGIC_RAW + master
    path = paths.key_file()
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_or_create() -> bytes:
    master = load()
    if master is None:
        master = crypto.new_master_key()
        store(master)
    return master


def recovery_key():
    master = load()
    return crypto.encode_recovery_key(master) if master else None


def import_recovery_key(text: str) -> None:
    store(crypto.decode_recovery_key(text))
