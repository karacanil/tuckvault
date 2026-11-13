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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from safekeep import config, crypto, engine, keystore, paths, scheduler  # noqa: E402

SECRET = b"TOP-SECRET-MARKER-9f2c "


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="safekeep-test-"))
        os.environ["SAFEKEEP_HOME"] = str(self.tmp / "home")
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
        os.environ.pop("SAFEKEEP_HOME", None)
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
        from safekeep.remote import Remote, RemoteError
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
        # the id does not depend on how the path is spelled
        self.assertEqual(config.source_id(str(self.vault).upper()), self.vault_id)

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
