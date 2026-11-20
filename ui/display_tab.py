"""Display tab: is each screen running at the refresh rate it's capable of?"""
import customtkinter as ctk

from core import display
from .bg import BackgroundMixin

STATUS_COLORS = {"ok": "#3E7D5A", "low": "#B07B1F", "unknown": "#5B6770"}
STATUS_TEXT = {"ok": "All good", "low": "Could be smoother", "unknown": "Can't tell"}


class DisplayTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.init_background()
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(4, 0))
        self.refresh_btn = ctk.CTkButton(top, text="Check again", width=110, command=self.refresh)
        self.refresh_btn.pack(side="left")
        ctk.CTkLabel(
            self, anchor="w", justify="left", wraplength=780, text_color="gray65", font=ctk.CTkFont(size=12),
            text=("Many gaming screens ship set to 60 Hz even when they can do 144 Hz or more. "
                  "This only LOOKS at your settings and never changes them. On a laptop, Windows may also "
                  "drop the refresh rate to save battery, so check again while plugged in."),
        ).pack(fill="x", padx=8, pady=(8, 6))
        self.list_frame = ctk.CTkScrollableFrame(self)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.status = ctk.CTkLabel(self, text="", text_color="gray70", anchor="w", wraplength=780)
        self.status.pack(fill="x", padx=8, pady=(0, 6))

    def refresh(self):
        self.refresh_btn.configure(state="disabled")
        self.status.configure(text="Checking your screens...")
        self.run_bg(display.get_displays, self._loaded)

    def _loaded(self, screens, error):
        self.refresh_btn.configure(state="normal")
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.status.configure(text="")
        if error:
            self.status.configure(text=f"Couldn't read display info: {error}")
            return
        if not screens:
            ctk.CTkLabel(self.list_frame, text="No screen information available (this check only works on Windows).",
                         text_color="gray60").pack(pady=30)
            return
        for info in screens:
            self._add_card(info)

    def _add_card(self, info):
        card = ctk.CTkFrame(self.list_frame)
        card.pack(fill="x", pady=6, padx=2)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 0))
        title = info.name + ("  (main screen)" if info.primary else "")
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(weight="bold"), anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=f" {STATUS_TEXT[info.status]} ", fg_color=STATUS_COLORS[info.status],
                     corner_radius=8, font=ctk.CTkFont(size=11), text_color="white").pack(side="left", padx=10)

        numbers = ctk.CTkFrame(card, fg_color="transparent")
        numbers.pack(fill="x", padx=14, pady=(8, 0))
        for label, value in (("Now", f"{info.current_hz} Hz" if info.current_hz > 1 else "unknown"),
                             ("Best at this resolution", f"{info.best_hz} Hz" if info.best_hz > 1 else "unknown"),
                             ("Resolution", f"{info.width} x {info.height}")):
            box = ctk.CTkFrame(numbers, fg_color="transparent")
            box.pack(side="left", padx=(0, 28))
            ctk.CTkLabel(box, text=label, text_color="gray60", font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w")
            ctk.CTkLabel(box, text=value, font=ctk.CTkFont(size=20, weight="bold"), anchor="w").pack(anchor="w")

        ctk.CTkLabel(card, text=info.message, anchor="w", justify="left", wraplength=720,
                     text_color="gray80").pack(fill="x", padx=14, pady=(10, 4))
        if info.status == "low":
            ctk.CTkButton(card, text="Open Windows display settings", width=210,
                          command=self._open_settings).pack(anchor="w", padx=14, pady=(2, 4))
            ctk.CTkLabel(card, anchor="w", justify="left", wraplength=720, text_color="gray60",
                         font=ctk.CTkFont(size=11),
                         text="There, choose 'Choose a refresh rate' and pick the highest one.").pack(
                fill="x", padx=14, pady=(0, 6))
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack()

    def _open_settings(self):
        ok = display.open_display_settings()
        self.status.configure(text="" if ok else
                              "Couldn't open it. Go to Settings > System > Display > Advanced display.")
