"""End-to-end test of the Android app against the real PC program.

Drives the installed app on an emulator the way a user would: sets it up,
pairs it with `tuckvault serve` running on this computer, backs up photos,
a video and a folder, and then proves the result from the outside:

  * the PC restores the phone's backup with the recovery key the app showed,
    and every file is byte-for-byte what was put on the phone;
  * a second backup sends only what is new;
  * a photo is restored from the backup back into the phone's library;
  * the app's own check of the stored data passes;
  * with the computer unreachable the app says so in plain words;
  * a backup started by the system in the background runs and notifies.

    python mobile/e2e/run_android.py --apk <app.apk> [--serial emulator-5580] [--shots <folder>]

With --expo-go exp://127.0.0.1:<port> instead of --apk, the same is done with the app running
inside Expo Go (already installed on the emulator, `npx expo start --go` running and reachable
there, e.g. through `adb reverse`). That runs the plain TypeScript storage code instead of the
native module. Expo Go has no background work, so the last step is left out.

Needs adb on PATH (or ADB set) and the Python requirements of Tuckvault.
The emulator reaches this computer's own address as 10.0.2.2.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adb_ui  # noqa: E402
from adb_ui import PACKAGE, Device  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
PORT = 8743


def log(text):
    print("[e2e] " + text, flush=True)


class Pc:
    """The PC side: a throwaway Tuckvault profile storing into a temp folder."""

    def __init__(self, work: Path, ssh: str = None, tailscale: bool = False):
        self.tailscale = tailscale
        self.work = work
        self.home = work / "home"
        self.home.mkdir(parents=True)
        # ssh: "host:folder" stores on another computer instead, the way a real setup may
        if ssh:
            host, _, folder = ssh.partition(":")
            destination = {"type": "ssh", "host": host, "path": folder}
        else:
            destination = {"type": "local", "folder": str(work / "repo")}
        self.local = not ssh
        (self.home / "config.json").write_text(json.dumps({"destination": destination}), encoding="utf-8")
        self.env = dict(os.environ, TUCKVAULT_HOME=str(self.home), TUCKVAULT_SERVE_IDLE="2", PYTHONUNBUFFERED="1")
        self.proc = None

    def run(self, *args, **extra):
        res = subprocess.run([sys.executable, "-m", "tuckvault", *args], cwd=REPO, env=dict(self.env, **extra),
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        return res.returncode, res.stdout + res.stderr

    def start(self):
        # Normally only this computer itself is listened on. For a Tailscale address the service is
        # started the way a user runs it: it then also listens on this computer's Tailscale address.
        listen = [] if self.tailscale else ["--listen", "127.0.0.1"]
        self.proc = subprocess.Popen([sys.executable, "-m", "tuckvault", "serve"] + listen,
                                     cwd=REPO, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            code, out = self.run("phone", "status")
            if "Accepting backups from phones: yes" in out:
                return
            time.sleep(0.2)
        raise RuntimeError("tuckvault serve did not start: " + out)

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            self.proc = None

    def pairing_code(self) -> str:
        code, out = self.run("phone", "pair")
        assert code == 0, out
        return next(line.split()[-1] for line in out.splitlines() if "Code:" in line)

    def pairing_link(self, prefix: str, address: str) -> str:
        """The link inside the code a phone scans, pointed at the address this phone reaches the computer by."""
        from urllib.parse import parse_qs, urlencode, urlsplit
        code, out = self.run("phone", "pair", "--show-link", "--link-prefix", prefix,
                             "--qr", str(self.home / "pair.png"))
        assert code == 0, out
        link = next(line.split()[-1] for line in out.splitlines() if "Link:" in line)
        token = parse_qs(urlsplit(link).query)["c"][0]
        return prefix + "connect?" + urlencode({"u": address, "c": token})

    def restore(self, recovery_key: str, dest: Path):
        code, out = self.run("phone", "restore", str(dest), TUCKVAULT_PHONE_KEY=recovery_key)
        assert code == 0, out
        return out


def make_media(folder: Path):
    """Test photos with a location inside (to prove the phone hands over the original file), a video, a note."""
    from PIL import Image, ImageDraw
    import random
    folder.mkdir(parents=True)
    rng = random.Random(7)
    for i in range(1, 5):
        im = Image.new("RGB", (1200, 900), tuple(rng.randrange(256) for _ in range(3)))
        d = ImageDraw.Draw(im)
        for _ in range(250):
            x, y = rng.randrange(1200), rng.randrange(900)
            d.ellipse((x, y, x + rng.randrange(10, 160), y + rng.randrange(10, 160)),
                      fill=tuple(rng.randrange(256) for _ in range(3)))
        exif = Image.Exif()
        exif[0x010F] = "Tuckvault test"
        exif[0x9003] = "2026:11:%02d 10:00:00" % i
        exif[0x8825] = {1: "N", 2: (41.0, 0.0, 29.0), 3: "E", 4: (28.0, 58.0, 0.0)}
        im.save(folder / ("TVTEST_%04d.jpg" % i), quality=90, exif=exif)
    (folder / "TVTEST_clip.mp4").write_bytes(rng.randbytes(9_300_000))  # larger than two pieces
    (folder / "notes.txt").write_text("gizli not ✓\n" * 300, encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def push_media(d: Device, media: Path, names, where="/sdcard/DCIM/TuckvaultTest"):
    d.shell("mkdir -p " + where)
    for name in names:
        d.run("push", str(media / name), where + "/" + name)
        d.shell('am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "file://%s/%s"' % (where, name))
    time.sleep(3)


def allow_system_dialog(d: Device, timeout=8):
    """Answer an Android permission question with yes, whatever this version calls the button."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label in ("Allow all", "Allow", "While using the app", "ALLOW"):
            node = d.find(text=label)
            if node is not None:
                x, y = d._center(node)
                d.shell("input tap %d %d" % (x, y))
                time.sleep(1)
                return True
        time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk")
    ap.add_argument("--expo-go", metavar="URL", help="run the app inside Expo Go from this exp:// address")
    ap.add_argument("--scan", action="store_true",
                    help="pair by opening the link of the code the computer shows, as scanning it does, "
                         "instead of typing the code")
    ap.add_argument("--serial")
    ap.add_argument("--shots", help="folder to save screenshots into")
    ap.add_argument("--address", default="10.0.2.2",
                    help="address typed into the app (default: the emulator's name for this computer). Give "
                         "this computer's Tailscale address or name to go through the Tailscale listener")
    ap.add_argument("--ssh", metavar="HOST:FOLDER",
                    help="store on another computer over SSH instead of a temp folder; the phone "
                         "backups then land in FOLDER-phones there")
    args = ap.parse_args()
    if bool(args.apk) == bool(args.expo_go):
        ap.error("give either --apk or --expo-go")
    if args.expo_go:
        adb_ui.PACKAGE = "host.exp.exponent"

    d = Device(args.serial)

    def launch():
        if not args.expo_go:
            return d.launch()
        d.shell("am start -a android.intent.action.VIEW -d %s host.exp.exponent" % args.expo_go)
        deadline = time.time() + 240  # the first bundle takes a while
        while d.find("get-started") is None and time.time() < deadline:
            for button in ("Continue", "Close"):  # Expo Go introduces its developer menu once
                if d.find(text=button) is not None:
                    d.tap(text=button)
                    time.sleep(1)
            time.sleep(2)

    work = Path(tempfile.mkdtemp(prefix="tuckvault-e2e-"))
    shots = Path(args.shots) if args.shots else work / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    pc = Pc(work, args.ssh, tailscale=args.address != "10.0.2.2")

    def shot(name):
        d.screenshot(str(shots / (name + ".png")))

    try:
        media = work / "media"
        make_media(media)
        first = ["TVTEST_0001.jpg", "TVTEST_0002.jpg", "TVTEST_0003.jpg", "TVTEST_clip.mp4"]

        log("installing the app and putting test media on the phone")
        if args.apk:
            d.run("install", "-r", args.apk, timeout=300)
        d.clear()
        d.shell("rm -rf /sdcard/DCIM/TuckvaultTest /sdcard/Pictures/TVTEST* /sdcard/DCIM/TVTEST*", check=False)
        push_media(d, media, first)
        d.shell("mkdir -p /sdcard/Documents/TuckvaultNotes")
        d.run("push", str(media / "notes.txt"), "/sdcard/Documents/TuckvaultNotes/notes.txt")
        pc.start()

        # -- setup ---------------------------------------------------------
        log("setup: welcome")
        launch()
        if d.dismiss_not_responding():  # the launcher of a freshly started emulator can hang for a moment
            launch()
        d.wait_for("get-started", timeout=120)
        shot("01-welcome")
        d.tap("get-started")

        log("setup: a wrong code is refused, the right one pairs")
        d.wait_for("address")
        d.type_into("address", args.address)
        d.shell("input keyevent 66")  # the keyboard's Next key moves on to the code
        time.sleep(1)
        d.shell("input text AAAA-AAAA")
        time.sleep(0.5)
        d.back()  # put the keyboard away
        pc.pairing_code()  # a pairing is in progress, but the code typed is not it
        d.tap("connect")
        d.wait_for("connect-error")
        assert d.find(contains="That code is not right") is not None, d.texts()
        shot("02-connect-wrong-code")
        if args.scan:
            log("setup: the code on the computer is scanned (its link is opened), nothing is typed")
            prefix = args.expo_go.rstrip("/") + "/--/" if args.expo_go else "tuckvault://"
            link = pc.pairing_link(prefix, "http://%s:%d" % (args.address, PORT))
            d.shell("am start -a android.intent.action.VIEW -d '%s' %s" % (link, adb_ui.PACKAGE))
            host = d.wait_for("scanned-host", timeout=60)
            assert args.address in (host.get("text") or ""), host.attrib
            assert d.find("code") is None, "the boxes to type into should be gone"
            shot("02b-scanned")
            d.tap("connect")
        else:
            code = pc.pairing_code()
            d.tap("code")
            d.shell("input keyevent --longpress 67 67 67 67 67 67 67 67 67 67 67 67")  # clear the box
            d.type_into("code", code)
            d.back()
            d.tap("connect")

        log("setup: recovery key")
        key_node = d.wait_for("recovery-key", timeout=30)
        recovery_key = "-".join(key_node.get("content-desc").split(": ", 1)[1].split())
        assert len(recovery_key.replace("-", "")) == 56, recovery_key
        shot("03-recovery-key")
        assert d.find("key-continue") is not None
        d.tap("key-saved")
        d.tap("key-continue")

        log("setup: what to back up")
        d.wait_for("toggle-photos")
        if not args.expo_go:  # on Android, Expo Go is not allowed to see the photo library
            d.tap("toggle-photos")
            allow_system_dialog(d)
            time.sleep(1)
        log("setup: add a folder with Android's folder picker")
        d.scroll_to("add-folder")
        d.tap("add-folder")
        d.tap(text="Documents", timeout=20)
        d.tap(text="TuckvaultNotes")
        d.tap(text="USE THIS FOLDER")
        d.tap(text="ALLOW")
        d.wait_for(text="TuckvaultNotes", timeout=20)
        shot("04-choose")
        d.scroll_to("finish-setup")
        d.tap("finish-setup")
        allow_system_dialog(d)  # notifications

        # -- first backup --------------------------------------------------
        log("first backup")
        d.wait_for("backup-now", timeout=30)
        shot("05-home-before")
        assert d.find(text="Not backed up yet") is not None, d.texts()
        assert d.find(contains="Connected to") is not None, d.texts()
        d.tap("backup-now")
        time.sleep(1.5)
        shot("06-backing-up")
        d.wait_for(text="Your phone is backed up", timeout=240)
        shot("07-home-after")

        log("the PC restores the phone's backup with the recovery key")
        out1 = work / "restored-1"
        pc.restore(recovery_key, out1)
        restored = {p.name: p for p in out1.rglob("*") if p.is_file()}
        for name in ([] if args.expo_go else first) + ["notes.txt"]:
            assert name in restored, "missing in the backup: %s (has %s)" % (name, sorted(restored))
            assert sha(restored[name]) == sha(media / name), name + " differs from what was put on the phone"
        if not args.expo_go:
            from PIL import Image
            gps = Image.open(restored["TVTEST_0001.jpg"]).getexif().get_ifd(0x8825)
            assert gps.get(1) == "N", "the photo's location did not survive the backup: %r" % dict(gps)
        if pc.local:
            blobs = b"".join(p.read_bytes() for p in (work / "repo-phones").rglob("*")
                             if p.is_file() and p.parent.parent.name == "blobs")
            assert b"Tuckvault test" not in blobs and b"TVTEST" not in blobs and "gizli".encode() not in blobs, "plain data found"
        notes = restored["notes.txt"].relative_to(out1).as_posix()
        assert notes == "Files/TuckvaultNotes/notes.txt", notes
        if args.expo_go:
            log("  restored the file from the chosen folder, identical%s" % ("; nothing readable stored" if pc.local else ""))
        else:
            log("  restored %d files (photos, a video, a file from a chosen folder), all identical; location kept%s" % (
                len(first) + 1, "; nothing readable stored" if pc.local else ""))

        # -- second backup: only what is new --------------------------------
        log("second backup after one new file")
        if args.expo_go:
            d.run("push", str(media / "TVTEST_0004.jpg"), "/sdcard/Documents/TuckvaultNotes/TVTEST_0004.jpg")
        else:
            push_media(d, media, ["TVTEST_0004.jpg"])
        d.tap("backup-now")
        deadline = time.time() + 240  # it can be over before the screen is read once
        while True:
            d.wait_for("backup-now", timeout=240)
            d.tap(text="Activity")
            if d.find(contains="1 new or changed file") is not None:
                break
            assert time.time() < deadline, d.texts()
            d.back()
            time.sleep(2)
        shot("08-activity")
        d.back()

        # -- browse and restore on the phone ---------------------------------
        log("restore a photo on the phone from the backup")
        d.tap("tab-backups")
        d.wait_for("backup-0", timeout=30)
        shot("09-backups")
        d.tap("backup-0")
        d.wait_for("file-search", timeout=30)
        d.type_into("file-search", "TVTEST_0004" if args.expo_go else "TVTEST_0002")
        d.back()
        d.wait_for("file-0")
        shot("10-backup-files")
        before = d.shell("content query --uri content://media/external/images/media --projection _display_name")
        if not args.expo_go:
            d.tap("file-0")
            d.tap("save-to-photos")
            d.wait_for(text="Restored", timeout=60)
            assert d.find(contains="1 item saved to your photo library") is not None, d.texts()
            shot("11-restored")
            d.tap(text="OK")
            after = d.shell("content query --uri content://media/external/images/media --projection _display_name")
            assert after.count("TVTEST_0002") == before.count("TVTEST_0002") + 1, "the photo did not arrive in the library"
        d.back()

        # -- the app's own check ----------------------------------------------
        log("check all backups from the app")
        d.tap("tab-settings")
        d.scroll_to("check-backups")
        shot("12-settings")
        d.tap("check-backups")
        d.wait_for(text="All backups are complete", timeout=120)
        d.tap(text="OK")
        d.scroll_to("show-recovery-key")
        d.tap("show-recovery-key")
        d.tap("reveal-key")
        shown = d.wait_for("recovery-key").get("content-desc")
        assert "-".join(shown.split(": ", 1)[1].split()) == recovery_key
        d.back()

        # -- the computer is unreachable ---------------------------------------
        log("with the computer switched off the app explains what is wrong")
        pc.stop()
        d.tap("tab-home")
        d.wait_for("backup-now")
        d.tap("backup-now")
        d.wait_for("last-error", timeout=90)
        assert d.find(contains="Cannot reach your computer") is not None, d.texts()
        assert d.find(text="The last backup did not work") is not None, d.texts()
        shot("13-unreachable")
        if args.expo_go:
            pc.start()
            out2 = work / "restored-2"
            pc.restore(recovery_key, out2)
            again = {p.name: p for p in out2.rglob("*") if p.is_file()}
            assert sha(again["TVTEST_0004.jpg"]) == sha(media / "TVTEST_0004.jpg"), "the second backup's file differs"
            log("ALL CHECKS PASSED (inside Expo Go, which has no background backups). Screenshots: " + str(shots))
            return 0

        # -- background run and notification -----------------------------------
        log("a backup started by the system in the background")
        pc.start()
        push_media(d, media, ["TVTEST_0001.jpg"], where="/sdcard/Pictures/TuckvaultTest2")
        d.shell("input keyevent 3")  # home: the app is in the background now
        time.sleep(2)
        # WorkManager's job for this app, e.g. "JOB androidx.work.systemjobscheduler:u0a216/0: ..." (newer
        # Android, with a namespace) or "JOB #u0a216/0: ..." (older).
        found = re.findall(r"JOB (?:(\S+):)?#?u\w+/(\d+): \S+ .*" + re.escape(PACKAGE) + r"/androidx\.work.*SystemJobService",
                           d.shell("dumpsys jobscheduler"))
        assert found, "the automatic backup is not scheduled with the system"
        for namespace, job in found:
            d.shell("cmd jobscheduler run -f %s%s %s" % ("-n %s " % namespace if namespace else "", PACKAGE, job), check=False)
        deadline = time.time() + 180
        while "Backup finished" not in d.shell("dumpsys notification --noredact"):
            assert time.time() < deadline, "no notification after the background backup"
            time.sleep(3)
        d.shell("cmd statusbar expand-notifications")
        time.sleep(2)
        shot("14-notification")
        d.shell("cmd statusbar collapse")
        out2 = work / "restored-2"
        pc.restore(recovery_key, out2)
        names = sorted(p.name for p in out2.rglob("*") if p.is_file())
        assert "TVTEST_0004.jpg" in names and len([n for n in names if n.startswith("TVTEST_0001")]) >= 2, names
        log("  background backup stored the new photo and notified")

        log("ALL CHECKS PASSED. Screenshots: " + str(shots))
        return 0
    except Exception:
        try:
            shot("failure")
            log("on screen: " + " | ".join(d.texts())[:2000])
        except Exception:
            pass
        raise
    finally:
        pc.stop()
        shutil.rmtree(work, ignore_errors=True) if not args.shots else shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
