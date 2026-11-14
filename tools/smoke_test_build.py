"""Checks a packaged build (dist\\Tuckvault) really works, with throwaway settings and data.

    python tools\\smoke_test_build.py dist\\Tuckvault [ssh-name]

Runs the two programs the way a user and the scheduled tasks would:
back up to a folder, check, restore, compare, and open the window.
With an SSH name it repeats the backup against that computer.
Nothing of the real Tuckvault settings is touched.
"""

import ctypes
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _window_of(pid: int, title: str) -> int:
    """A visible top-level window with this title that belongs to this process (never another one)."""
    from ctypes import wintypes
    user32, found = ctypes.windll.user32, []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        text = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, text, 64)
        if owner.value == pid and text.value == title and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else 0


def main() -> int:
    dist = Path(sys.argv[1]).resolve()
    ssh_host = sys.argv[2] if len(sys.argv) > 2 else None
    cli, gui = dist / "tuckvault-cli.exe", dist / "Tuckvault.exe"
    tmp = Path(tempfile.mkdtemp(prefix="tuckvault-smoke-"))
    home = tmp / "home"
    env = dict(os.environ, TUCKVAULT_HOME=str(home), TUCKVAULT_TASK_NAME=r"Tuckvault\SmokeTest")
    failures = []

    def check(name, ok, detail=""):
        print(("ok    " if ok else "FAIL  ") + name + (f"  {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    def run(exe, *args):
        return subprocess.run([str(exe), *args], env=env, capture_output=True, text=True,
                              errors="replace", timeout=300)

    def write_config(destination):
        home.mkdir(exist_ok=True)
        (home / "config.json").write_text(json.dumps({
            "sources": [str(src)], "destination": destination,
            "schedule": {"enabled": False}, "verify_every_days": 0, "check_updates": False}), encoding="utf-8")

    try:
        src = tmp / "documents"
        (src / "sub").mkdir(parents=True)
        (src / "big.bin").write_bytes(os.urandom(6_000_000))
        (src / "sub" / "vault.json").write_text("secret vault " * 100, encoding="utf-8")

        r = run(cli, "--version")
        check("version", r.returncode == 0 and r.stdout.strip()[:1].isdigit(), r.stdout + r.stderr)

        write_config({"type": "local", "folder": str(tmp / "drive")})
        r = run(cli, "run")
        check("backup to a folder", r.returncode == 0 and "success" in r.stdout, r.stdout + r.stderr)
        r = run(gui, "run", "--scheduled")  # what a scheduled task does: no console at all
        state = json.loads((home / "state.json").read_text(encoding="utf-8"))
        check("unattended run (window program)", r.returncode == 0 and len(state["history"]) == 2
              and state["history"][0]["uploaded_bytes"] == 0, str(state.get("last_run")))
        r = run(cli, "verify")
        check("check stored data", r.returncode == 0, r.stdout + r.stderr)
        r = run(cli, "restore", "latest", str(tmp / "out"))
        restored = next((tmp / "out").rglob("big.bin"), None)
        check("restore", r.returncode == 0 and restored is not None
              and filecmp.cmp(restored, src / "big.bin", shallow=False), r.stdout + r.stderr)
        r = run(cli, "key", "show")
        check("recovery key", len(r.stdout.strip().replace("-", "")) == 56, r.stdout + r.stderr)
        r = run(cli, "schedule", "status")
        check("schedule status", r.returncode == 0, r.stdout + r.stderr)

        if ssh_host:
            repo = f"tuckvault-smoke-{os.getpid()}"
            write_config({"type": "ssh", "host": ssh_host, "path": repo})
            (home / "cache.json").unlink(missing_ok=True)
            try:
                r = run(cli, "run")
                check(f"backup over SSH to {ssh_host}", r.returncode == 0 and "success" in r.stdout,
                      r.stdout + r.stderr)
                r = run(cli, "verify")
                check("check over SSH", r.returncode == 0, r.stdout + r.stderr)
            finally:
                subprocess.run(["ssh", "-o", "BatchMode=yes", ssh_host, "rm", "-r", repo],
                               capture_output=True)

        # The window opens, has the right title, and closes when asked.
        proc = subprocess.Popen([str(gui)], env=env)
        user32, hwnd = ctypes.windll.user32, 0
        for _ in range(100):
            time.sleep(0.2)
            hwnd = _window_of(proc.pid, "Tuckvault")
            if hwnd or proc.poll() is not None:
                break
        check("window opens", bool(hwnd) and proc.poll() is None)
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        try:
            proc.wait(15)
            check("window closes", True)
        except subprocess.TimeoutExpired:
            proc.kill()
            check("window closes", False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL GOOD" if not failures else f"{len(failures)} FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
