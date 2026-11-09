# Safekeep

Small automatic backups from this Windows PC to another machine over SSH
(for example a spare laptop reachable as `ssh laptop`).

You pick files and folders and how often to back them up. Windows starts the
backup on schedule; nothing of Safekeep keeps running in between. You hear
from it only when a backup fails.

## Start it

Double-click `Safekeep.pyw`, or:

```
.venv\Scripts\pythonw.exe -m safekeep
```

1. **Backup tab**: add folders/files, choose how often, click **Save settings**
   (this registers the automatic run with Windows), then **Back up now** once.
2. **Security tab**: save the recovery key somewhere that is neither this PC
   nor the backup machine. Without it the backups are unreadable if this PC
   or Windows account is lost.
3. **Restore tab**: list the stored backups, restore one into a folder, or
   re-check everything that is stored.

## How the backups are protected

| Threat | Protection |
| --- | --- |
| Someone reads the backup machine's disk | Everything is AES-256-GCM encrypted on this PC before upload, including file names. The backup machine never has the key. |
| Disk damage or tampering on the backup machine | Every stored piece is named by the SHA-256 of its content. The backup machine re-hashes all of them every 7 days (and on demand) and any mismatch is reported. Damaged pieces are dropped so the next backup re-sends them. |
| Damage in transit | The receiving side refuses any piece whose hash does not match. |
| A file deleted or overwritten by mistake | Each run is a separate snapshot; the last 30 are kept. |
| A copy of the key file leaks | The key file is locked to this Windows account with DPAPI. |
| Half-finished backup (power cut, cable pulled) | Pieces are written to a temp file, synced, then renamed. A snapshot only appears after all its data is confirmed present. |

Not covered in 1.0: an attacker who controls this Windows account can still
delete the backups (the SSH login is allowed to), and the backup machine's
disk itself is not redundant.

## How it works

```
safekeep/
  gui.py        the window (tkinter)
  engine.py     scan, chunk, de-duplicate, upload, verify, prune, restore
  crypto.py     encryption, hashing, recovery key
  keystore.py   key file on this PC (DPAPI)
  remote.py     talks to the storage agent over ssh
  agent.py      runs on the backup machine; standard library only
  scheduler.py  Windows Task Scheduler integration
  notify.py     Windows notification on failure
```

Files are cut into 4 MiB chunks. Each chunk is encrypted and stored once
under the hash of its ciphertext, so unchanged files cost nothing on later
runs and identical data is stored once. A snapshot is an encrypted list of
files and the chunks they consist of.

The backup machine needs only `python3` and an SSH login. Nothing is
installed there: the agent's source is sent over the SSH connection each
time and stores data under `~/safekeep-repo`.

Settings, history, log and the key live in `%APPDATA%\Safekeep`.

## Command line

```
python -m safekeep run            back up now
python -m safekeep verify         re-hash everything stored
python -m safekeep snapshots      list backups
python -m safekeep restore latest D:\restore-here
python -m safekeep schedule apply|remove|status
python -m safekeep key show|import <key>
python -m safekeep test           test the connection
```

## Development

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests
```

The tests run the real engine and storage agent against a repository in a
temp folder, including damage, tampering, wrong-key and recovery scenarios,
and drive the real window through a backup, restore and check.
