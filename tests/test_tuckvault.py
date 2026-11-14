"""End-to-end tests against a repository in a temp folder.

The storage agent is the same code that runs on the backup machine; here it
is started as a local process instead of over SSH.

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""

import copy
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuckvault import config, crypto, engine, keystore, paths, scheduler, updates, watch  # noqa: E402

SECRET = b"TOP-SECRET-MARKER-9f2c "


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tuckvault-test-"))
        os.environ["TUCKVAULT_HOME"] = str(self.tmp / "home")
        self.src = self.tmp / "src"
        self.repo = self.tmp / "repo"
        (self.src / "sub" / "deep").mkdir(parents=True)
        (self.src / "a.txt").write_bytes(SECRET * 50)
        (self.src / "empty.bin").write_bytes(b"")
        (self.src / "sub" / "şifreler ünïcode.json").write_bytes(b'{"vault": "' + SECRET + b'"}')
        (self.src / "sub" / "deep" / "random.bin").write_bytes(os.urandom(100_000))
        # larger than one chunk, compressible
        (self.src / "big.dat").write_bytes(SECRET * (engine.CHUNK // len(SECRET) + 5000))
        (self.src / "skip.tmp").write_bytes(b"excluded")
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["sources"] = [str(self.src)]
        self.cfg["destination"] = {"type": "local", "path": str(self.repo)}
        self.cfg["verify_every_days"] = 0

    def tearDown(self):
        os.environ.pop("TUCKVAULT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def backup(self, expect="success"):
        result = engine.run_backup(self.cfg)
        self.assertEqual(result["status"], expect, result)
        return result

    def restore(self, snapshot, name="out"):
        dest = self.tmp / name
        result = engine.run_restore(snapshot, str(dest), self.cfg)
        return result, Path(engine._restore_target(str(dest), str(self.src)))

    def blob_files(self):
        return sorted(p for p in (self.repo / "blobs").rglob("*") if p.is_file())

    def assert_same_tree(self, restored: Path):
        expected = {p.relative_to(self.src): p.read_bytes()
                    for p in self.src.rglob("*") if p.is_file() and p.suffix != ".tmp"}
        actual = {p.relative_to(restored): p.read_bytes() for p in restored.rglob("*") if p.is_file()}
        self.assertEqual(set(expected), set(actual))
        for rel in expected:
            self.assertEqual(expected[rel], actual[rel], rel)


class BackupRestore(Base):
    def test_roundtrip(self):
        r = self.backup()
        self.assertEqual(r["files"], 5)
        res, restored = self.restore(r["snapshot"])
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)
        src_file, out_file = self.src / "a.txt", restored / "a.txt"
        self.assertEqual(src_file.stat().st_mtime_ns, out_file.stat().st_mtime_ns)

    def test_nothing_readable_is_stored(self):
        self.backup()
        for p in self.repo.rglob("*"):
            if p.is_file():
                data = p.read_bytes()
                self.assertNotIn(b"TOP-SECRET", data, p)
                self.assertNotIn(b"a.txt", data, p)
        if sys.platform == "win32":
            self.assertNotIn(keystore.load(), paths.key_file().read_bytes())

    def test_incremental_uploads_only_changes(self):
        first = self.backup()
        self.assertGreater(first["uploaded_bytes"], 0)
        second = self.backup()
        self.assertEqual(second["uploaded_bytes"], 0)
        self.assertEqual(second["new_files"], 0)
        (self.src / "a.txt").write_bytes(b"changed content")
        third = self.backup()
        self.assertEqual(third["new_files"], 1)
        self.assertLess(third["uploaded_bytes"], 200)
        # the older snapshot still restores the old content
        res, restored = self.restore(first["snapshot"], "old")
        self.assertEqual(res["status"], "success", res)
        self.assertEqual((restored / "a.txt").read_bytes(), SECRET * 50)
        res, restored = self.restore(third["snapshot"], "new")
        self.assertEqual((restored / "a.txt").read_bytes(), b"changed content")

    def test_identical_files_are_stored_once(self):
        self.backup()
        before = len(self.blob_files())
        shutil.copy(self.src / "sub" / "deep" / "random.bin", self.src / "copy.bin")
        r = self.backup()
        self.assertEqual(len(self.blob_files()), before)
        self.assertEqual(r["uploaded_bytes"], 0)

    def test_missing_source_is_a_warning(self):
        self.cfg["sources"].append(str(self.tmp / "does-not-exist"))
        r = self.backup(expect="warning")
        self.assertIn("does-not-exist", r["warnings"][0])

    def test_no_sources_fails(self):
        self.cfg["sources"] = []
        r = engine.run_backup(self.cfg)
        self.assertEqual(r["status"], "failed")

    def test_unreachable_destination_fails_cleanly(self):
        self.cfg["destination"] = {"type": "ssh", "host": "-bad", "path": "x"}
        r = engine.run_backup(self.cfg)
        self.assertEqual(r["status"], "failed")
        self.assertTrue(r["error"])
        self.assertEqual(config.load_state()["last_run"]["status"], "failed")


class Protection(Base):
    def test_damaged_blob_is_detected_and_healed(self):
        r = self.backup()
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")
        victim = max(self.blob_files(), key=lambda p: p.stat().st_size)
        data = bytearray(victim.read_bytes())
        data[len(data) // 2] ^= 0x01
        victim.write_bytes(bytes(data))

        # a restore must refuse the damaged data rather than hand it back
        res, restored = self.restore(r["snapshot"], "damaged")
        self.assertIn(res["status"], ("warning", "failed"))
        self.assertTrue(res["warnings"])

        victim.write_bytes(bytes(data))  # restore does not repair; verify does
        v = engine.run_verify(self.cfg)
        self.assertEqual(v["status"], "failed")
        self.assertIn("damaged or missing", v["error"])
        self.assertFalse(victim.exists())

        # the next backup notices the gap and re-sends the data
        again = self.backup()
        self.assertGreater(again["uploaded_bytes"], 0)
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")
        res, restored = self.restore(again["snapshot"], "healed")
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)

    def test_deleted_blob_is_detected(self):
        self.backup()
        self.blob_files()[0].unlink()
        self.assertEqual(engine.run_verify(self.cfg)["status"], "failed")

    def test_scheduled_scrub_reports_damage(self):
        self.backup()
        victim = self.blob_files()[0]
        victim.write_bytes(victim.read_bytes()[:-1] + b"\x00\x01")
        self.cfg["verify_every_days"] = 7
        r = engine.run_backup(self.cfg)
        self.assertEqual(r["status"], "failed")
        self.assertIn("damaged", r["error"])

    def test_tampered_snapshot_is_rejected(self):
        r = self.backup()
        snap = self.repo / "snapshots" / r["snapshot"]
        data = bytearray(snap.read_bytes())
        data[-5] ^= 0xFF
        snap.write_bytes(bytes(data))
        shutil.rmtree(paths.snapshot_cache(config.read_json(self.repo / "repo.json", {})["id"]))
        listed = engine.list_snapshots(self.cfg)
        self.assertIn("error", listed[0])
        res, _ = self.restore(r["snapshot"])
        self.assertEqual(res["status"], "failed")

    @unittest.skipIf(sys.platform == "win32", "Windows protects the key with DPAPI instead")
    def test_key_file_readable_by_others_is_refused(self):
        self.backup()
        key = paths.key_file()
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        os.chmod(key, 0o644)
        with self.assertRaises(OSError) as e:
            keystore.load()
        self.assertIn("chmod 600", str(e.exception))
        os.chmod(key, 0o600)
        self.assertIsNotNone(keystore.load())

    def test_wrong_key_is_refused(self):
        self.backup()
        keystore.store(crypto.new_master_key())
        r = engine.run_backup(self.cfg)
        self.assertEqual(r["status"], "failed")
        self.assertIn("does not match", r["error"])

    def test_recovery_key_restores_on_a_fresh_pc(self):
        r = self.backup()
        recovery = keystore.recovery_key()
        shutil.rmtree(self.tmp / "home")  # the PC is gone
        self.assertIsNone(keystore.load())
        res, _ = self.restore(r["snapshot"], "nokey")
        self.assertEqual(res["status"], "failed")
        keystore.import_recovery_key(recovery.lower().replace("-", " "))
        res, restored = self.restore(r["snapshot"], "withkey")
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)

    def test_recovery_key_typo_is_caught(self):
        key = crypto.encode_recovery_key(crypto.new_master_key())
        bad = ("B" if key[0] != "B" else "C") + key[1:]
        with self.assertRaises(ValueError):
            crypto.decode_recovery_key(bad)

    def test_agent_rejects_blob_with_wrong_hash(self):
        from tuckvault.remote import Remote, RemoteError
        with Remote(self.cfg["destination"]) as remote:
            remote.open(create=True)
            with self.assertRaises(RemoteError):
                remote.put_blob("0" * 64, b"not matching")
            with self.assertRaises(RemoteError):
                remote.call("get_blob", name="../../etc/passwd")
            # the connection is still usable after errors
            self.assertEqual(remote.list_blobs(), {})


class Retention(Base):
    def test_prune_keeps_last_n_and_frees_space(self):
        self.cfg["keep_last"] = 2
        snaps = []
        for i in range(4):
            (self.src / "sub" / "deep" / "random.bin").write_bytes(os.urandom(50_000))
            snaps.append(self.backup()["snapshot"])
        names = [s["name"] for s in engine.list_snapshots(self.cfg)]
        self.assertEqual(sorted(names), sorted(snaps[-2:]))
        randoms = [p for p in self.blob_files() if 49_000 < p.stat().st_size < 52_000]
        self.assertEqual(len(randoms), 2)
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")
        for snap in snaps[-2:]:
            res, _ = self.restore(snap, "r-" + snap[-11:-5])
            self.assertEqual(res["status"], "success", res)


class UntrustedStorage(Base):
    """Things a broken or hostile storage side could do."""

    def test_old_backup_passed_off_as_new_is_rejected(self):
        first = self.backup()["snapshot"]
        (self.src / "a.txt").write_bytes(b"new content")
        second = self.backup()["snapshot"]
        snaps = self.repo / "snapshots"
        (snaps / second).write_bytes((snaps / first).read_bytes())  # roll the newest one back
        shutil.rmtree(paths.snapshot_cache(self.repo_id()))
        listed = {s["name"]: s for s in engine.list_snapshots(self.cfg)}
        self.assertNotIn("error", listed[first])
        self.assertIn("different name", listed[second]["error"])
        res, _ = self.restore(second)
        self.assertEqual(res["status"], "failed")

    def test_vanished_backup_is_reported_once(self):
        first = self.backup()["snapshot"]
        self.backup()
        (self.repo / "snapshots" / first).unlink()
        r = engine.run_verify(self.cfg)
        self.assertEqual(r["status"], "failed")
        self.assertIn("disappeared", r["error"])
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")
        # ...and by a normal backup run too
        second = sorted(p.name for p in (self.repo / "snapshots").iterdir())[0]
        (self.repo / "snapshots" / second).unlink()
        r = self.backup(expect="warning")
        self.assertIn("disappeared", r["warnings"][0])
        self.backup()

    def test_removing_old_backups_ourselves_is_not_reported(self):
        self.cfg["keep_last"] = 1
        for _ in range(3):
            self.backup()
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")

    def test_oversized_reply_is_refused(self):
        import io
        import struct
        from tuckvault import remote

        class Fake:
            stdin = io.BytesIO()
            stderr = io.BytesIO()

            def __init__(self, reply):
                self.stdout = io.BytesIO(reply)

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        r = remote.Remote({"type": "local", "folder": "x"})
        r.proc = Fake(struct.pack(">I", remote.MAX_HEADER + 1))
        with self.assertRaises(remote.RemoteError):
            r.call("stat")
        hdr = b'{"ok": true, "size": %d}' % (remote.MAX_PAYLOAD + 1)
        r.proc = Fake(struct.pack(">I", len(hdr)) + hdr)
        with self.assertRaises(remote.RemoteError):
            r.call("stat")
        r.proc = Fake(struct.pack(">I", 5) + b"[1,2]")
        with self.assertRaises(remote.RemoteError):
            r.call("stat")

    def repo_id(self):
        import json
        return json.loads((self.repo / "repo.json").read_text())["id"]

    def fake_remote(self, reply_header: dict):
        """A Remote whose other side answers with exactly this header."""
        import io
        import json
        import struct
        from tuckvault import remote

        class Fake:
            stdin, stderr = io.BytesIO(), io.BytesIO()

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        raw = json.dumps(dict(reply_header, ok=True)).encode()
        r = remote.Remote({"type": "local", "folder": "x"})
        r.proc = Fake()
        r.proc.stdout = io.BytesIO(struct.pack(">I", len(raw)) + raw)
        return r

    def test_file_names_from_storage_cannot_leave_the_cache_folder(self):
        from tuckvault import remote
        for bad in ("..\\..\\key.bin", "../../key.bin", "C:\\Users\\x\\doc.snap", "a/b.snap",
                    "20260101T000000.000000Z-abcdef.snap\n", "x.txt", ""):
            with self.assertRaises(remote.RemoteError, msg=bad):
                self.fake_remote({"snapshots": [{"name": bad, "size": 1}]}).list_snapshots()
        for bad_id in ("..\\..\\..", "C:\\Windows", "abc", 5, None):
            with self.assertRaises(remote.RemoteError, msg=str(bad_id)):
                self.fake_remote({"meta": {"id": bad_id, "key_check": "x"}}).open(create=False)
        with self.assertRaises(remote.RemoteError):  # a name that was never asked about
            self.fake_remote({"missing": ["f" * 64], "corrupt": []}).verify_blobs(["a" * 64], full=True)
        with self.assertRaises(ValueError):
            paths.snapshot_cache("..\\..\\elsewhere")

    def test_tampered_repo_id_is_refused_before_touching_local_files(self):
        import json
        self.backup()
        key_before = paths.key_file().read_bytes()
        meta = json.loads((self.repo / "repo.json").read_text())
        meta["id"] = "..\\..\\..\\victim"
        (self.repo / "repo.json").write_text(json.dumps(meta))
        r = self.backup(expect="failed")
        self.assertIn("invalid reply", r["error"])
        self.assertEqual(paths.key_file().read_bytes(), key_before)
        self.assertFalse((self.tmp / "victim").exists())

    def test_emptied_destination_is_not_silently_started_over(self):
        self.backup()
        shutil.rmtree(self.repo)  # the disk is not mounted, or was wiped
        r = self.backup(expect="failed")
        self.assertEqual(r.get("code"), "repo_changed")
        self.assertFalse((self.repo / "repo.json").exists())  # nothing was created there
        self.assertEqual(engine.run_verify(self.cfg)["status"], "failed")
        config.remember_repo(self.cfg["destination"], None)  # the user says: start over
        self.backup()

    def test_swapped_destination_is_noticed(self):
        import json
        self.backup()
        meta = json.loads((self.repo / "repo.json").read_text())
        meta["id"] = "0123456789abcdef"
        (self.repo / "repo.json").write_text(json.dumps(meta))
        self.assertEqual(self.backup(expect="failed").get("code"), "repo_changed")

    def test_damaged_backup_index_is_put_back(self):
        snap = self.backup()["snapshot"]
        stored = self.repo / "snapshots" / snap
        good = stored.read_bytes()
        stored.write_bytes(b"rotten")
        r = engine.run_verify(self.cfg)
        self.assertIn("put back", r["error"])
        self.assertEqual(stored.read_bytes(), good)
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")

    def test_lost_backup_index_is_put_back(self):
        snap = self.backup()["snapshot"]
        (self.repo / "snapshots" / snap).unlink()
        self.assertIn("disappeared", engine.run_verify(self.cfg)["error"])
        res, restored = self.restore(snap)
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)

    def test_damaged_data_is_set_aside_not_destroyed(self):
        self.backup()
        blob = self.blob_files()[0]
        blob.write_bytes(b"garbage")
        self.assertEqual(engine.run_verify(self.cfg)["status"], "failed")
        self.assertFalse(blob.exists())
        self.assertEqual([p.name for p in (self.repo / "quarantine").iterdir()], [blob.name + ".0"])

    def test_old_format_backup_under_wrong_name_is_rejected(self):
        self.backup()
        keys = crypto.Keys(keystore.load())
        legacy = {"v": 1, "created": engine.now_iso(), "host": "x", "sources": [str(self.src)],
                  "files": [], "stats": {"files": 0, "bytes": 0}, "warnings": []}
        (self.repo / "snapshots" / "20300101T000000.000000Z-abcdef.snap").write_bytes(
            keys.seal_snapshot(legacy))
        listed = {s["name"]: s for s in engine.list_snapshots(self.cfg)}
        self.assertIn("different name", listed["20300101T000000.000000Z-abcdef.snap"]["error"])

    def test_new_backup_survives_a_clock_that_went_backwards(self):
        from datetime import datetime as real
        from unittest import mock

        class Past(real):
            @classmethod
            def now(cls, tz=None):
                return real(2020, 1, 1, 12, 0, 0, tzinfo=tz)

        self.cfg["keep_last"] = 1
        self.backup()
        (self.src / "a.txt").write_bytes(b"changed after the clock was reset")
        with mock.patch.object(engine, "datetime", Past):
            snap = self.backup()["snapshot"]
        self.assertTrue(snap.startswith("2020"))
        self.assertIn(snap, [s["name"] for s in engine.list_snapshots(self.cfg)])
        res, restored = self.restore(snap)
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)

    def test_first_backup_into_an_existing_folder_leaves_its_files_alone(self):
        (self.repo / "tmp").mkdir(parents=True)
        (self.repo / "tmp" / "someone-elses.txt").write_text("keep me")
        self.backup()
        self.backup()
        self.assertEqual((self.repo / "tmp" / "someone-elses.txt").read_text(), "keep me")

    def test_restore_never_leaves_the_chosen_folder(self):
        dest = str(self.tmp / "out")
        root = os.path.abspath(dest)
        for evil in ("C:\\a\\..:\\..:\\b.txt", "Z:evil.txt", "a\\Z:\\evil.txt", "..\\..\\x", "/etc/../../x",
                     "\\\\server\\share\\f.txt", "C:\\ok\\. .\\f"):
            target = os.path.abspath(engine._restore_target(dest, evil))
            self.assertEqual(os.path.commonpath([target, root]), root, evil)
        with self.assertRaises(engine.BackupError):
            engine._restore_target(dest, "..\\..")


class Destinations(Base):
    def test_bootstrap_path_used_for_ssh(self):
        """The agent source is sent to a separate python, as it is over SSH."""
        self.cfg["destination"] = {"type": "process", "path": str(self.repo)}
        r = self.backup()
        res, restored = self.restore(r["snapshot"])
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")

    def test_backup_folder_inside_a_source_is_not_backed_up(self):
        inside = self.src / "backups"
        self.cfg["destination"] = {"type": "local", "folder": str(inside)}
        self.assertEqual(self.backup()["files"], 5)
        r = self.backup()  # the repository now exists inside the source
        self.assertEqual((r["files"], r["uploaded_bytes"]), (5, 0), r)

    def test_missing_drive_is_reported_plainly(self):
        if sys.platform == "win32":
            free = next(f"{c}:\\" for c in "ZYXWVUTSRQ" if not os.path.exists(f"{c}:\\"))
            self.cfg["destination"] = {"type": "local", "folder": free + "tuckvault"}
            self.assertIn("is not connected", self.backup(expect="failed")["error"])
        else:  # an unmounted disk: its mount folder is missing, and nothing is created in its place
            unplugged = self.tmp / "media" / "usb"
            self.cfg["destination"] = {"type": "local", "folder": str(unplugged / "tuckvault")}
            self.assertIn("is connected and mounted", self.backup(expect="failed")["error"])
            self.assertFalse(unplugged.exists())

    def test_relative_folder_is_refused(self):
        self.cfg["destination"] = {"type": "local", "folder": "backups"}
        self.assertIn("not a full path", self.backup(expect="failed")["error"])
        self.assertFalse(Path("backups").exists())

    def test_no_folder_set(self):
        self.cfg["destination"] = {"type": "local", "folder": ""}
        self.assertIn("No backup folder", self.backup(expect="failed")["error"])


class PerItemSchedules(Base):
    def setUp(self):
        super().setUp()
        self.vault = self.tmp / "vault.json"
        self.vault.write_bytes(b"vault v1")
        self.cfg["sources"].append(str(self.vault))
        self.cfg["source_schedules"] = {str(self.vault): {"enabled": True, "frequency": "hourly",
                                                          "interval_hours": 1}}
        self.vault_id = config.source_id(str(self.vault))

    def test_groups(self):
        groups = config.groups(self.cfg)
        self.assertEqual([g["id"] for g in groups], ["default", self.vault_id])
        self.assertEqual(groups[0]["sources"], [str(self.src)])
        self.assertEqual(groups[1]["schedule"]["frequency"], "hourly")
        self.assertEqual(groups[1]["schedule"]["time"], "20:00")  # missing fields are filled in
        self.cfg["schedule"]["enabled"] = False
        self.assertEqual([g["id"] for g in config.active_groups(self.cfg)], [self.vault_id])
        # on Windows the id does not depend on upper or lower case; on Linux those are other files
        same = config.source_id(str(self.vault).upper()) == self.vault_id
        self.assertEqual(same, sys.platform == "win32")

    def test_scheduled_run_backs_up_only_its_items(self):
        r = engine.run_backup(self.cfg, only=self.vault_id)
        self.assertEqual((r["status"], r["files"]), ("success", 1), r)
        r = engine.run_backup(self.cfg, only="default")
        self.assertEqual((r["status"], r["files"]), ("success", 5), r)
        snaps = engine.list_snapshots(self.cfg)
        self.assertEqual([s["sources"] for s in snaps], [[str(self.src)], [str(self.vault)]])
        # a full run afterwards re-reads nothing: both partial runs kept the file cache
        r = engine.run_backup(self.cfg)
        self.assertEqual((r["files"], r["new_files"], r["uploaded_bytes"]), (6, 0, 0), r)

    def test_leftover_schedule_is_skipped_quietly(self):
        r = engine.run_backup(self.cfg, only="0123456789")
        self.assertEqual(r["status"], "skipped")
        self.assertEqual(config.load_state()["history"], [])

    def test_frequent_item_does_not_push_out_rare_one(self):
        self.cfg["keep_last"] = 2
        weekly = engine.run_backup(self.cfg, only="default")["snapshot"]
        hourly = []
        for i in range(5):
            self.vault.write_bytes(b"vault v%d" % (i + 2))
            hourly.append(engine.run_backup(self.cfg, only=self.vault_id)["snapshot"])
        names = sorted(s["name"] for s in engine.list_snapshots(self.cfg))
        self.assertEqual(names, sorted([weekly] + hourly[-2:]))
        res, restored = self.restore(weekly)
        self.assertEqual(res["status"], "success", res)
        self.assert_same_tree(restored)
        self.assertEqual(engine.run_verify(self.cfg)["status"], "success")

    def test_second_run_waits_for_the_first(self):
        import threading
        import time
        with engine._Lock():
            self.assertEqual(engine.run_backup(self.cfg)["status"], "skipped")
            box = {}
            t = threading.Thread(target=lambda: box.update(r=engine.run_backup(self.cfg, wait=30)))
            t.start()
            time.sleep(0.5)
            self.assertTrue(t.is_alive())
        t.join(60)
        self.assertEqual(box["r"]["status"], "success", box)

    def test_task_per_schedule(self):
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        group = config.groups(self.cfg)[1]
        root = ET.fromstring(scheduler.task_xml(group["schedule"], group["id"]).split("?>", 1)[1])
        self.assertTrue(root.find("t:Actions/t:Exec/t:Arguments", ns).text.endswith("--only " + self.vault_id))
        self.assertEqual(root.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", ns).text, "PT1H")
        self.assertEqual(scheduler.task_for("default"), scheduler.task_name())
        self.assertEqual(scheduler.task_for(self.vault_id), scheduler.task_name() + "-" + self.vault_id)
        now = datetime(2026, 11, 9, 21, 30)
        self.assertEqual(scheduler.next_due(self.cfg, now), datetime(2026, 11, 9, 22, 0))


class ChosenFiles(Base):
    def test_restore_one_file_and_one_folder(self):
        snap = self.backup()["snapshot"]
        listed = {f["p"]: f["s"] for f in engine.snapshot_files(snap, self.cfg)}
        self.assertEqual(len(listed), 5)
        self.assertEqual(listed[str(self.src / "a.txt")], len(SECRET) * 50)

        out = self.tmp / "one"
        chosen = str(self.src / "a.txt")
        if sys.platform == "win32":  # Windows names ignore upper and lower case
            chosen = chosen.upper()
        else:  # Linux names do not
            r = engine.run_restore(snap, str(self.tmp / "no"), self.cfg, only=[chosen.upper()])
            self.assertEqual(r["status"], "failed", r)
        r = engine.run_restore(snap, str(out), self.cfg, only=[chosen])
        self.assertEqual((r["status"], r["files"]), ("success", 1), r)
        got = [p for p in out.rglob("*") if p.is_file()]
        self.assertEqual([p.name for p in got], ["a.txt"])
        self.assertEqual(got[0].read_bytes(), (self.src / "a.txt").read_bytes())

        out = self.tmp / "folder"
        r = engine.run_restore(snap, str(out), self.cfg, only=[str(self.src / "sub")])
        self.assertEqual((r["status"], r["files"]), ("success", 2), r)
        self.assertEqual(sorted(p.name for p in out.rglob("*") if p.is_file()),
                         sorted(["random.bin", "şifreler ünïcode.json"]))

    def test_a_name_that_only_starts_the_same_is_not_included(self):
        (self.src / "sub2").mkdir()
        (self.src / "sub2" / "other.txt").write_bytes(b"x")
        snap = self.backup()["snapshot"]
        r = engine.run_restore(snap, str(self.tmp / "o"), self.cfg, only=[str(self.src / "sub")])
        self.assertEqual(r["files"], 2, r)

    def test_nothing_matching_fails_plainly(self):
        snap = self.backup()["snapshot"]
        r = engine.run_restore(snap, str(self.tmp / "o"), self.cfg, only=["C:\\nowhere\\x.txt"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("None of the chosen files", r["error"])


class OverdueAlarm(Base):
    def setUp(self):
        super().setUp()
        self.cfg["schedule"].update(enabled=True, frequency="daily")
        self.t0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

    def test_not_overdue_until_two_runs_were_missed(self):
        self.assertEqual(watch.overdue(self.cfg, self.t0 + timedelta(days=30)), [])  # never scheduled
        watch.note_schedules(self.cfg, self.t0)
        self.assertEqual(watch.overdue(self.cfg, self.t0 + timedelta(days=1, hours=23)), [])
        late = watch.overdue(self.cfg, self.t0 + timedelta(days=3))
        self.assertEqual([(x["path"], x["days"], x["never"]) for x in late], [(str(self.src), 3, True)])
        self.assertIn("No successful backup of src for 3 days", watch.describe(late))

    def test_a_successful_backup_resets_the_clock(self):
        watch.note_schedules(self.cfg, self.t0)
        r = self.backup()  # records success "now", long after t0
        done = datetime.strptime(r["finished"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertEqual(watch.overdue(self.cfg, done + timedelta(days=1)), [])
        self.assertEqual(len(watch.overdue(self.cfg, done + timedelta(days=2, minutes=1))), 1)

    def test_failed_backups_do_not_count(self):
        watch.note_schedules(self.cfg, self.t0)
        self.cfg["destination"] = {"type": "local", "folder": ""}
        self.backup(expect="failed")
        self.assertEqual(len(watch.overdue(self.cfg, self.t0 + timedelta(days=3))), 1)

    def test_switching_off_and_on_starts_fresh(self):
        watch.note_schedules(self.cfg, self.t0)
        self.cfg["schedule"]["enabled"] = False
        watch.note_schedules(self.cfg, self.t0 + timedelta(days=40))
        self.assertEqual(watch.overdue(self.cfg, self.t0 + timedelta(days=41)), [])
        self.cfg["schedule"]["enabled"] = True
        watch.note_schedules(self.cfg, self.t0 + timedelta(days=50))
        self.assertEqual(watch.overdue(self.cfg, self.t0 + timedelta(days=51)), [])
        self.assertEqual(len(watch.overdue(self.cfg, self.t0 + timedelta(days=53))), 1)

    def test_slower_schedules_get_more_time(self):
        self.assertEqual(watch.allowed_gap({"frequency": "hourly", "interval_hours": 1}), timedelta(hours=6))
        self.assertEqual(watch.allowed_gap({"frequency": "hourly", "interval_hours": 12}), timedelta(hours=24))
        self.assertEqual(watch.allowed_gap({"frequency": "weekly"}), timedelta(days=14))
        self.assertEqual(watch.allowed_gap({"frequency": "monthly"}), timedelta(days=62))

    def test_watch_command_notifies(self):
        from unittest import mock
        from tuckvault import __main__ as cli, notify
        config.save(self.cfg)
        watch.note_schedules(self.cfg, datetime.now(timezone.utc) - timedelta(days=5))
        with mock.patch.object(notify, "notify") as shown:
            self.assertEqual(cli.main(["watch"]), 1)
            self.assertEqual(shown.call_args[0][0], "Backups have stopped")
            self.assertIn("5 days", shown.call_args[0][1])
        self.backup()
        with mock.patch.object(notify, "notify") as shown:
            self.assertEqual(cli.main(["watch"]), 0)
            shown.assert_not_called()

    def test_damaged_settings_are_announced_not_ignored(self):
        from unittest import mock
        from tuckvault import __main__ as cli, notify
        paths.config_file().write_text("{ this is not json", encoding="utf-8")
        self.assertTrue(config.is_damaged())
        for argv in (["watch"], ["run", "--scheduled"]):
            with mock.patch.object(notify, "notify") as shown:
                self.assertEqual(cli.main(argv), 1)
                self.assertIn("cannot read its settings", shown.call_args[0][0])
        paths.config_file().unlink()
        self.assertFalse(config.is_damaged())

    def test_watch_task(self):
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        root = ET.fromstring(scheduler.watch_xml().split("?>", 1)[1])
        self.assertTrue(root.find("t:Actions/t:Exec/t:Arguments", ns).text.endswith("watch"))
        self.assertEqual(root.find("t:Settings/t:StartWhenAvailable", ns).text, "false")
        self.assertIsNotNone(root.find("t:Triggers/t:CalendarTrigger/t:ScheduleByDay", ns))
        self.assertEqual(scheduler.task_for(scheduler.WATCH), scheduler.task_name() + "-watch")


class UpdateNotice(Base):
    def check(self, tag, now=1_000_000.0):
        from unittest import mock
        with mock.patch.object(updates, "_fetch_latest", return_value=tag) as fetch:
            return updates.newer_version(now=now), fetch.call_count

    def test_newer_older_and_odd_answers(self):
        from unittest import mock
        with mock.patch.object(updates, "__version__", "1.2.0"):
            day = 86400
            self.assertEqual(self.check("v1.3.0", now=1 * day * 10), ("1.3.0", 1))
            self.assertEqual(self.check("v1.2.0", now=2 * day * 10), (None, 1))
            self.assertEqual(self.check("v1.1.9", now=3 * day * 10), (None, 1))
            self.assertEqual(self.check("v1.10.0", now=4 * day * 10), ("1.10.0", 1))
            for odd in ("latest", "v1.2", "<script>", None, 5, "v1.2.0\n"):
                self.assertEqual(self.check(odd, now=(5 + len(str(odd))) * day * 100)[0], None, odd)

    def test_asks_at_most_once_a_day_and_survives_being_offline(self):
        from unittest import mock
        with mock.patch.object(updates, "__version__", "1.2.0"):
            self.assertEqual(self.check("v2.0.0", now=1000.0 + 86400), ("2.0.0", 1))
            self.assertEqual(self.check("v9.9.9", now=2000.0 + 86400), ("2.0.0", 0))  # remembered
            with mock.patch.object(updates, "_fetch_latest", side_effect=OSError("offline")):
                self.assertIsNone(updates.newer_version(now=10 * 86400.0))

    def test_only_ever_talks_to_the_fixed_https_address(self):
        self.assertTrue(updates.LATEST_URL.startswith("https://api.github.com/repos/"))
        self.assertTrue(updates.DOWNLOAD_PAGE.startswith("https://github.com/"))


class Renamed(unittest.TestCase):
    """The program was called Safekeep until 1.2."""

    def test_old_settings_folder_is_taken_over_once(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "Safekeep"
            old.mkdir()
            (old / "config.json").write_text('{"keep_last": 7}', encoding="utf-8")
            (old / "key.bin").write_bytes(b"k")
            (old / "safekeep.log").write_text("x", encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "TUCKVAULT_HOME"}
            env["APPDATA"] = env["HOME"] = tmp
            if sys.platform != "win32":  # Linux: ~/.config/safekeep becomes ~/.config/tuckvault
                (Path(tmp) / ".config").mkdir()
                old = old.rename(Path(tmp) / ".config" / "safekeep")
            with mock.patch.dict(os.environ, env, clear=True):
                home = paths.app_dir()
                self.assertEqual(home, old.with_name("Tuckvault" if sys.platform == "win32" else "tuckvault"))
                self.assertFalse(old.exists())
                self.assertEqual(sorted(p.name for p in home.iterdir()),
                                 ["config.json", "key.bin", "tuckvault.log"])
                self.assertEqual(config.load()["keep_last"], 7)
                old.mkdir()  # a later leftover must never replace what is in use
                (old / "config.json").write_text('{"keep_last": 1}', encoding="utf-8")
                self.assertEqual(config.load()["keep_last"], 7)

    def test_old_backups_stay_readable(self):
        # The key derivation label is part of the storage format and keeps the old name.
        import hashlib, hmac
        self.assertEqual(crypto._derive(b"m" * 32, b"blob"),
                         hmac.new(b"m" * 32, b"safekeep/v1/blob", hashlib.sha256).digest())

    def test_old_scheduled_tasks_are_found_only_outside_tests(self):
        from unittest import mock
        for name in ("TUCKVAULT_TASK_NAME", "TUCKVAULT_HOME"):
            with mock.patch.dict(os.environ, {name: "x"}),                     mock.patch.object(scheduler, "_tasks_named", side_effect=AssertionError("asked Windows")):
                self.assertEqual(scheduler._legacy(), set())
        self.assertEqual(scheduler.LEGACY_TASKS, "Safekeep\\Backup")


class SystemdTimers(unittest.TestCase):
    """The Linux side of scheduling. The unit text is checked on every platform."""

    def test_calendar_for_every_frequency(self):
        base = dict(config.DEFAULTS["schedule"], time="07:05", weekday="WED", monthday=9)
        cal = lambda **kw: scheduler._calendar(dict(base, **kw))  # noqa: E731
        self.assertEqual(cal(frequency="daily"), "*-*-* 07:05:00")
        self.assertEqual(cal(frequency="weekly"), "Wed *-*-* 07:05:00")
        self.assertEqual(cal(frequency="monthly"), "*-*-09 07:05:00")
        self.assertEqual(cal(frequency="hourly", interval_hours=6), "*-*-* 00,06,12,18:00:00")
        self.assertEqual(cal(frequency="hourly", interval_hours=24), "*-*-* 00:00:00")
        self.assertEqual(cal(frequency="hourly", interval_hours=1).count(","), 23)

    def test_next_run_matches_the_timer_on_linux(self):
        from unittest import mock
        base = dict(config.DEFAULTS["schedule"], frequency="hourly", interval_hours=5)
        with mock.patch.object(scheduler, "_LINUX", True):
            # 00, 05, 10, 15, 20, then 00 again: the day starts over at midnight
            self.assertEqual(scheduler.next_run(base, datetime(2026, 11, 9, 21, 30)), datetime(2026, 11, 10, 0, 0))
            self.assertEqual(scheduler.next_run(base, datetime(2026, 11, 9, 10, 0)), datetime(2026, 11, 9, 15, 0))
            self.assertEqual(scheduler.next_run(base, datetime(2026, 11, 9, 4, 59)), datetime(2026, 11, 9, 5, 0))

    def test_unit_text(self):
        from unittest import mock
        sched = dict(config.DEFAULTS["schedule"], frequency="daily", time="09:30")
        with mock.patch.object(scheduler, "_program", return_value=("/opt/my apps/py%thon", "-m tuckvault ", "/w d")), \
                mock.patch.dict(os.environ, {"TUCKVAULT_HOME": '/h/"x"$y'}):
            service, timer = scheduler.systemd_units(sched, "abc123")
        self.assertIn('ExecStart="/opt/my apps/py%%thon" "-m" "tuckvault" "run" "--scheduled" "--only" "abc123"\n',
                      service)
        self.assertIn("WorkingDirectory=/w d\n", service)
        self.assertIn('Environment="TUCKVAULT_HOME=/h/\\"x\\"$y"\n', service)
        self.assertIn("Type=oneshot", service)
        self.assertIn("OnCalendar=*-*-* 09:30:00\n", timer)
        self.assertIn("Persistent=true", timer)
        _, watch_timer = scheduler.systemd_watch_units()
        self.assertIn("OnCalendar=*-*-* 12:00:00\n", watch_timer)
        self.assertIn("Persistent=false", watch_timer)
        with self.assertRaises(OSError):
            scheduler._sd_quote("bad\nname")

    def test_unit_names(self):
        self.assertEqual(scheduler.unit_for("Tuckvault\\Backup"), "tuckvault-backup")
        self.assertEqual(scheduler.unit_for("Tuckvault\\Backup-1a2b3c4d5e"), "tuckvault-backup-1a2b3c4d5e")
        self.assertEqual(scheduler.unit_for("Tuckvault\\Backup-watch"), "tuckvault-backup-watch")


@unittest.skipUnless(sys.platform.startswith("linux"), "systemd timers are the Linux scheduler")
class SystemdForReal(Base):
    """Registers real timers with this user's systemd under a test name, then removes them."""

    def setUp(self):
        super().setUp()
        try:
            scheduler._check_systemd()
        except OSError as e:
            self.skipTest(str(e))
        os.environ["TUCKVAULT_TASK_NAME"] = f"Tuckvault\\UnitTest{os.getpid()}"
        self.addCleanup(os.environ.pop, "TUCKVAULT_TASK_NAME", None)
        self.addCleanup(scheduler.remove)

    def enabled(self, unit):
        return scheduler._systemctl("is-enabled", unit + ".timer").stdout.strip()

    def test_apply_change_and_remove(self):
        import subprocess
        self.cfg["schedule"].update(enabled=True, frequency="daily", time="03:00")
        config.save(self.cfg)
        scheduler.apply(self.cfg)
        base = scheduler.unit_for(scheduler.task_name())
        self.assertEqual(scheduler.installed(), {base, base + "-watch"})
        self.assertTrue(scheduler.is_active(self.cfg))
        self.assertEqual(self.enabled(base), "enabled")

        # a real scheduled run, started the way the timer starts it
        res = scheduler._systemctl("start", base + ".service")
        self.assertEqual(res.returncode, 0, res.stderr)
        last = config.load_state()["history"][0]
        self.assertEqual((last["kind"], last["status"]), ("backup", "success"), last)

        # an item with its own schedule gets its own timer; switching it off removes it again
        item = str(self.tmp / "vault.json")
        Path(item).write_bytes(b"v")
        self.cfg["sources"].append(item)
        self.cfg["source_schedules"] = {item: {"enabled": True, "frequency": "hourly", "interval_hours": 6}}
        scheduler.apply(self.cfg)
        own = base + "-" + config.source_id(item)
        self.assertIn(own, scheduler.installed())
        self.assertEqual(self.enabled(own), "enabled")
        self.cfg["source_schedules"] = {}
        scheduler.apply(self.cfg)
        self.assertNotIn(own, scheduler.installed())
        self.assertFalse((scheduler.unit_dir() / (own + ".service")).exists())

        listed = subprocess.run([scheduler.paths.trusted_exe("systemctl"), "--user", "list-timers", "--all"],
                                capture_output=True, text=True).stdout
        self.assertIn(base + ".timer", listed)

        scheduler.remove()
        self.assertEqual(scheduler.installed(), set())
        self.assertFalse(scheduler.is_active(self.cfg))


