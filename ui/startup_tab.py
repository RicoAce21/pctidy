"""Startup tab: see what launches with Windows and switch things off (reversibly)."""
import customtkinter as ctk

from core import startup
from .bg import BackgroundMixin

BADGE_COLORS = {"Broken": "#B5483E", "Optional": "#B07B1F", "Unknown": "#5B6770", "Keep": "#3E7D5A"}
BADGE_TEXT = {"Broken": "Broken", "Optional": "Usually safe to disable", "Unknown": "Unknown", "Keep": "Better to keep"}


class StartupTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.items = []
        self.init_background()
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(4, 0))
        self.refresh_btn = ctk.CTkButton(top, text="Refresh", width=90, command=self.refresh)
        self.refresh_btn.pack(side="left")
        self.summary = ctk.CTkLabel(top, text="", text_color="gray70")
        self.summary.pack(side="left", padx=12)
        self.admin_label = ctk.CTkLabel(top, text="", text_color="gray60", font=ctk.CTkFont(size=11))
        self.admin_label.pack(side="right")

        ctk.CTkLabel(
            self, anchor="w", justify="left", wraplength=780, text_color="gray65",
            font=ctk.CTkFont(size=12),
            text=("Turning something off here does NOT delete it - it is the same switch as Task Manager's "
                  "Startup tab. Flip it back on any time. Changes apply at your next restart. "
                  "The labels are a guess from the name, not a measurement."),
        ).pack(fill="x", padx=8, pady=(8, 6))

        self.list_frame = ctk.CTkScrollableFrame(self)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.status = ctk.CTkLabel(self, text="", text_color="gray70", anchor="w",
                                   wraplength=780, justify="left")
        self.status.pack(fill="x", padx=8, pady=(0, 6))

    # ---------- loading ----------
    def refresh(self):
        self.refresh_btn.configure(state="disabled")
        self.status.configure(text="Reading startup entries...")
        self.run_bg(startup.list_startup_items, self._loaded)

    def _loaded(self, items, error):
        self.refresh_btn.configure(state="normal")
        for child in self.list_frame.winfo_children():
            child.destroy()
        if error:
            self.status.configure(text=f"Could not read startup entries: {error}")
            return
        self.items = items
        if startup.winreg is None:
            ctk.CTkLabel(self.list_frame, text="The startup manager only works on Windows.",
                         text_color="gray60").pack(pady=30)
            self.status.configure(text="")
            return
        admin = startup.is_admin()
        self.admin_label.configure(
            text="Running as administrator" if admin else "Not administrator: 'all users' items can't be changed")
        enabled = sum(1 for i in items if i.enabled)
        self.summary.configure(text=f"{len(items)} startup entries - {enabled} enabled")
        if not items:
            ctk.CTkLabel(self.list_frame, text="No startup entries found.", text_color="gray60").pack(pady=30)
        for item in items:
            self._add_row(item)
        self.status.configure(text="")

    def _add_row(self, item):
        row = ctk.CTkFrame(self.list_frame)
        row.pack(fill="x", pady=4)

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=8)

        head = ctk.CTkFrame(text_col, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=item.name, font=ctk.CTkFont(weight="bold"), anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=f" {BADGE_TEXT[item.rating]} ", fg_color=BADGE_COLORS[item.rating],
                     corner_radius=8, font=ctk.CTkFont(size=11), text_color="white").pack(side="left", padx=8)
        if item.needs_admin:
            ctk.CTkLabel(head, text="needs admin", text_color="gray55",
                         font=ctk.CTkFont(size=11)).pack(side="left")

        ctk.CTkLabel(text_col, text=item.reason, text_color="gray70", anchor="w", justify="left",
                     wraplength=520, font=ctk.CTkFont(size=11)).pack(fill="x")
        command = item.command if len(item.command) <= 90 else item.command[:87] + "..."
        ctk.CTkLabel(text_col, text=f"{item.source}  |  {command}", text_color="gray50", anchor="w",
                     font=ctk.CTkFont(size=10)).pack(fill="x")

        switch = ctk.CTkSwitch(row, text="", width=46)
        switch.configure(command=lambda i=item, s=switch: self._toggled(i, s))
        if item.enabled:
            switch.select()
        switch.pack(side="right", padx=14)

    def _toggled(self, item, switch):
        wanted = bool(switch.get())
        ok, message = startup.set_enabled(item, wanted)
        if not ok:                       # put the switch back to what Windows really has
            if wanted:
                switch.deselect()
            else:
                switch.select()
        self.status.configure(text=message)
        enabled = sum(1 for i in self.items if i.enabled)
        self.summary.configure(text=f"{len(self.items)} startup entries - {enabled} enabled")
