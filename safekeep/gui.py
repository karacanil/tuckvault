"""The Safekeep window (tkinter, no extra dependencies)."""

import os
import queue
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk

from . import __version__, config, engine, keystore, paths, scheduler

FREQ_LABELS = {"hourly": "Every few hours", "daily": "Every day",
               "weekly": "Every week", "monthly": "Every month"}
DAY_LABELS = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
              "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
STATUS_LABELS = {"success": "OK", "warning": "Finished with problems", "failed": "Failed",
                 "skipped": "Skipped"}
KIND_LABELS = {"backup": "Backup", "verify": "Check", "restore": "Restore"}
PAD = 8


def local_time(iso: str) -> str:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return "—"


def human_size(n) -> str:
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def key_from(value: str, labels: dict) -> str:
    return next((k for k, v in labels.items() if v == value), next(iter(labels)))


class ScheduleEditor(ttk.Frame):
    """Frequency picker plus the detail fields that belong to the choice."""

    def __init__(self, parent):
        super().__init__(parent)
        self.v_freq = tk.StringVar()
        self.v_day = tk.StringVar()
        self.v_monthday = tk.StringVar()
        self.v_hours = tk.StringVar()
        self.v_hh = tk.StringVar()
        self.v_mm = tk.StringVar()
        self.w_freq = ttk.Combobox(self, textvariable=self.v_freq, state="readonly", width=16,
                                   values=list(FREQ_LABELS.values()))
        self.w_freq.pack(side="left", padx=(0, PAD))
        self.w_freq.bind("<<ComboboxSelected>>", lambda e: self.sync())
        d = ttk.Frame(self)
        d.pack(side="left")
        self.w_hours = (ttk.Label(d, text="every"),
                        ttk.Spinbox(d, from_=1, to=24, width=4, textvariable=self.v_hours),
                        ttk.Label(d, text="hours"))
        self.w_day = (ttk.Label(d, text="on"),
                      ttk.Combobox(d, textvariable=self.v_day, state="readonly", width=11,
                                   values=list(DAY_LABELS.values())))
        self.w_monthday = (ttk.Label(d, text="on day"),
                           ttk.Spinbox(d, from_=1, to=28, width=4, textvariable=self.v_monthday))
        self.w_time = (ttk.Label(d, text="at"),
                       ttk.Spinbox(d, from_=0, to=23, width=3, format="%02.0f", textvariable=self.v_hh),
                       ttk.Label(d, text=":"),
                       ttk.Spinbox(d, from_=0, to=59, width=3, format="%02.0f", textvariable=self.v_mm))
        self.enabled = True

    def set(self, sched: dict):
        self.v_freq.set(FREQ_LABELS.get(sched.get("frequency"), FREQ_LABELS["weekly"]))
        self.v_day.set(DAY_LABELS.get(sched.get("weekday"), "Sunday"))
        self.v_monthday.set(str(sched.get("monthday", 1)))
        self.v_hours.set(str(sched.get("interval_hours", 6)))
        hh, _, mm = str(sched.get("time", "20:00")).partition(":")
        self.v_hh.set(hh.zfill(2))
        self.v_mm.set((mm or "0").zfill(2))
        self.sync()

    def get(self) -> dict:
        return dict(
            frequency=key_from(self.v_freq.get(), FREQ_LABELS),
            weekday=key_from(self.v_day.get(), DAY_LABELS),
            monthday=_int(self.v_monthday, 1, 28, 1),
            interval_hours=_int(self.v_hours, 1, 24, 6),
            time=f"{_int(self.v_hh, 0, 23, 20):02d}:{_int(self.v_mm, 0, 59, 0):02d}",
        )

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        self.sync()

    def sync(self):
        groups = (self.w_hours, self.w_day, self.w_monthday, self.w_time)
        for group in groups:
            for w in group:
                w.pack_forget()
        freq = key_from(self.v_freq.get(), FREQ_LABELS)
        shown = {"hourly": [self.w_hours], "daily": [self.w_time],
                 "weekly": [self.w_day, self.w_time], "monthly": [self.w_monthday, self.w_time]}[freq]
        for group in shown:
            for w in group:
                w.pack(side="left", padx=2)
        self.w_freq.configure(state="readonly" if self.enabled else "disabled")
        for group in groups:
            for w in group:
                if isinstance(w, ttk.Combobox):
                    w.configure(state="readonly" if self.enabled else "disabled")
                elif isinstance(w, ttk.Spinbox):
                    w.configure(state="normal" if self.enabled else "disabled")


