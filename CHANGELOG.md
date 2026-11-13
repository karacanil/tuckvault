# Changelog

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
- An independent code review was done before this release; its findings
  are fixed and covered by tests.

**Changed**

- "Keep the last N backups" now counts per item.
- Existing settings and backups from 1.0.0 keep working unchanged.

## 1.0.0

- Choose files and folders, back them up on a schedule to another computer
  over SSH.
- AES-256-GCM encryption on the PC, SHA-256 content addressing, weekly
  re-check of stored data with automatic repair.
- Restore, backup history, recovery key, failure notifications.
