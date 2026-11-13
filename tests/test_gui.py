"""Drives the real window through a backup, a restore and a check.

Dialogs are replaced by recorders so the test never blocks; everything
else (widgets, worker thread, progress queue, engine, storage agent) is
the real thing against a repository in a temp folder.
"""

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from safekeep import config, engine, gui  # noqa: E402


class GuiFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="safekeep-gui-"))
        os.environ["SAFEKEEP_HOME"] = str(self.tmp / "home")
        os.environ["SAFEKEEP_TASK_NAME"] = r"Safekeep\GuiTest"
        self.src = self.tmp / "documents"
        self.src.mkdir()
        (self.src / "vault.json").write_bytes(b'{"secret": 1}' * 100)
        (self.src / "notes.txt").write_bytes(b"hello")
        cfg = config.load()
        cfg["destination"] = {"type": "local", "host": "", "path": str(self.tmp / "repo")}
        cfg["schedule"]["enabled"] = False
        config.save(cfg)
        self.dialogs = []
        patches = [mock.patch.object(gui.messagebox, name,
                                     side_effect=lambda *a, _n=name, **k: self.dialogs.append((_n, a)) or True)
                   for name in ("showinfo", "showwarning", "showerror", "askyesno")]
        if hasattr(os, "startfile"):
            patches.append(mock.patch.object(os, "startfile"))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        try:
            self.app = gui.App()
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.app.update()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        for var in ("SAFEKEEP_HOME", "SAFEKEEP_TASK_NAME"):
            os.environ.pop(var, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_idle(self, timeout=60):
        end = time.time() + timeout
        self.app.update()
        while self.app.busy:
            self.assertLess(time.time(), end, "job did not finish")
            self.app.update()
            time.sleep(0.02)
        self.app.update()

    def test_layout_fits_on_every_tab(self):
        app = self.app
        for index in range(app.notebook.index("end")):
            app.notebook.select(index)
            app.update()
            right = app.winfo_rootx() + app.winfo_width()
            bottom = app.winfo_rooty() + app.winfo_height()
            stack = [app]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if not w.winfo_viewable() or isinstance(w, (tk.Toplevel, tk.Tk)):
                    continue
                name = f"tab {index}: {w.winfo_class()} {w}"
                self.assertLessEqual(w.winfo_rootx() + w.winfo_width(), right + 1, name)
                self.assertLessEqual(w.winfo_rooty() + w.winfo_height(), bottom + 1, name)
                if w.winfo_class() in ("TButton", "TLabel", "TCheckbutton"):
                    self.assertGreaterEqual(w.winfo_width(), w.winfo_reqwidth() - 1, name + " is clipped")
                    self.assertGreaterEqual(w.winfo_height(), w.winfo_reqheight() - 1, name + " is clipped")

    def test_schedule_widgets_follow_frequency(self):
        app = self.app
        app.v_enabled.set(True)
        ed = app.schedule
        for freq, visible in (("hourly", ed.w_hours), ("weekly", ed.w_day), ("monthly", ed.w_monthday)):
            ed.v_freq.set(gui.FREQ_LABELS[freq])
            app._sync_schedule_widgets()
            app.update()
            self.assertTrue(visible[1].winfo_ismapped(), freq)
            self.assertEqual(ed.w_time[1].winfo_ismapped(), freq != "hourly")
        ed.v_freq.set(gui.FREQ_LABELS["weekly"])
        ed.v_day.set("Wednesday")
        ed.v_hh.set("07")
        ed.v_mm.set("5")
        sched = app._collect()["schedule"]
        self.assertEqual((sched["frequency"], sched["weekday"], sched["time"]), ("weekly", "WED", "07:05"))

    def test_backup_restore_and_check_through_the_window(self):
        app = self.app
        app._backup_now()  # nothing selected yet
        self.assertEqual(self.dialogs[-1][0], "showinfo")
        self.assertFalse(app.busy)

        app._add_paths([str(self.src)])
        app._add_paths([str(self.src)])  # duplicates are ignored
        self.assertEqual(len(app.sources.get_children()), 1)
        self.dialogs.clear()
        app._backup_now()
        self.assertTrue(app.busy)
        self.assertEqual(str(app.action_buttons[0].cget("state")), "disabled")
        self.wait_idle()
        self.assertEqual(self.dialogs, [])
        self.assertIn("Backup complete: 2 files", app.status.cget("text"))
        self.assertEqual(config.load()["sources"], [str(self.src)])
        self.assertTrue(app.key_banner.winfo_ismapped() or app.key_banner.winfo_manager() == "grid")
        self.assertEqual(len(app.activity.get_children()), 1)

        app.notebook.select(1)
        app._load_snapshots()
        self.wait_idle()
        rows = app.snapshots.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(app.snapshots.item(rows[0], "values")[1]), "2")

        out = self.tmp / "restored"
        out.mkdir()
        app.snapshots.selection_set(rows[0])
        with mock.patch.object(gui.filedialog, "askdirectory", return_value=str(out)):
            app._restore_selected()
        self.wait_idle()
        self.assertEqual(self.dialogs, [])
        restored = Path(engine._restore_target(str(out), str(self.src)))
        self.assertEqual((restored / "vault.json").read_bytes(), (self.src / "vault.json").read_bytes())

        app._verify_now()
        self.wait_idle()
        self.assertEqual(app.status.cget("text"), "All stored backups are intact.")

        # damage is reported in a dialog, not silently
        blob = next(p for p in (self.tmp / "repo" / "blobs").rglob("*") if p.is_file())
        blob.write_bytes(b"garbage")
        app._verify_now()
        self.wait_idle()
        self.assertEqual(self.dialogs[-1][0], "showerror")
        self.assertIn("damaged", self.dialogs[-1][1][1])

        # recovery key tab
        app._show_key()
        self.assertEqual(len(app.v_key.get().replace("-", "")), 56)
        app.v_key_saved.set(True)
        app._key_saved_changed()
        self.assertTrue(config.load()["recovery_key_saved"])
        self.assertEqual(app.key_banner.winfo_manager(), "")

    def test_item_can_have_its_own_schedule(self):
        app = self.app
        other = self.tmp / "vault.kdbx"
        other.write_bytes(b"x")
        app._add_paths([str(self.src), str(other)])
        rows = app.sources.get_children()
        self.assertEqual(app.sources.set(rows[1], "often"), "General schedule")

        app._edit_item_schedule()  # nothing selected
        self.assertEqual(self.dialogs[-1][0], "showinfo")

        app.sources.selection_set(rows[1])
        app._edit_item_schedule()
        dialog, mode, editor, ok = app._item_dialog
        app.update()
        self.assertEqual(mode.get(), "general")
        mode.set("own")
        editor.v_freq.set(gui.FREQ_LABELS["daily"])
        editor.v_hh.set("09")
        editor.v_mm.set("30")
        ok()
        self.assertEqual(app.sources.set(rows[1], "often"), "Every day at 09:30")
        self.assertEqual(app.sources.set(rows[0], "often"), "General schedule")

        cfg = app._collect()
        groups = config.groups(cfg)
        self.assertEqual([g["sources"] for g in groups], [[str(self.src)], [str(other)]])
        self.assertEqual((groups[1]["schedule"]["frequency"], groups[1]["schedule"]["time"]), ("daily", "09:30"))

        # reopening shows the stored choice; "manual" switches automatic runs off for it
        app._edit_item_schedule()
        dialog, mode, editor, ok = app._item_dialog
        self.assertEqual(mode.get(), "own")
        mode.set("manual")
        ok()
        self.assertEqual(app.sources.set(rows[1], "often"), "Only with Back up now")
        self.assertEqual(config.active_groups(app._collect()), [])

        # removing the item drops its schedule on save
        config.save(app._collect())
        self.assertIn(str(other), config.load()["source_schedules"])
        app.sources.selection_set(rows[1])
        app._remove_sources()
        config.save(app._collect())
        self.assertEqual(config.load()["source_schedules"], {})

    def test_unreachable_machine_shows_an_error(self):
        app = self.app
        app.cfg["destination"]["type"] = "ssh"
        app.v_host.set("-nonexistent")
        app._test_connection()
        self.wait_idle()
        self.assertEqual(self.dialogs[-1][0], "showerror")


if __name__ == "__main__":
    unittest.main()
