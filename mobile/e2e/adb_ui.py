"""Drives the app on an Android emulator or phone through adb, the way a user would.

Elements are found by the testID given in the app's code (Android exposes it
as the resource-id) or by their visible text, and tapped at their centre.
"""

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET

PACKAGE = "io.github.karacanil.tuckvault"


class Device:
    def __init__(self, serial: str = None, adb: str = None):
        self.serial = serial or os.environ.get("ANDROID_SERIAL")
        self.adb = adb or os.environ.get("ADB", "adb")

    # -- plumbing ----------------------------------------------------------
    def run(self, *args, check=True, binary=False, timeout=120):
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + list(args)
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if check and res.returncode != 0:
            raise RuntimeError("adb %s failed: %s" % (" ".join(args), res.stderr.decode("utf-8", "replace")))
        return res.stdout if binary else res.stdout.decode("utf-8", "replace")

    def shell(self, command: str, check=True, timeout=120) -> str:
        return self.run("shell", command, check=check, timeout=timeout)

    # -- looking -----------------------------------------------------------
    def dump(self) -> ET.Element:
        for _ in range(5):
            raw = self.run("exec-out", "uiautomator", "dump", "/dev/tty", check=False)
            start = raw.find("<?xml")
            end = raw.rfind("</hierarchy>")
            if start >= 0 and end > start:
                return ET.fromstring(raw[start:end + len("</hierarchy>")])
            time.sleep(0.5)
        raise RuntimeError("could not read the screen")

    @staticmethod
    def _center(node):
        x1, y1, x2, y2 = (int(n) for n in re.findall(r"\d+", node.get("bounds")))
        return (x1 + x2) // 2, (y1 + y2) // 2

    def find(self, test_id: str = None, text: str = None, contains: str = None):
        for node in self.dump().iter("node"):
            if test_id is not None and node.get("resource-id") == test_id:
                return node
            label = node.get("text") or node.get("content-desc") or ""
            if text is not None and label == text:
                return node
            if contains is not None and contains in label:
                return node
        return None

    def texts(self) -> list:
        """Every piece of text on the screen, top to bottom."""
        out = []
        for node in self.dump().iter("node"):
            for value in (node.get("text"), node.get("content-desc")):
                if value and value not in out:
                    out.append(value)
        return out

    def wait_for(self, test_id: str = None, text: str = None, contains: str = None, timeout: float = 30):
        deadline = time.time() + timeout
        while True:
            node = self.find(test_id, text, contains)
            if node is not None:
                return node
            if time.time() > deadline:
                raise AssertionError("did not appear: %s. On screen: %s"
                                     % (test_id or text or contains, " | ".join(self.texts())[:1500]))
            time.sleep(0.7)

    def gone(self, test_id: str = None, text: str = None, contains: str = None, timeout: float = 30):
        deadline = time.time() + timeout
        while self.find(test_id, text, contains) is not None:
            if time.time() > deadline:
                raise AssertionError("still on screen: %s" % (test_id or text or contains))
            time.sleep(0.7)

    # -- doing -------------------------------------------------------------
    def tap(self, test_id: str = None, text: str = None, contains: str = None, timeout: float = 30):
        node = self.wait_for(test_id, text, contains, timeout)
        x, y = self._center(node)
        self.shell("input tap %d %d" % (x, y))
        time.sleep(0.6)

    def type_into(self, test_id: str, value: str):
        self.tap(test_id)
        # "input text" takes %s for a space and needs the shell's special characters escaped
        escaped = re.sub(r"([\\'\"`$&|;<>()*?#~ ])", lambda m: "%s" if m.group(1) == " " else "\\" + m.group(1), value)
        self.shell("input text '%s'" % escaped.replace("'", ""))
        time.sleep(0.4)

    def back(self):
        self.shell("input keyevent 4")
        time.sleep(0.6)

    def swipe_up(self, fraction: float = 0.5):
        size = re.findall(r"\d+", self.shell("wm size"))
        w, h = int(size[-2]), int(size[-1])
        self.shell("input swipe %d %d %d %d 300" % (w // 2, int(h * 0.75), w // 2, int(h * (0.75 - fraction))))
        time.sleep(0.7)

    def scroll_to(self, test_id: str = None, text: str = None, contains: str = None, tries: int = 8):
        for _ in range(tries):
            node = self.find(test_id, text, contains)
            if node is not None:
                return node
            self.swipe_up(0.35)
        raise AssertionError("not found by scrolling: %s" % (test_id or text or contains))

    def screenshot(self, path: str):
        with open(path, "wb") as f:
            f.write(self.run("exec-out", "screencap", "-p", binary=True))

    def launch(self):
        self.shell("monkey -p %s -c android.intent.category.LAUNCHER 1" % PACKAGE)
        time.sleep(2)

    def stop(self):
        self.shell("am force-stop " + PACKAGE)

    def clear(self):
        self.shell("pm clear " + PACKAGE)

    def grant(self, *permissions):
        for p in permissions:
            self.shell("pm grant %s android.permission.%s" % (PACKAGE, p), check=False)
