"""Ping tab: how fast is your connection to servers around the world, and what could be hurting it?"""
import queue
import threading

import customtkinter as ctk

from core import ping
from .bg import BackgroundMixin

RATING_COLORS = {"Great": "#3E7D5A", "OK": "#B07B1F", "Poor": "#B5483E", "Unreachable": "#6B3A36",
                 "Not tested": "#4A5560", "Testing...": "#4A5560"}
LEVEL_COLORS = {"good": "#6FBF73", "info": "#8DB8E8", "warn": "#E0A94A", "bad": "#E0594A"}
BAR_MAX_MS = 300.0


class PingTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.rows = {}
        self.updates = queue.Queue()
        self.cancel = None
        self.testing = False
        self.init_background()
        self._build_ui()

    # ---------- layout ----------
    def _build_ui(self):
        page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        page.pack(fill="both", expand=True)

        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(2, 0))
        self.run_btn = ctk.CTkButton(top, text="Run test", width=110, command=self.start)
        self.run_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(top, text="Cancel", width=80, state="disabled", fg_color="gray30",
                                        hover_color="gray40", command=self._cancel)
        self.cancel_btn.pack(side="left", padx=8)
        self.status = ctk.CTkLabel(top, text="About 15 seconds. Nothing is changed on your PC.",
                                   text_color="gray65", anchor="w")
        self.status.pack(side="left", padx=8)

        self.verdict_card = ctk.CTkFrame(page)
        self.verdict_card.pack(fill="x", padx=4, pady=(10, 4))
        self.verdict_title = ctk.CTkLabel(self.verdict_card, text="Press Run test to check your connection.",
                                          anchor="w", font=ctk.CTkFont(size=16, weight="bold"))
        self.verdict_title.pack(fill="x", padx=14, pady=(12, 0))
        self.verdict_detail = ctk.CTkLabel(self.verdict_card, text="", anchor="w", justify="left",
                                           wraplength=760, text_color="gray80")
        self.verdict_detail.pack(fill="x", padx=14, pady=(2, 12))
        self.findings_frame = ctk.CTkFrame(page, fg_color="transparent")
        self.findings_frame.pack(fill="x", padx=4)

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 2))
        for text, width, anchor in (("Server", 250, "w"), ("Speed", 80, "e"), ("Steadiness", 90, "e"),
                                    ("Lost", 60, "e"), ("", 170, "w")):
            ctk.CTkLabel(head, text=text, width=width, anchor=anchor, text_color="gray55",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=2)
        for target in ping.TARGETS:
            self._add_row(page, target)

        ctk.CTkLabel(
            page, anchor="w", justify="left", wraplength=780, text_color="gray55", font=ctk.CTkFont(size=11),
            text=("Speed is how long a connection takes to open, in milliseconds (lower is better, under about 50 is "
                  "great for games). Steadiness is how much that bounces around. This measures your connection to cloud "
                  "data-centre regions, not to a specific game's servers (games don't publish those addresses), so "
                  "treat it as a good guide, not a guarantee."),
        ).pack(fill="x", padx=14, pady=(10, 6))

        tips = ctk.CTkFrame(page)
        tips.pack(fill="x", padx=4, pady=(4, 10))
        ctk.CTkLabel(tips, text="How to actually lower your ping", anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=14, pady=(10, 4))
        for i, tip in enumerate(ping.TIPS, start=1):
            ctk.CTkLabel(tips, text=f"{i}. {tip}", anchor="w", justify="left", wraplength=740,
                         text_color="gray80", font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=1)
        ctk.CTkLabel(tips, text="What doesn't help: " + ping.MYTHS, anchor="w", justify="left", wraplength=740,
                     text_color="gray60", font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=(8, 12))

    def _add_row(self, parent, target):
        row = ctk.CTkFrame(parent)
        row.pack(fill="x", padx=4, pady=2)
        tag = "" if target.kind == "reference" else "  "
        name = ctk.CTkLabel(row, text=tag + target.name, width=250, anchor="w",
                            font=ctk.CTkFont(weight="bold" if target.kind == "reference" else "normal"))
        name.pack(side="left", padx=(10, 2), pady=8)
        speed = ctk.CTkLabel(row, text="-", width=80, anchor="e")
        speed.pack(side="left", padx=2)
        jitter = ctk.CTkLabel(row, text="-", width=90, anchor="e", text_color="gray70")
        jitter.pack(side="left", padx=2)
        lost = ctk.CTkLabel(row, text="-", width=60, anchor="e", text_color="gray70")
        lost.pack(side="left", padx=2)
        badge = ctk.CTkLabel(row, text=" Not tested ", corner_radius=8, text_color="white", font=ctk.CTkFont(size=11),
                             fg_color=RATING_COLORS["Not tested"], width=84)
        badge.pack(side="left", padx=(10, 6))
        bar = ctk.CTkProgressBar(row, width=70, height=7)
        bar.set(0)
        bar.pack(side="left", padx=(0, 8))
        self.rows[target.id] = (speed, jitter, lost, badge, bar)

    # ---------- running ----------
    def start(self):
        if self.testing:
            return
        self.testing = True
        self.cancel = threading.Event()
        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status.configure(text="Testing... connecting to each server about 10 times.")
        self._clear_findings()
        self.verdict_title.configure(text="Testing...", text_color="gray80")
        self.verdict_detail.configure(text="")
        for speed, jitter, lost, badge, bar in self.rows.values():
            speed.configure(text="...")
            jitter.configure(text="")
            lost.configure(text="")
            badge.configure(text=" Testing... ", fg_color=RATING_COLORS["Testing..."])
            bar.set(0)

        def work():
            env = ping.get_environment()
            results = ping.run_all(cancel=self.cancel, progress=self.updates.put)
            return results, env

        self.run_bg(work, self._finished)
        self.after(150, self._poll)

    def _cancel(self):
        if self.cancel:
            self.cancel.set()
            self.status.configure(text="Cancelling...")

    def _poll(self):
        try:
            while True:
                self._show_result(self.updates.get_nowait())
        except queue.Empty:
            pass
        if self.testing:
            self.after(150, self._poll)

    def _show_result(self, result):
        speed, jitter, lost, badge, bar = self.rows[result.target.id]
        stats, rating = result.stats, ping.rate(result.stats)
        if stats.reachable:
            speed.configure(text=f"{stats.avg:.0f} ms")
            jitter.configure(text=f"±{stats.jitter:.0f} ms")
            lost.configure(text=f"{stats.loss_pct:.0f}%")
            bar.set(min(stats.avg / BAR_MAX_MS, 1.0))
        else:
            speed.configure(text="-")
            jitter.configure(text="")
            lost.configure(text="all" if stats.sent else "")
        badge.configure(text=f" {rating} ", fg_color=RATING_COLORS.get(rating, "#4A5560"))

    def _finished(self, data, error):
        self.testing = False
        self.run_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        try:                                   # show any results still waiting in the queue
            while True:
                self._show_result(self.updates.get_nowait())
        except queue.Empty:
            pass
        if error:
            self.status.configure(text=f"The test failed: {error}")
            self.verdict_title.configure(text="Couldn't run the test", text_color="#E0594A")
            return
        results, env = data
        if self.cancel is not None and self.cancel.is_set():
            self.status.configure(text="Test cancelled.")
            self.verdict_title.configure(text="Test cancelled.", text_color="gray80")
            return
        self.status.configure(text="Done. Run it again after changing something to see the difference.")
        diagnosis = ping.diagnose(results, env)
        self.verdict_title.configure(text=diagnosis.verdict.title, text_color=LEVEL_COLORS[diagnosis.verdict.level])
        self.verdict_detail.configure(text=diagnosis.verdict.detail)
        for finding in diagnosis.findings:
            card = ctk.CTkFrame(self.findings_frame)
            card.pack(fill="x", pady=3)
            ctk.CTkLabel(card, text=finding.title, anchor="w", text_color=LEVEL_COLORS[finding.level],
                         font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=14, pady=(8, 0))
            ctk.CTkLabel(card, text=finding.detail, anchor="w", justify="left", wraplength=760,
                         text_color="gray80", font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=(0, 8))

    def _clear_findings(self):
        for child in self.findings_frame.winfo_children():
            child.destroy()