def _int(var, low, high, default):
    try:
        return max(low, min(high, int(float(var.get()))))
    except ValueError:
        return default


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Safekeep")
        self.geometry("800x640")
        self.minsize(720, 600)
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass
        self.cfg = config.load()
        self.events = queue.Queue()
        self.busy = False
        self.action_buttons = []

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=PAD, pady=(PAD, 0))
        self._build_backup_tab()
        self._build_restore_tab()
        self._build_activity_tab()
        self._build_security_tab()
        self._build_status_bar()

        self._load_into_widgets()
        self._refresh_summary()
        self._refresh_activity()
        self._poll_id = self.after(100, self._poll)

    def destroy(self):
        self.after_cancel(self._poll_id)
        super().destroy()

    # -- construction ----------------------------------------------------
    def _build_backup_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  Backup  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        self.key_banner = ttk.Label(tab, foreground="#9a5b00", cursor="hand2", wraplength=740,
                                    text="Your recovery key is not saved yet. Without it the backups "
                                         "cannot be read if this PC is lost. Click here to save it.")
        self.key_banner.bind("<Button-1>", lambda e: self.notebook.select(3))

        box = ttk.LabelFrame(tab, text="What to back up", padding=PAD)
        box.grid(row=1, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.sources = ttk.Treeview(box, columns=("item", "often"), show="headings",
                                    selectmode="extended", height=6)
        self.sources.heading("item", text="File or folder", anchor="w")
        self.sources.heading("often", text="How often", anchor="w")
        self.sources.column("item", width=380, anchor="w", stretch=True)
        self.sources.column("often", width=210, anchor="w", stretch=False)
        self.sources.grid(row=0, column=0, sticky="nsew")
        self.sources.bind("<Double-1>",
                          lambda e: self._edit_item_schedule() if self.sources.selection() else None)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.sources.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.sources.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(box)
        buttons.grid(row=0, column=2, sticky="n", padx=(PAD, 0))
        ttk.Button(buttons, text="Add folder…", command=self._add_folder).pack(fill="x")
        ttk.Button(buttons, text="Add files…", command=self._add_files).pack(fill="x", pady=4)
        ttk.Button(buttons, text="Remove", command=self._remove_sources).pack(fill="x")
        ttk.Button(buttons, text="How often…", command=self._edit_item_schedule).pack(fill="x", pady=(12, 0))

        box = ttk.LabelFrame(tab, text="General schedule (for every item without its own)", padding=PAD)
        box.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        self.v_enabled = tk.BooleanVar()
        ttk.Checkbutton(box, text="Back up automatically", variable=self.v_enabled,
                        command=self._sync_schedule_widgets).pack(side="left")
        self.schedule = ScheduleEditor(box)
        self.schedule.pack(side="left", padx=(PAD * 2, 0))

        box = ttk.LabelFrame(tab, text="Where the backups go", padding=PAD)
        box.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        box.columnconfigure(1, weight=1)
        self.v_host = tk.StringVar()
        self.v_path = tk.StringVar()
        self.v_keep = tk.StringVar()
        ttk.Label(box, text="Backup machine (SSH name)").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.v_host).grid(row=0, column=1, sticky="ew", padx=PAD)
        b = ttk.Button(box, text="Test connection", command=self._test_connection)
        b.grid(row=0, column=2)
        self.action_buttons.append(b)
        ttk.Label(box, text="Folder on that machine").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(box, textvariable=self.v_path).grid(row=1, column=1, sticky="ew", padx=PAD, pady=(4, 0))
        keep = ttk.Frame(box)
        keep.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(keep, text="Keep the last").pack(side="left")
        ttk.Spinbox(keep, from_=1, to=999, width=5, textvariable=self.v_keep).pack(side="left", padx=4)
        ttk.Label(keep, text="backups of each item; older ones are removed automatically").pack(side="left")

        bottom = ttk.Frame(tab)
        bottom.grid(row=4, column=0, sticky="ew", pady=(PAD, 0))
        bottom.columnconfigure(0, weight=1)
        self.summary = ttk.Label(bottom, justify="left")
        self.summary.grid(row=0, column=0, sticky="w")
        b = ttk.Button(bottom, text="Save settings", command=self._save)
        b.grid(row=0, column=1, padx=(0, PAD))
        self.action_buttons.append(b)
        b = ttk.Button(bottom, text="Back up now", command=self._backup_now)
        b.grid(row=0, column=2)
        self.action_buttons.append(b)

    def _build_restore_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  Restore  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="Backups stored on the backup machine. Restoring copies a whole backup "
                            "into a folder you choose; nothing is overwritten in place.",
                  wraplength=740, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        cols = ("when", "files", "size", "notes")
        self.snapshots = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (("when", "Taken", 160), ("files", "Files", 80),
                                 ("size", "Size", 100), ("notes", "Contains", 360)):
            self.snapshots.heading(col, text=text, anchor="w")
            self.snapshots.column(col, width=width, anchor="w", stretch=(col == "notes"))
        self.snapshots.grid(row=1, column=0, sticky="nsew", pady=PAD)
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.snapshots.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=PAD)
        self.snapshots.configure(yscrollcommand=scroll.set)
        row = ttk.Frame(tab)
        row.grid(row=2, column=0, columnspan=2, sticky="ew")
        for text, cmd in (("Refresh list", self._load_snapshots),
                          ("Restore selected…", self._restore_selected),
                          ("Check all backups now", self._verify_now)):
            b = ttk.Button(row, text=text, command=cmd)
            b.pack(side="left", padx=(0, PAD))
            self.action_buttons.append(b)

    def _build_activity_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(tab, text="  Activity  ")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        cols = ("when", "what", "result", "details")
        self.activity = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (("when", "When", 150), ("what", "What", 70),
                                 ("result", "Result", 150), ("details", "Details", 360)):
            self.activity.heading(col, text=text, anchor="w")
            self.activity.column(col, width=width, anchor="w", stretch=(col == "details"))
        self.activity.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.activity.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.activity.configure(yscrollcommand=scroll.set)
        self.activity.bind("<<TreeviewSelect>>", self._show_activity_detail)
        self.activity_detail = tk.Text(tab, height=6, wrap="word", state="disabled",
                                       relief="flat", background=self.cget("background"))
        self.activity_detail.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))
        ttk.Button(tab, text="Open log file", command=self._open_log).grid(
            row=2, column=0, sticky="w", pady=(PAD, 0))

    def _build_security_tab(self):
        tab = ttk.Frame(self.notebook, padding=PAD * 2)
        self.notebook.add(tab, text="  Security  ")
        text = (
            "How your backups are protected\n\n"
            "•  Everything is encrypted on this PC (AES-256) before it is sent. The backup "
            "machine only ever stores unreadable data, including file names.\n"
            "•  Every stored piece is named after its SHA-256 fingerprint. The backup machine "
            "re-checks those fingerprints regularly, so silent disk damage or tampering is "
            "detected and reported to you.\n"
            "•  Older backups are kept, so a file that was deleted or overwritten by mistake "
            "can be taken from an earlier backup.\n\n"
            "The recovery key\n\n"
            "The encryption key lives on this PC, locked to your Windows account. If this PC "
            "or account is lost, the recovery key below is the only way to read your backups. "
            "Keep a copy somewhere that is not this PC and not the backup machine, for example "
            "on paper or in your password manager."
        )
        ttk.Label(tab, text=text, wraplength=720, justify="left").pack(anchor="w")
        self.v_key = tk.StringVar()
        ttk.Entry(tab, textvariable=self.v_key, state="readonly", font=("Consolas", 10),
                  justify="center").pack(fill="x", pady=(PAD * 2, PAD))
        row = ttk.Frame(tab)
        row.pack(anchor="w")
        ttk.Button(row, text="Show recovery key", command=self._show_key).pack(side="left")
        ttk.Button(row, text="Copy", command=self._copy_key).pack(side="left", padx=PAD)
        ttk.Button(row, text="Save to file…", command=self._save_key).pack(side="left")
        ttk.Button(row, text="Import a recovery key…", command=self._import_key).pack(
            side="left", padx=(PAD * 3, 0))
        self.v_key_saved = tk.BooleanVar()
        ttk.Checkbutton(tab, text="I have stored my recovery key somewhere safe",
                        variable=self.v_key_saved, command=self._key_saved_changed).pack(
            anchor="w", pady=(PAD * 2, 0))
        ttk.Label(tab, text=f"Safekeep {__version__}", foreground="#777").pack(anchor="e", side="bottom")

    def _build_status_bar(self):
        bar = ttk.Frame(self, padding=(PAD, 6))
        bar.pack(fill="x", side="bottom")
        bar.columnconfigure(0, weight=1)
        self.status = ttk.Label(bar, text="Ready", anchor="w")
        self.status.grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(bar, length=220, mode="determinate")
        self.progress.grid(row=0, column=1, padx=(PAD, 0))

    # -- settings <-> widgets ---------------------------------------------
    def _load_into_widgets(self):
        cfg, sched = self.cfg, self.cfg["schedule"]
        self.sources.delete(*self.sources.get_children())
        for path in cfg["sources"]:
            self.sources.insert("", "end", values=(path, self._often_label(path)))
        self.v_enabled.set(bool(sched.get("enabled")))
        self.schedule.set(sched)
        self.v_host.set(cfg["destination"].get("host", ""))
        self.v_path.set(cfg["destination"].get("path", ""))
        self.v_keep.set(str(cfg.get("keep_last", 30)))
        self.v_key_saved.set(bool(cfg.get("recovery_key_saved")))
        self._sync_schedule_widgets()
        self._sync_key_banner()

    def _sync_schedule_widgets(self):
        self.schedule.set_enabled(self.v_enabled.get())

    def _source_paths(self) -> list:
        return [self.sources.set(i, "item") for i in self.sources.get_children()]

    def _often_label(self, path: str) -> str:
        own = config.own_schedule(self.cfg, path)
        if own is None:
            return "General schedule"
        return scheduler.describe(own) if own.get("enabled") else "Only with Back up now"

    def _sync_key_banner(self):
        if keystore.load() is not None and not self.cfg.get("recovery_key_saved"):
            self.key_banner.grid(row=0, column=0, sticky="w", pady=(0, PAD))
        else:
            self.key_banner.grid_forget()

    def _collect(self) -> dict:
        cfg = self.cfg
        cfg["sources"] = self._source_paths()
        cfg["destination"].update(host=self.v_host.get().strip(), path=self.v_path.get().strip())
        cfg["keep_last"] = _int(self.v_keep, 1, 999, 30)
        cfg["schedule"].update(self.schedule.get(), enabled=bool(self.v_enabled.get()))
        return cfg

    def _save(self, quiet=False) -> bool:
        cfg = self._collect()
        if not cfg["destination"]["host"] and cfg["destination"].get("type") != "local":
            messagebox.showwarning("Safekeep", "Enter the SSH name of the backup machine.")
            return False
        config.save(cfg)
        try:
            scheduler.apply(cfg)
        except OSError as e:
            messagebox.showerror("Safekeep", f"Settings were saved, but the automatic schedule "
                                             f"could not be set.\n\n{e}")
            return False
        self._load_into_widgets()
        self._refresh_summary()
        if not quiet:
            active = config.active_groups(cfg)
            if not active:
                what = "Automatic backups are off"
            elif len(active) == 1:
                what = scheduler.describe(active[0]["schedule"])
            else:
                what = f"{len(active)} automatic schedules are active"
            self.status.configure(text=f"Settings saved. {what}.")
        return True

    # -- sources ---------------------------------------------------------
    def _add_paths(self, new_paths):
        have = {os.path.normcase(p) for p in self._source_paths()}
        for p in new_paths:
            p = os.path.normpath(p)
            if os.path.normcase(p) not in have:
                self.sources.insert("", "end", values=(p, self._often_label(p)))
                have.add(os.path.normcase(p))

    def _add_folder(self):
        path = filedialog.askdirectory(parent=self, title="Choose a folder to back up")
        if path:
            self._add_paths([path])

    def _add_files(self):
        self._add_paths(filedialog.askopenfilenames(parent=self, title="Choose files to back up"))

    def _remove_sources(self):
        self.sources.delete(*self.sources.selection())

    def _set_item_schedule(self, item_paths, sched):
        """sched: a schedule dict, or None to follow the general schedule."""
        table = self.cfg.setdefault("source_schedules", {})
        want = {os.path.normcase(os.path.abspath(p)) for p in item_paths}
        for key in [k for k in table if os.path.normcase(os.path.abspath(k)) in want]:
            del table[key]
        if sched is not None:
            for p in item_paths:
                table[p] = dict(sched)
        for iid in self.sources.get_children():
            path = self.sources.set(iid, "item")
            self.sources.set(iid, "often", self._often_label(path))
        self.status.configure(text="Click Save settings to apply the new schedule.")

    def _edit_item_schedule(self):
        chosen = [self.sources.set(i, "item") for i in self.sources.selection()]
        if not chosen:
            messagebox.showinfo("Safekeep", "Select one or more items in the list first.")
            return
        current = config.own_schedule(self.cfg, chosen[0])
        dialog = tk.Toplevel(self)
        dialog.title("How often")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=PAD * 2)
        frame.pack(fill="both", expand=True)
        title = chosen[0] if len(chosen) == 1 else f"{len(chosen)} selected items"
        ttk.Label(frame, text=title, wraplength=460, justify="left",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, PAD))
        mode = tk.StringVar(value="general" if current is None
                            else ("own" if current.get("enabled") else "manual"))
        editor = ScheduleEditor(frame)
        editor.set(current or self.cfg["schedule"])

        def sync():
            editor.set_enabled(mode.get() == "own")

        general = scheduler.describe(dict(self.cfg["schedule"], **self.schedule.get(),
                                          enabled=bool(self.v_enabled.get())))
        general = general[0].lower() + general[1:]
        ttk.Radiobutton(frame, text=f"Follow the general schedule ({general})",
                        variable=mode, value="general", command=sync).pack(anchor="w")
        ttk.Radiobutton(frame, text="Its own schedule:", variable=mode, value="own",
                        command=sync).pack(anchor="w", pady=(4, 0))
        editor.pack(anchor="w", padx=(PAD * 3, 0), pady=4)
        ttk.Radiobutton(frame, text="Never automatically, only with Back up now",
                        variable=mode, value="manual", command=sync).pack(anchor="w")
        sync()

        def ok():
            sched = {"general": None,
                     "own": dict(editor.get(), enabled=True),
                     "manual": dict(editor.get(), enabled=False)}[mode.get()]
            dialog.destroy()
            self._set_item_schedule(chosen, sched)

        row = ttk.Frame(frame)
        row.pack(anchor="e", pady=(PAD * 2, 0))
        ttk.Button(row, text="OK", command=ok).pack(side="left", padx=PAD)
        ttk.Button(row, text="Cancel", command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", lambda e: ok())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.grab_set()
        self._item_dialog = (dialog, mode, editor, ok)  # reachable for tests

    # -- summaries -------------------------------------------------------
    def _refresh_summary(self):
        state = config.load_state()
        last = state.get("last_run")
        lines = ["Last successful backup: " + local_time(state.get("last_success"))]
        if last and last.get("kind") == "backup" and last.get("status") == "failed":
            lines.append("Last attempt failed: " + local_time(last.get("finished")))
        nxt = scheduler.next_due(self.cfg)
        if nxt and scheduler.is_active(self.cfg):
            lines.append("Next automatic backup: " + nxt.strftime("%d %b %Y %H:%M"))
        elif nxt:
            lines.append("Automatic backups are not active yet. Click Save settings.")
        else:
            lines.append("Automatic backups are off.")
        self.summary.configure(text="\n".join(lines))

    def _refresh_activity(self):
        self.activity.delete(*self.activity.get_children())
        self._history = config.load_state()["history"]
        for i, r in enumerate(self._history):
            if r.get("kind") == "backup" and r.get("status") in ("success", "warning"):
                detail = (f"{r.get('files', 0)} files, {human_size(r.get('bytes'))}; "
                          f"{human_size(r.get('uploaded_bytes'))} sent")
            elif r.get("kind") == "restore" and r.get("files"):
                detail = f"{r.get('files', 0)} files, {human_size(r.get('bytes'))}"
            else:
                detail = ""
            if r.get("error"):
                detail = r["error"]
            elif r.get("warnings"):
                detail += f"; {len(r['warnings'])} problems"
            self.activity.insert("", "end", iid=str(i), values=(
                local_time(r.get("finished")), KIND_LABELS.get(r.get("kind"), r.get("kind")),
                STATUS_LABELS.get(r.get("status"), r.get("status")), detail))

    def _show_activity_detail(self, _event=None):
        sel = self.activity.selection()
        text = ""
        if sel:
            r = self._history[int(sel[0])]
            text = "\n".join(filter(None, [r.get("error")] + list(r.get("warnings", []))))
        self.activity_detail.configure(state="normal")
        self.activity_detail.delete("1.0", "end")
        self.activity_detail.insert("1.0", text)
        self.activity_detail.configure(state="disabled")

    def _open_log(self):
        path = paths.log_file()
        if not path.exists():
            messagebox.showinfo("Safekeep", "There is no log yet.")
        elif hasattr(os, "startfile"):
            os.startfile(path)

    # -- background jobs -------------------------------------------------
    def _start(self, fn, on_done):
        if self.busy:
            return
        self.busy = True
        for b in self.action_buttons:
            b.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)

        def report(phase, done, total, text):
            self.events.put(("progress", (done, total, text)))

        def work():
            try:
                self.events.put(("done", (on_done, fn(report), None)))
            except Exception as e:  # shown to the user, never lost in a thread
                self.events.put(("done", (on_done, None, e)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        latest = None
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    latest = data
                else:
                    self._finish(*data)
        except queue.Empty:
            pass
        if latest and self.busy:
            done, total, text = latest
            if total:
                if str(self.progress.cget("mode")) != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress.configure(maximum=total, value=done)
            if len(text) > 80:
                text = "…" + text[-79:]
            self.status.configure(text=text)
        self._poll_id = self.after(100, self._poll)

    def _finish(self, on_done, value, error):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        for b in self.action_buttons:
            b.configure(state="normal")
        self._refresh_summary()
        self._refresh_activity()
        self._sync_key_banner()
        if error is not None:
            self.status.configure(text="Failed")
            messagebox.showerror("Safekeep", str(error))
        else:
            on_done(value)

    def _report(self, result, ok_text):
        status = result.get("status")
        if status == "success":
            self.status.configure(text=ok_text)
        elif status == "skipped":
            self.status.configure(text=result.get("error", "Skipped"))
            messagebox.showinfo("Safekeep", result.get("error", "Skipped"))
        else:
            self.status.configure(text=STATUS_LABELS.get(status, "Failed"))
            lines = [result.get("error") or "Some files had problems:"] + result.get("warnings", [])[:8]
            box = messagebox.showwarning if status == "warning" else messagebox.showerror
            box("Safekeep", "\n\n".join(l for l in lines if l))

    # -- actions ---------------------------------------------------------
    def _backup_now(self):
        if not self._save(quiet=True):
            return
        if not self.cfg["sources"]:
            messagebox.showinfo("Safekeep", "Add at least one file or folder to back up first.")
            return
        cfg = self.cfg

        def done(result):
            self._report(result, f"Backup complete: {result.get('files', 0)} files, "
                                 f"{human_size(result.get('uploaded_bytes'))} sent.")
            if self.notebook.index("current") == 1:
                self._load_snapshots()

        self._start(lambda progress: engine.run_backup(cfg, progress=progress), done)

    def _test_connection(self):
        cfg = self._collect()
        self._start(lambda progress: engine.test_connection(cfg),
                    lambda text: self.status.configure(text=text))

    def _load_snapshots(self):
        cfg = self._collect()

        def done(snaps):
            self.snapshots.delete(*self.snapshots.get_children())
            for s in snaps:
                if "error" in s:
                    values = (s["name"][:15], "", "", "Unreadable: " + s["error"])
                else:
                    notes = ", ".join(os.path.basename(p.rstrip("\\/")) or p for p in s.get("sources", []))
                    if s["warnings"]:
                        notes += f"  ({s['warnings']} files had problems)"
                    values = (local_time(s["created"]), s["files"], human_size(s["bytes"]), notes)
                self.snapshots.insert("", "end", iid=s["name"], values=values)
            self.status.configure(text=f"{len(snaps)} backups on the backup machine.")

        self._start(lambda progress: engine.list_snapshots(cfg), done)

    def _restore_selected(self):
        sel = self.snapshots.selection()
        if not sel:
            messagebox.showinfo("Safekeep", "Click Refresh list, then select the backup to restore.")
            return
        dest = filedialog.askdirectory(parent=self, title="Choose an empty folder to restore into")
        if not dest:
            return
        dest = os.path.normpath(dest)
        if os.listdir(dest) and not messagebox.askyesno(
                "Safekeep", f"{dest} is not empty. Files with the same name inside it will be "
                            "replaced by the restored copies.\n\nRestore here anyway?"):
            return
        cfg, name = self._collect(), sel[0]

        def done(result):
            self._report(result, f"Restored {result.get('files', 0)} files to {dest}.")
            if result.get("files") and hasattr(os, "startfile"):
                os.startfile(dest)

        self._start(lambda progress: engine.run_restore(name, dest, cfg, progress=progress), done)

    def _verify_now(self):
        cfg = self._collect()
        self._start(lambda progress: engine.run_verify(cfg, progress=progress),
                    lambda result: self._report(result, "All stored backups are intact."))

    # -- recovery key ----------------------------------------------------
    def _key_text(self):
        key = keystore.recovery_key()
        if key is None:
            messagebox.showinfo("Safekeep", "There is no key yet. It is created by the first backup.")
        return key

    def _show_key(self):
        key = self._key_text()
        if key:
            self.v_key.set(key)

    def _copy_key(self):
        key = self._key_text()
        if key:
            self.clipboard_clear()
            self.clipboard_append(key)
            self.status.configure(text="Recovery key copied to the clipboard.")

    def _save_key(self):
        key = self._key_text()
        if not key:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Save recovery key",
                                            defaultextension=".txt",
                                            initialfile="safekeep-recovery-key.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("Safekeep recovery key\n\n" + key + "\n\nAnyone with this key and access "
                        "to the backup machine can read your backups. Keep it private.\n")
            self.status.configure(text=f"Recovery key saved to {path}")

    def _import_key(self):
        dialog = tk.Toplevel(self)
        dialog.title("Import a recovery key")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=PAD * 2)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, wraplength=460, justify="left",
                  text="Paste the recovery key of the backups you want to open. This replaces "
                       "the key on this PC, so only do it on a new PC or after saving the "
                       "current key.").pack(anchor="w")
        value = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=value, width=72, font=("Consolas", 9))
        entry.pack(pady=PAD, fill="x")
        entry.focus_set()

        def ok():
            try:
                keystore.import_recovery_key(value.get())
            except (ValueError, OSError) as e:
                messagebox.showerror("Safekeep", str(e), parent=dialog)
                return
            dialog.destroy()
            self.v_key.set("")
            self.status.configure(text="Recovery key imported.")
            self._sync_key_banner()

        row = ttk.Frame(frame)
        row.pack(anchor="e")
        ttk.Button(row, text="Import", command=ok).pack(side="left", padx=PAD)
        ttk.Button(row, text="Cancel", command=dialog.destroy).pack(side="left")

    def _key_saved_changed(self):
        self.cfg["recovery_key_saved"] = bool(self.v_key_saved.get())
        saved = config.load()
        saved["recovery_key_saved"] = self.cfg["recovery_key_saved"]
        config.save(saved)
        self._sync_key_banner()


def main():
    engine.setup_logging()
    try:  # crisp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