class Scheduling(unittest.TestCase):
    def test_task_xml_is_well_formed(self):
        for freq in config.FREQUENCIES:
            sched = dict(config.DEFAULTS["schedule"], frequency=freq)
            root = ET.fromstring(scheduler.task_xml(sched).split("?>", 1)[1])
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            self.assertEqual(root.find("t:Settings/t:StartWhenAvailable", ns).text, "true")
            self.assertIn("run --scheduled", root.find("t:Actions/t:Exec/t:Arguments", ns).text)

    def test_next_run(self):
        now = datetime(2026, 11, 9, 21, 30)  # a Monday
        base = dict(config.DEFAULTS["schedule"], time="20:00")
        self.assertIsNone(scheduler.next_run(dict(base, enabled=False), now))
        self.assertEqual(scheduler.next_run(dict(base, frequency="daily"), now), datetime(2026, 11, 10, 20, 0))
        self.assertEqual(scheduler.next_run(dict(base, frequency="weekly", weekday="SUN"), now),
                         datetime(2026, 11, 15, 20, 0))
        self.assertEqual(scheduler.next_run(dict(base, frequency="weekly", weekday="MON"), now),
                         datetime(2026, 11, 16, 20, 0))
        self.assertEqual(scheduler.next_run(dict(base, frequency="monthly", monthday=1), now),
                         datetime(2026, 12, 1, 20, 0))
        self.assertEqual(scheduler.next_run(dict(base, frequency="hourly", interval_hours=6), now),
                         datetime(2026, 11, 10, 0, 0))


if __name__ == "__main__":
    unittest.main()
