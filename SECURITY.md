# Security

## What Tuckvault assumes

- **Your PC and Windows account are trusted.** The backup key is stored in
  `%APPDATA%\Tuckvault\key.bin`, wrapped with Windows DPAPI so that only
  your Windows account can unwrap it. That is what lets scheduled backups
  run without asking for a password.
- **The backup destination is not trusted** with the content of your
  files. It may be read by others, lose data, or return wrong data;
  Tuckvault is built to keep the content private and to notice the rest.

## What is protected

| | |
| --- | --- |
| Content and names of your files | AES-256-GCM, encrypted on the PC. Keys for file data, for backup indexes and for nonces are derived separately from one 256-bit master key. |
| Integrity of stored data | Each piece is named by the SHA-256 of its ciphertext and authenticated by GCM. The destination re-hashes on a schedule; the PC re-verifies every byte on restore. |
| Integrity of a backup's file list | Encrypted and authenticated; it also carries its own name, so an old backup cannot be presented as a newer one. |
| Removal of backups | Tuckvault remembers which backups it has seen, reports any that disappear without it having removed them, and puts their index back if the data is still there. |
| A swapped or emptied destination | Tuckvault remembers which set of backups belongs to a destination and refuses to carry on if it finds an empty or different one, until you say so. |
| What the destination sends back | Treated as untrusted: names, sizes and identifiers are validated before they are used on the PC. |
| The connection | SSH with strict host key checking and no agent or port forwarding. Tuckvault never accepts an unknown host by itself and never sends a password. |

## What is not protected

- **Anything running as your Windows user** can use the key, read your
  settings and delete backups at the destination (the SSH login and folder
  access are allowed to). A destination that keeps its own versions or
  snapshots, or a login that can add but not delete, closes that gap.
- **Sizes and repetition are visible to the destination.** It can see
  roughly how much data you have, when it changes, and that two stored
  pieces are identical. It cannot see names or content.
- **Losing both the PC and the recovery key** makes the backups
  unreadable. There is no back door.
- **A destination that goes back to an older state as a whole** (for
  example a restored disk image) is noticed only as "backups disappeared".
- **The destination checks itself.** The regular re-hash runs at the
  destination, so a destination that lies about it is only caught when
  data is read back during a restore, where every byte is verified on the
  PC.
- **Backups made by 1.0.0** carry no name inside; for those only the time
  in the name is checked against the time inside.
- **The recovery key file**, if you save one, is plain text. Keep it
  somewhere private that is neither the PC nor the destination.
- Downloads are **not code-signed** yet. Compare them against
  `SHA256SUMS.txt` from the same release.

## What Tuckvault sends anywhere

Your backup data goes to the destination you chose and nowhere else. The
only other connection is the optional version check: when the window opens
it asks `api.github.com` for the newest release number, at most once a
day. It sends nothing about you or your files, downloads nothing, and can
be switched off on the Security tab. Scheduled backups never do it.

## Backups from phones

- A phone's backups are encrypted on the phone with a key that never reaches
  the computer, in the same format as the PC's own. The computer that
  receives them, and the machine that stores them, see only unreadable data.
- The receiving service (`tuckvault serve`) listens on the computer itself
  and on its Tailscale address only. It speaks plain HTTP: privacy on the way
  comes from Tailscale, which encrypts everything between its devices.
- A phone is paired with a one-time code (8 characters, valid ten minutes,
  five attempts). The reply carries that phone's own secret; afterwards every
  request is signed with it and the secret is never sent again.
- What a paired phone can do: store, read and delete data in phone vaults on
  that computer. It cannot read another vault without that vault's key, and it
  cannot reach the PC's own backups or files.
- What this does not protect against: someone on your Tailscale network who
  watches the one pairing exchange, or who holds a paired phone's secret, can
  delete that phone's backups or fill the disk. They still cannot read them.
  Remove a lost phone in the Phones tab.
- A signed request stays valid for a few minutes, so someone who can record
  the traffic could send the same request again in that time. Inside
  Tailscale nobody can record it; do not run `tuckvault serve --listen` on an
  address outside Tailscale.
- On the phone the key and the pairing secret are kept in the system's secure
  storage (Android Keystore, iOS Keychain), readable only by the app, and are
  left out of device backups and transfers.

## Known limitations

- The phone service has no limit on how much a paired phone may store.

- On Linux the key file on your computer (`~/.config/tuckvault/key.bin`) is
  protected only by its permissions (0600, readable by you alone), not
  encrypted by the operating system as Windows DPAPI does. Anyone who can
  read your files as you, or as root, can read it. Tuckvault refuses a key
  file that other users can read.

- The daily check for backups that stopped happening is itself a
  scheduled task. If Windows Task Scheduler is not running at all, nothing
  can raise the alarm; the Backup tab still shows the last successful
  backup and an "Overdue" line.
- A backup computer that stalls without closing the connection can hold a
  backup run for a long time before it gives up.
- `tuckvault-cli key import <key>` takes the key on the command line, where
  shell history can keep it, and the Copy button puts it on the clipboard.
  Prefer the import dialog in the window.
- One backup can list roughly a million files; beyond that the index is
  too large for the destination to accept.
- Backups whose index cannot be read (and cannot be put back from the PC's
  copy) are removed once they are older than the newest N.

## Checks before every release

The release workflow runs the test suite (including damage, tampering,
wrong-key and recovery scenarios), a static scan with `bandit`, a
known-vulnerability audit of dependencies with `pip-audit`, and installs
and exercises the built installer before publishing.

## Reporting a problem

Report it privately through
[a security advisory](https://github.com/karacanil/tuckvault/security/advisories/new).
Please do not put details in a public issue. You can expect a first answer
within a week.
