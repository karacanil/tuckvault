# Changelog of the phone app

## 1.0.0

First release. Needs Tuckvault 1.5.0 or newer on the computer.

- Backs up photos, videos and files to a computer you own, over Tailscale.
  On Android: the whole photo library and any folders you choose. On iPhone:
  the whole photo library and the Tuckvault folder in the Files app.
- Everything is encrypted on the phone (AES-256-GCM) in the same format as the
  PC program, which can restore a phone's backup with its recovery key.
- Later backups send only what is new or changed. Runs automatically in the
  background a few times a day, optionally only on Wi-Fi or while charging;
  a run that is out of time continues where it stopped.
- Restore on the phone: save photos and videos back to the library, share any
  file, or restore everything at once. A new phone continues the old phone's
  backups with the recovery key.
- Notifications when a backup stored new files, when a backup has a problem,
  and when there has been no backup for a chosen number of days.
- "Check all backups now" re-reads every stored piece on the computer;
  damaged pieces are sent again by the next backup.
- Light and dark appearance.
