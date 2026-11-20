"""Performance tab: three power modes (each undoable) plus a helper for background apps."""
import customtkinter as ctk
from tkinter import messagebox

from core import background_apps, monitor, power
from .admin_dialog import ask_admin
from .bg import BackgroundMixin

MODE_TEXT = {
    "cool": ("Cool and quiet",
             "Caps the CPU just below its top speed. This usually switches off turbo boost, the biggest source of "
             "heat and fan noise, for a small loss of speed in short, heavy bursts.",
             "A lower cap runs cooler but slower. Check the Monitor tab's temperature before and after to see "
             "what it does on your laptop."),
    "balanced": ("Balanced",
                 "Windows' standard plan. A good everyday default.",
                 "This is what most laptops start on."),
    "performance": ("Performance",
                    "Lets the CPU run at full speed all the time.",
                    "Faster, but hotter and louder, and the battery drains quicker. Best when plugged in."),
}


class PerformanceTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.state = power.PowerState()
        self.busy = False
        self.apps = []
        self.app_vars = []
        self.mode_buttons, self.mode_badges = {}, {}
        self.init_background()
        self._build_ui()
        self.refresh()

    # ---------- layout ----------
    def _build_ui(self):
        page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        page.pack(fill="both", expand=True)

        head = ctk.CTkFrame(page)
        head.pack(fill="x", padx=4, pady=(2, 8))
        row = ctk.CTkFrame(head, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 0))
        self.plan_label = ctk.CTkLabel(row, text="Reading your power plan...", anchor="w",
                                       font=ctk.CTkFont(size=16, weight="bold"))
        self.plan_label.pack(side="left")
        ctk.CTkButton(row, text="Refresh", width=80, height=26, fg_color="gray30", hover_color="gray40",
                      command=self.refresh).pack(side="right")
        self.cap_label = ctk.CTkLabel(head, text="", anchor="w", text_color="gray70")
        self.cap_label.pack(fill="x", padx=14, pady=(2, 12))

        for mode in ("cool", "balanced", "performance"):
            self._add_mode_card(page, mode)

        undo = ctk.CTkFrame(page)
        undo.pack(fill="x", padx=4, pady=(6, 4))
        row = ctk.CTkFrame(undo, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=12)
        self.undo_btn = ctk.CTkButton(row, text="Undo: go back to my original plan", width=250, fg_color="#B5483E",
                                      hover_color="#963B33", state="disabled", command=self._undo)
        self.undo_btn.pack(side="left")
        self.undo_label = ctk.CTkLabel(row, text="", text_color="gray65", anchor="w", wraplength=430)
        self.undo_label.pack(side="left", padx=12)

        self.status = ctk.CTkLabel(page, text="", anchor="w", justify="left", wraplength=780)
        self.status.pack(fill="x", padx=8, pady=(4, 0))
        self.log = ctk.CTkTextbox(page, height=80, font=ctk.CTkFont(family="Consolas", size=11))
        self.log.pack(fill="x", padx=4, pady=(4, 10))
        self.log.configure(state="disabled")

        apps = ctk.CTkFrame(page)
        apps.pack(fill="x", padx=4, pady=(4, 10))
        top = ctk.CTkFrame(apps, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(top, text="Background apps (gaming helper)", font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="Look again", width=90, height=26, fg_color="gray30", hover_color="gray40",
                      command=self._find_apps).pack(side="right")
        ctk.CTkLabel(
            apps, anchor="w", justify="left", wraplength=760, text_color="gray65", font=ctk.CTkFont(size=12),
            text=("Well-known apps that use your network, disk or CPU in the background and can hurt ping and heat while "
                  "you game. Nothing is closed unless you tick it. Closing works like clicking the X, and you can reopen "
                  "the app any time."),
        ).pack(fill="x", padx=14, pady=(4, 4))
        self.apps_frame = ctk.CTkFrame(apps, fg_color="transparent")
        self.apps_frame.pack(fill="x", padx=14)
        self.close_btn = ctk.CTkButton(apps, text="Close selected", width=130, state="disabled", command=self._close_apps)
        self.close_btn.pack(anchor="w", padx=14, pady=(8, 12))

    def _add_mode_card(self, parent, mode):
        title, what, tradeoff = MODE_TEXT[mode]
        card = ctk.CTkFrame(parent)
        card.pack(fill="x", padx=4, pady=5)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(weight="bold")).pack(side="left")
        badge = ctk.CTkLabel(head, text=" Active ", corner_radius=8, fg_color="#3E7D5A", text_color="white",
                             font=ctk.CTkFont(size=11))
        self.mode_badges[mode] = badge
        button = ctk.CTkButton(head, text="Apply", width=80, command=lambda m=mode: self._apply(m))
        button.pack(side="right")
        self.mode_buttons[mode] = button
        ctk.CTkLabel(card, text=what, anchor="w", justify="left", wraplength=700, text_color="gray80").pack(
            fill="x", padx=14, pady=(4, 0))
        ctk.CTkLabel(card, text=tradeoff, anchor="w", justify="left", wraplength=700, text_color="gray60",
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=14, pady=(2, 8 if mode == "cool" else 10))
        if mode == "cool":
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 12))
            ctk.CTkLabel(row, text="Top CPU speed:", text_color="gray70").pack(side="left")
            self.cap_slider = ctk.CTkSlider(row, from_=power.MIN_CAP, to=power.MAX_CAP,
                                            number_of_steps=power.MAX_CAP - power.MIN_CAP, width=260,
                                            command=self._slider_moved)
            self.cap_slider.set(power.DEFAULT_CAP)
            self.cap_slider.pack(side="left", padx=10)
            self.cap_value = ctk.CTkLabel(row, text=f"{power.DEFAULT_CAP}%", width=44, font=ctk.CTkFont(weight="bold"))
            self.cap_value.pack(side="left")
            ctk.CTkLabel(row, text="(99% = turbo off, lower = cooler and slower)", text_color="gray55",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=8)

    def _slider_moved(self, value):
        self.cap_value.configure(text=f"{int(round(value))}%")

    def _cap(self):
        return int(round(self.cap_slider.get()))

    # ---------- reading the current state ----------
    def refresh(self):
        self.run_bg(power.get_state, self._state_loaded)
        self._find_apps()

    def _state_loaded(self, state, error):
        if error or state is None:
            self.plan_label.configure(text="Couldn't read your power plan")
            return
        self.state = state
        if not state.plans:
            self.plan_label.configure(text="Power plans are only available on Windows")
            self.cap_label.configure(text="")
            self._set_buttons(False)
            return
        name = state.active.name if state.active else "unknown"
        self.plan_label.configure(text=f"Current power plan: {name}")
        if state.throttle:
            ac, dc = state.throttle
            self.cap_label.configure(text=f"Top CPU speed allowed: {ac}% plugged in, {dc}% on battery")
            if state.mode == "cool" and power.MIN_CAP <= ac <= power.MAX_CAP:
                self.cap_slider.set(ac)
                self._slider_moved(ac)
        else:
            self.cap_label.configure(text="")
        for mode, badge in self.mode_badges.items():
            if state.mode == mode:
                badge.pack(side="left", padx=10)
            else:
                badge.pack_forget()
        has_undo = bool(state.original_name) or any(p.name.startswith(power.PLAN_PREFIX) for p in state.plans)
        self.undo_btn.configure(state="normal" if has_undo and not self.busy else "disabled")
        self.undo_label.configure(text=f"Goes back to: {state.original_name}" if state.original_name else
                                  ("Removes PC Tidy's own plans." if has_undo else "Nothing to undo yet."))
        self._set_buttons(not self.busy)

    def _set_buttons(self, enabled):
        for button in self.mode_buttons.values():
            button.configure(state="normal" if enabled else "disabled")

    # ---------- applying a mode / undo ----------
    def _begin(self, text):
        self.busy = True
        self._set_buttons(False)
        self.undo_btn.configure(state="disabled")
        self.status.configure(text=text, text_color="gray80")

    def _apply(self, mode, elevated=False):
        self._begin(f"Switching to {power.MODES[mode]}..." if not elevated else
                    "Waiting for you to approve the Windows prompt...")
        cap = self._cap()
        self.run_bg(lambda: power.apply_mode(mode, cap, elevated=elevated),
                    lambda result, error: self._done(result, error, lambda: self._apply(mode, True), f"{power.MODES[mode]} mode"))

    def _undo(self, elevated=False):
        self._begin("Restoring your original plan..." if not elevated else "Waiting for you to approve the Windows prompt...")
        self.run_bg(lambda: power.undo(elevated=elevated),
                    lambda result, error: self._done(result, error, lambda: self._undo(True), "Undo"))

    def _done(self, result, error, retry_as_admin, what):
        self.busy = False
        if error:
            self.status.configure(text=f"Something went wrong: {error}", text_color="#E0594A")
            self.refresh()
            return
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", result.output or "")
        self.log.configure(state="disabled")
        if result.needs_admin:
            self.status.configure(text=result.message, text_color="#E0A94A")
            ask_admin(self, what, "Changing this needs administrator rights. If you continue, Windows will show a prompt "
                                  "asking you to approve it. Only this change runs with those rights, and this app stays "
                                  "as it is.", retry_as_admin)
            self._set_buttons(True)
            self.refresh()
            return
        self.status.configure(text=result.message, text_color="#6FBF73" if result.ok else "#E0594A")
        self.refresh()

    # ---------- background apps ----------
    def _find_apps(self):
        def work():
            return background_apps.find_background_apps(monitor.top_processes(limit=2000, sort_by="memory"))
        self.run_bg(work, self._apps_found)

    def _apps_found(self, apps, error):
        for child in self.apps_frame.winfo_children():
            child.destroy()
        self.apps, self.app_vars = ([] if error else apps), []
        if error or not apps:
            ctk.CTkLabel(self.apps_frame, text="None of the usual background apps are running right now.",
                         text_color="gray60").pack(anchor="w", pady=6)
            self.close_btn.configure(state="disabled")
            return
        for app in apps:
            var = ctk.BooleanVar(value=False)
            self.app_vars.append(var)
            row = ctk.CTkFrame(self.apps_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkCheckBox(row, text="", variable=var, width=24, command=self._apps_changed).pack(side="left")
            extra = f"  x{app['count']}" if app["count"] > 1 else ""
            ctk.CTkLabel(row, text=f"{app['label']}{extra}", width=190, anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(side="left")
            ctk.CTkLabel(row, text=f"{app['mem_mb']:.0f} MB", width=70, anchor="e", text_color="gray75").pack(side="left")
            ctk.CTkLabel(row, text=app["why"], anchor="w", text_color="gray60", wraplength=430,
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=12)
        self._apps_changed()

    def _apps_changed(self):
        self.close_btn.configure(state="normal" if any(v.get() for v in self.app_vars) else "disabled")

    def _close_apps(self):
        chosen = [a for a, v in zip(self.apps, self.app_vars) if v.get()]
        if not chosen:
            return
        names = ", ".join(a["label"] for a in chosen)
        if not messagebox.askyesno("Close these apps?", f"This closes: {names}.\n\nAnything unsaved in them will be lost. "
                                                         "You can reopen them any time.\n\nContinue?"):
            return
        pids = [pid for a in chosen for pid in a["pids"]]
        self.status.configure(text="Closing...", text_color="gray80")
        self.run_bg(lambda: monitor.end_processes(pids), self._apps_closed)

    def _apps_closed(self, result, error):
        ok, message = (False, str(error)) if error else result
        self.status.configure(text=message, text_color="#6FBF73" if ok else "#E0594A")
        self._find_apps()
