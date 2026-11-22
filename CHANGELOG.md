# Changelog

The phone app has its own version numbers and notes: see
[mobile/CHANGELOG.md](mobile/CHANGELOG.md).

## 1.6.1

- A selected folder or file that is missing at backup time (an unplugged
  drive, say) no longer uses up its "keep the last" places. Until now every
  backup counted for every selected item, found or not, so after enough
  nights the last copies of a missing item were removed. Now only backups
  that really hold something from an item count for it, older backups
  included, and the run says plainly that nothing from that item was backed
  up.
- An item that was not backed up no longer counts as backed up for the
  overdue warning.
- Importing a recovery key that differs from the PC's own key now asks
  first, in the window. `tuckvault key import` refuses it unless
  `--replace` is added after the key. Phone backups are opened with
  **Restore a phone's backup** (or `tuckvault phone restore`) and need no
  import.
- The "recovery key not saved" banner opens the Security tab again (it
  opened Phones).

## 1.6.0

- Pairing a phone is now one scan. **Pair a phone…** (and `tuckvault phone
  pair`) shows a square code; pointing the phone's camera at it opens the app
  with everything filled in, and one tap connects. The address and short code
  can still be typed. Both now stay valid for 30 minutes instead of 10.
- The phone is told exactly why a pairing was refused (expired, already used,
  too many wrong codes, how many tries are left), and refusals are written to
  the log, never with the code.
- Wrong short codes no longer cancel the whole pairing, only the short code.
- `tuckvault phone pair --qr FILE` saves the code as a picture. Drawing the
  code uses the small `segno` library; without it pairing works by typing.

## 1.5.0

**New**

- Backups from phones. The new Tuckvault app for Android and iPhone (see
  [mobile/README.md](mobile/README.md)) backs up photos, videos and files to
  this computer over Tailscale. The Phones tab switches it on, pairs a phone
  with a one-time code, lists paired phones and restores a phone's backup
  onto this computer. The same from the command line: `tuckvault serve`,
  `tuckvault phone pair|list|remove|enable|disable|status|restore`.
- Each phone encrypts its own backups with its own key, in the same format as
  the PC's. They are stored next to the PC's backups (in
  `<destination>-phones`), on a folder or on another computer over SSH, and
  this computer cannot read them.

**Security**

- The phone service listens only on this computer itself and on its
  Tailscale address. Requests from a phone are signed with the secret agreed
  at pairing; unpaired callers are turned away before any upload is read.

## 1.4.0

**New**

- Linux support. Automatic backups use systemd user timers, notifications
  use `notify-send`, and the window works the same as on Windows. The
  release has a `Tuckvault-x.y.z-linux.tar.gz` with an install script that
  needs no root (`sh tools/install-linux.sh`). See "Linux" in the README.
- On Linux the key file is refused if other users can read it.

**Changed**

- Every system program Tuckvault starts (ssh, notify-send, systemctl,
  xdg-open) is taken from the system folders by full path.
- CI also runs every test on Linux.

## 1.3.0

**Renamed to Tuckvault.** Until 1.2 this program was called Safekeep; the
old name was already used by another backup tool. Nothing else changes.

- Your settings, key and history are taken over the first time Tuckvault
  starts (`%APPDATA%\Safekeep` becomes `%APPDATA%\Tuckvault`), and
  existing backups stay readable.
- Automatic backups registered by an older version are re-registered under
  the new name when you open the window.
- The programs are now `Tuckvault.exe` and `tuckvault-cli.exe`. If you
  installed Safekeep with its installer, uninstall it and install
  Tuckvault; your settings and backups are kept.
- The entries below keep the old name, as they were written.

## 1.2.0

**New**

- Restore chosen files: search inside a backup and take out single files
  or folders instead of the whole backup (`--only` on the command line,
  and `safekeep-cli files` lists what a backup contains).
- An alarm when backups quietly stop happening. Once a day Windows checks
  that every automatically backed-up item had a successful backup within
  twice its interval, and notifies you if not. The Backup tab shows an
  "Overdue" line as well.
- A notice when a newer version is available, with a link to the download
  page. Nothing is downloaded by itself; it can be switched off on the
  Security tab.
- Damaged settings are announced instead of silently replaced by defaults.

**Changed**

- "Restore selected…" is now "Restore everything…".
- Released under the MIT licence.
- Dependencies are pinned to exact versions for repeatable builds.

## 1.1.0

First packaged release.

**New**

- Each file or folder can have its own "how often": follow the general
  schedule, have its own, or only be backed up by hand.
- Backups can go to a folder or drive (USB disk, network share,
  cloud-synced folder) as well as to another computer over SSH.
- Windows installer and portable zip; Safekeep has its own icon in the
  window, taskbar and Start menu.
- The Restore tab shows what each backup contains.

**Safer**

- A backup that is put back under another name at the destination is
  rejected, and backups that disappear from the destination without
  Safekeep removing them are reported.
- The newest backups are kept per item, so an item backed up every hour
  cannot push out the backups of one backed up every month.
- Two scheduled backups due at the same minute now run one after the
  other instead of the second being skipped.
- If the destination is suddenly empty or holds a different set of
  backups (a disk that is not mounted, another drive on the same letter),
  Safekeep stops and says so instead of quietly starting over.
- The regular check now also compares every stored backup index with the
  copy on the PC and puts back any that were damaged or lost.
- Damaged data is moved aside at the destination instead of deleted, and
  data that merely could not be read at that moment is left alone.
- The backup just made is never removed by clean-up, even if the PC's
  clock went backwards.
- Folder destinations are locked while in use, so two PCs or users cannot
  clean up under each other.
- Everything the destination sends back is checked before use: names
  cannot point outside Safekeep's own folder, replies are size-limited,
  and error text is shown as plain, bounded text.
- SSH connections never forward your SSH agent or ports to the backup
  computer and always insist on a known host key. Windows system programs
  are started by full path.
- Restoring can never write outside the folder you chose.
- An independent code review was done before this release. Its high and
  medium findings are fixed and covered by tests; the remaining minor
  ones are listed under "Known limitations" in SECURITY.md.

**Changed**

- "Keep the last N backups" now counts per item.
- Existing settings and backups from 1.0.0 keep working unchanged.

## 1.0.0

- Choose files and folders, back them up on a schedule to another computer
  over SSH.
- AES-256-GCM encryption on the PC, SHA-256 content addressing, weekly
  re-check of stored data with automatic repair.
- Restore, backup history, recovery key, failure notifications.
