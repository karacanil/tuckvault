# Changelog of the phone app

## Unreleased

- Connecting to the computer is now one scan: point the phone's camera at the
  code Tuckvault shows on the computer, check the address the app shows, and
  tap **Connect to this computer**. Typing the address and code still works.
- Clearer messages when a code has expired, was already used or was mistyped.
- Choose which photos are backed up: everything, or only the albums you tick
  (**Which photos**, during setup and in Settings). Backups of chosen albums
  are kept apart from backups of the whole library.
- Copying the recovery key now says that it was copied.
- The home screen shows how much room is left on the computer for backups
  (once this phone has a backup there), and points out when it is nearly full.
- The app now also runs inside Expo Go, which makes it usable on an iPhone
  without a Mac or an Apple developer account: `npm run go` in `mobile/` serves
  it over Tailscale. There the storage format is done in TypeScript with the
  phone's own AES and SHA-256 (tested against the PC's vectors); the installed
  app keeps using its native module. Inside Expo Go there are no automatic
  backups and no Files-app folder.

## 1.0.1

- Android: the on-screen keyboard no longer covers the pairing code box and the Connect button. The form now stays above the keyboard.
- The keyboard's own key moves from the address to the code and then connects.

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
