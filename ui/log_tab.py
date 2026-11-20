"""Log tab: record a session, change one thing, record again, then compare the two."""
import time
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
from tkinter import messagebox

from core import power, session_log
from .bg import BackgroundMixin

BEFORE_COLOR, AFTER_COLOR = "#4C9BE8", "#E8924C"
VERDICT_COLORS = {"better": "#6FBF73", "worse": "#E0594A", "same": "gray70", "info": "gray70"}
METRICS = {"Temperature": ("temp", "°C"), "CPU speed": ("mhz", " MHz"), "CPU load": ("cpu", "%")}


class OverlayGraph(tk.Canvas):
    """Two recordings drawn on top of each other, so you can see where they differ."""

    def __init__(self, master, height=170):
        super().__init__(master, height=height, bg="#1c1c1c", highlightthickness=0)
        self.lines = []
        self.unit = ""
        self.bind("<Configure>", lambda _e: self.redraw())

    def plot(self, series, unit):
        self.lines, self.unit = series, unit
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        values = [v for _c, pts in self.lines for _x, v in pts]
        if w < 60 or h < 40 or not values:
            if w >= 60:
                self.create_text(w / 2, h / 2, text="No data for this metric in these recordings.", fill="#777")
            return
        lo, hi = min(values), max(values)
        if hi - lo < 1:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        left, right, top, bottom = 52, 10, 8, 18
        longest = max((pts[-1][0] for _c, pts in self.lines if pts), default=1) or 1
        for frac in (0, 0.5, 1):
            y = bottom_y = h - bottom - (h - top - bottom) * frac
            self.create_line(left, y, w - right, y, fill="#2c2c2c")
            self.create_text(left - 6, y, text=f"{lo + (hi - lo) * frac:.0f}{self.unit.strip()}", fill="#8a8a8a",
                             anchor="e", font=("Segoe UI", 8))
        self.create_text(left, h - 6, text="0 min", fill="#8a8a8a", anchor="w", font=("Segoe UI", 8))
        self.create_text(w - right, h - 6, text=f"{longest / 60:.1f} min", fill="#8a8a8a", anchor="e", font=("Segoe UI", 8))
        for color, pts in self.lines:
            if len(pts) < 2:
                continue
            coords = []
            for x, v in pts:
                coords += [left + (w - left - right) * x / longest, h - bottom - (h - top - bottom) * (v - lo) / (hi - lo)]
            self.create_line(*coords, fill=color, width=2, smooth=True)


def _when(ts):
    return datetime.fromtimestamp(ts).strftime("%d %b, %H:%M")


class LogTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.recorder = None
        self.sessions = []
        self.checks = {}                 # session id -> tk variable
        self.pair = None                 # (before, after) sessions currently compared
        self.init_background()
        self._build_ui()
        self.refresh()

    # ---------- layout ----------
    def _build_ui(self):
        page = self.page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        page.pack(fill="both", expand=True)

        ctk.CTkLabel(
            page, anchor="w", justify="left", wraplength=780, text_color="gray65", font=ctk.CTkFont(size=12),
            text=("Record a session, change ONE thing (a power mode, closing apps, cleaning the fans...), record again, then "
                  "compare. For a fair result, do the same activity both times, for example the same game for 10 minutes. "
                  "This only reads numbers and changes nothing on your PC."),
        ).pack(fill="x", padx=6, pady=(2, 8))

        rec = ctk.CTkFrame(page)
        rec.pack(fill="x", padx=4, pady=(0, 8))
        row = ctk.CTkFrame(rec, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        self.name_entry = ctk.CTkEntry(row, width=300, placeholder_text="Name it, e.g. Before - Balanced")
        self.name_entry.pack(side="left")
        self.start_btn = ctk.CTkButton(row, text="Start recording", width=140, command=self._start)
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = ctk.CTkButton(row, text="Stop", width=80, state="disabled", fg_color="#B5483E",
                                      hover_color="#963B33", command=self._stop)
        self.stop_btn.pack(side="left")
        self.live = ctk.CTkLabel(rec, text="Not recording.", anchor="w", text_color="gray70")
        self.live.pack(fill="x", padx=14, pady=(0, 12))

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(head, text="Your recordings", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.compare_btn = ctk.CTkButton(head, text="Compare the 2 ticked", width=170, state="disabled",
                                         command=self._compare)
        self.compare_btn.pack(side="right")
        self.list_frame = ctk.CTkFrame(page, fg_color="transparent")
        self.list_frame.pack(fill="x", padx=4)

        self.result = ctk.CTkFrame(page, fg_color="transparent")
        self.result.pack(fill="x", padx=4, pady=(8, 10))

    # ---------- the list of recordings ----------
    def refresh(self):
        self.run_bg(session_log.list_sessions, self._listed)

    def _listed(self, sessions, error):
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.sessions = [] if error else sessions
        self.checks = {}
        if not self.sessions:
            ctk.CTkLabel(self.list_frame, text="No recordings yet. Record one above.", text_color="gray60").pack(
                anchor="w", padx=10, pady=10)
        for s in self.sessions:
            summary = session_log.summarize(s)
            var = ctk.BooleanVar(value=False)
            self.checks[s.id] = var
            row = ctk.CTkFrame(self.list_frame)
            row.pack(fill="x", pady=2)
            ctk.CTkCheckBox(row, text="", variable=var, width=24, command=self._ticks_changed).pack(side="left", padx=(10, 2), pady=10)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True)
            title = s.name + ("   (unfinished)" if s.partial else "")
            ctk.CTkLabel(text, text=title, anchor="w", font=ctk.CTkFont(weight="bold")).pack(fill="x")
            temp = f"avg {summary.temp_avg:.0f}°C, peak {summary.temp_peak:.0f}°C" if summary.temp_avg is not None else "no temperature"
            plan = f"  |  {s.plan}" if s.plan else ""
            ctk.CTkLabel(text, anchor="w", text_color="gray60", font=ctk.CTkFont(size=11),
                         text=f"{_when(s.started)}  |  {summary.duration / 60:.1f} min  |  {temp}  |  CPU {summary.cpu_avg:.0f}%{plan}").pack(fill="x")
            ctk.CTkButton(row, text="Delete", width=60, height=24, fg_color="gray30", hover_color="#963B33",
                          command=lambda sid=s.id, name=s.name: self._delete(sid, name)).pack(side="right", padx=10)
        self._ticks_changed()

    def _ticks_changed(self):
        ticked = [sid for sid, v in self.checks.items() if v.get()]
        self.compare_btn.configure(state="normal" if len(ticked) == 2 else "disabled")

    def _delete(self, session_id, name):
        if messagebox.askyesno("Delete recording?", f"Delete '{name}'? This can't be undone."):
            session_log.delete(session_id)
            self.refresh()

    # ---------- recording ----------
    def _start(self):
        if self.recorder and self.recorder.running:
            return
        self.start_btn.configure(state="disabled")
        self.live.configure(text="Starting...")
        name = self.name_entry.get().strip() or f"Recording {datetime.now().strftime('%H:%M')}"

        def work():
            try:
                state = power.get_state()
                return state.active.name if state.active else ""
            except Exception:
                return ""

        self.run_bg(work, lambda plan, error: self._begin(name, plan or ""))

    def _begin(self, name, plan):
        self.recorder = session_log.Recorder(name, plan=plan)
        self.recorder.start()
        self.stop_btn.configure(state="normal")
        self.name_entry.configure(state="disabled")
        self._tick_live()

    def _tick_live(self):
        rec = self.recorder
        if rec is None or not rec.running:
            return
        n = len(rec.session.samples)
        latest = rec.latest or {}
        temp = f"{latest['temp']:.0f}°C" if latest.get("temp") is not None else "no temperature"
        self.live.configure(text=f"Recording '{rec.session.name}': {n * rec.session.interval / 60:.1f} min  |  "
                                 f"CPU {latest.get('cpu', 0):.0f}%  |  {temp}  |  plan: {rec.session.plan or 'unknown'}",
                            text_color="#6FBF73")
        self.after(1000, self._tick_live)

    def _stop(self):
        if not self.recorder:
            return
        self.stop_btn.configure(state="disabled")
        self.live.configure(text="Saving...", text_color="gray80")
        rec = self.recorder
        self.run_bg(rec.stop, self._stopped)

    def _stopped(self, session, error):
        self.name_entry.configure(state="normal")
        self.name_entry.delete(0, "end")
        self.start_btn.configure(state="normal")
        if error:
            self.live.configure(text=f"Couldn't save the recording: {error}", text_color="#E0594A")
        else:
            self.live.configure(text=f"Saved '{session.name}' ({session.duration / 60:.1f} min). Tick two recordings to compare.",
                                text_color="gray80")
        self.recorder = None
        self.refresh()

    # ---------- comparing ----------
    def _compare(self):
        ticked = [s for s in self.sessions if self.checks.get(s.id) and self.checks[s.id].get()]
        if len(ticked) != 2:
            return
        before, after = sorted(ticked, key=lambda s: s.started)      # the older one is "before"
        self.pair = (before, after)
        self._show_comparison(before, after, "Temperature")

    def _show_comparison(self, before, after, metric):
        for child in self.result.winfo_children():
            child.destroy()
        sb, sa = session_log.summarize(before), session_log.summarize(after)
        cmp = session_log.compare(sb, sa)

        box = ctk.CTkFrame(self.result)
        box.pack(fill="x")
        ctk.CTkLabel(box, text="Comparison", font=ctk.CTkFont(size=16, weight="bold"), anchor="w").pack(
            fill="x", padx=14, pady=(12, 0))
        names = ctk.CTkFrame(box, fg_color="transparent")
        names.pack(fill="x", padx=14, pady=(2, 6))
        ctk.CTkLabel(names, text=f"Before: {before.name}", text_color=BEFORE_COLOR).pack(side="left")
        ctk.CTkLabel(names, text=f"After: {after.name}", text_color=AFTER_COLOR).pack(side="left", padx=20)

        for sentence in cmp.sentences:
            ctk.CTkLabel(box, text=sentence, anchor="w", justify="left", wraplength=760,
                         font=ctk.CTkFont(size=14)).pack(fill="x", padx=14, pady=2)
        for warning in cmp.warnings:
            ctk.CTkLabel(box, text="Note: " + warning, anchor="w", justify="left", wraplength=760,
                         text_color="#E0A94A", font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=2)

        table = ctk.CTkFrame(box, fg_color="transparent")
        table.pack(fill="x", padx=14, pady=(10, 4))
        for col, (text, width, anchor) in enumerate((("", 190, "w"), ("Before", 120, "e"), ("After", 120, "e"), ("Change", 110, "e"))):
            ctk.CTkLabel(table, text=text, width=width, anchor=anchor, text_color="gray55",
                         font=ctk.CTkFont(size=11)).grid(row=0, column=col, padx=4)
        for r, row in enumerate(cmp.rows, start=1):
            ctk.CTkLabel(table, text=row.metric, width=190, anchor="w").grid(row=r, column=0, padx=4, pady=1)
            ctk.CTkLabel(table, text=row.before, width=120, anchor="e", text_color="gray80").grid(row=r, column=1, padx=4)
            ctk.CTkLabel(table, text=row.after, width=120, anchor="e", text_color="gray80").grid(row=r, column=2, padx=4)
            ctk.CTkLabel(table, text=row.change, width=110, anchor="e", font=ctk.CTkFont(weight="bold"),
                         text_color=VERDICT_COLORS[row.verdict]).grid(row=r, column=3, padx=4)

        apps = ctk.CTkFrame(box, fg_color="transparent")
        apps.pack(fill="x", padx=14, pady=(8, 0))
        for label, summary, color in (("Busiest apps before", sb, BEFORE_COLOR), ("Busiest apps after", sa, AFTER_COLOR)):
            names_text = ", ".join(f"{n} ({v:.0f}%)" for n, v in summary.top_apps[:3]) or "none recorded"
            ctk.CTkLabel(apps, text=f"{label}: {names_text}", anchor="w", justify="left", wraplength=760, text_color=color,
                         font=ctk.CTkFont(size=11)).pack(fill="x")

        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 2))
        ctk.CTkLabel(top, text="Graph", font=ctk.CTkFont(weight="bold")).pack(side="left")
        switch = ctk.CTkSegmentedButton(top, values=list(METRICS), command=lambda m: self._show_comparison(before, after, m))
        switch.set(metric)
        switch.pack(side="right")
        graph = OverlayGraph(box)
        graph.pack(fill="x", padx=14, pady=(0, 14))
        key, unit = METRICS[metric]

        def series(session):
            pts = [(i * session.interval, x[key]) for i, x in enumerate(session.samples) if x.get(key) is not None]
            return pts

        graph.plot([(BEFORE_COLOR, series(before)), (AFTER_COLOR, series(after))], unit)
