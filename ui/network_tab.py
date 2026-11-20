"""Network tab: a ladder of fixes, safest first. Each one explains itself and asks before running."""
import customtkinter as ctk
from tkinter import messagebox

from core import network
from core.startup import is_admin
from .bg import BackgroundMixin

RISK_COLORS = {"Safe": "#3E7D5A"}
DEFAULT_RISK_COLOR = "#B07B1F"


class NetworkTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.buttons = {}
        self.init_background()
        self._build_ui()

    def _build_ui(self):
        admin = is_admin()
        ctk.CTkLabel(
            self, anchor="w", justify="left", wraplength=780, font=ctk.CTkFont(size=12), text_color="gray65",
            text=("Running as administrator: fixes run straight away." if admin else
                  "These fixes need administrator rights. When you press Run, you'll get an option to run it "
                  "as administrator, and Windows will ask you to approve it. This app stays as it is."),
        ).pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkLabel(self, anchor="w", justify="left", wraplength=780, text_color="gray65",
                     font=ctk.CTkFont(size=12),
                     text="Try them in order, and stop as soon as your connection is fixed.").pack(
            fill="x", padx=8, pady=(0, 6))

        for number, fix in enumerate(network.FIXES, start=1):
            card = ctk.CTkFrame(self)
            card.pack(fill="x", padx=8, pady=5)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=14, pady=(10, 0))
            ctk.CTkLabel(head, text=f"{number}. {fix.name}", font=ctk.CTkFont(weight="bold")).pack(side="left")
            ctk.CTkLabel(head, text=f" {fix.risk} ", corner_radius=8, text_color="white", font=ctk.CTkFont(size=11),
                         fg_color=RISK_COLORS.get(fix.risk, DEFAULT_RISK_COLOR)).pack(side="left", padx=10)
            button = ctk.CTkButton(head, text="Run", width=70, command=lambda f=fix: self._run(f))
            button.pack(side="right")
            self.buttons[fix.id] = button
            ctk.CTkLabel(card, text=fix.what, anchor="w", justify="left", wraplength=700,
                         text_color="gray80").pack(fill="x", padx=14, pady=(4, 0))
            ctk.CTkLabel(card, text=f"Use when: {fix.when}", anchor="w", justify="left", wraplength=700,
                         text_color="gray60", font=ctk.CTkFont(size=11)).pack(
                fill="x", padx=14, pady=(2, 10 if fix.id != "flush_dns" else 2))
            if fix.id == "flush_dns":
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=14, pady=(0, 10))
                self.dns_label = ctk.CTkLabel(row, text="Checking what's saved...", anchor="w", justify="left",
                                              wraplength=560, text_color="#8DB8E8", font=ctk.CTkFont(size=12))
                self.dns_label.pack(side="left")
                ctk.CTkButton(row, text="Check size", width=84, height=24, fg_color="gray30", hover_color="gray40",
                              font=ctk.CTkFont(size=11), command=self._refresh_dns).pack(side="right")

        self.status = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=780)
        self.status.pack(fill="x", padx=8, pady=(6, 2))
        self.dns_before = None
        self._last_entries = None
        self._refresh_dns()
        self.log = ctk.CTkTextbox(self, height=140, font=ctk.CTkFont(family="Consolas", size=11))
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log.configure(state="disabled")

    def _run(self, fix):
        if is_admin():
            if fix.risk != "Safe" and not messagebox.askyesno(
                    f"Run: {fix.name}?",
                    f"{fix.what}\n\nWarning: {fix.risk.lower()}.\n\nSave anything open first. Continue?"):
                return
            self._start(fix, elevated=False)
        else:
            self._ask_admin(fix)

    def _ask_admin(self, fix):
        """Popup offering to run this one fix as administrator (Windows shows its own approval prompt)."""
        win = ctk.CTkToplevel(self)
        win.title("Administrator needed")
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + max((root.winfo_width() - 440) // 2, 0)
        y = root.winfo_rooty() + max((root.winfo_height() - 220) // 2, 0)
        win.geometry(f"440x220+{x}+{y}")           # centred over the main window
        win.resizable(False, False)
        win.after(150, win.focus)
        ctk.CTkLabel(win, text=f"{fix.name}", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=22, pady=(20, 4))
        message = (f"This needs administrator rights. If you continue, Windows will show a prompt asking you "
                   f"to approve it. Only this one fix runs with those rights, and this app stays as it is.")
        if fix.risk != "Safe":
            message += f"\n\nWarning: {fix.risk.lower()}. Save anything open first."
        ctk.CTkLabel(win, text=message, anchor="w", justify="left", wraplength=396,
                     text_color="gray80").pack(fill="x", padx=22)
        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=22, pady=18)
        ctk.CTkButton(buttons, text="Run as administrator", width=190,
                      command=lambda: self._admin_chosen(win, fix)).pack(side="left")
        ctk.CTkButton(buttons, text="Cancel", width=90, fg_color="gray30", hover_color="gray40",
                      command=win.destroy).pack(side="left", padx=10)

    def _admin_chosen(self, win, fix):
        win.destroy()
        self._start(fix, elevated=True)

    def _refresh_dns(self, before=None):
        """Read how much is saved in the DNS cache (in the background) and show it."""
        self.dns_before = before

        def done(info, error):
            text = network.describe_dns_cache(info) if not error else "Couldn't read the size right now."
            if info and info.entries is not None:
                self._last_entries = info.entries
            if self.dns_before is not None and info and info.entries is not None:
                text = f"Was {self.dns_before:,} addresses, now {info.entries:,}.  " + text
            self.dns_label.configure(text=text)

        self.run_bg(network.dns_cache_info, done)

    def _start(self, fix, elevated):
        self._count_before = getattr(self, "_last_entries", None)
        for button in self.buttons.values():
            button.configure(state="disabled")
        if elevated:
            self.status.configure(text=f"Waiting for you to approve the Windows prompt for '{fix.name}'...",
                                  text_color="gray80")
            self.run_bg(lambda: network.run_fix_elevated(fix), self._done)
        else:
            self.status.configure(text=f"Running '{fix.name}'... please wait.", text_color="gray80")
            self.run_bg(lambda: network.run_fix(fix), self._done)

    def _done(self, result, error):
        for button in self.buttons.values():
            button.configure(state="normal")
        if error:
            self.status.configure(text=f"Something went wrong: {error}", text_color="#E0594A")
            return
        self.status.configure(text=result.message, text_color="#6FBF73" if result.ok else "#E0594A")
        self._refresh_dns(before=self._last_entries if result.ok else None)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", result.output or "(no output)")
        self.log.configure(state="disabled")
