"""
PC Tidy - main window with tabs:
  Cleanup  - find and remove junk files (preview first, you confirm)
  Startup  - switch off programs that launch with Windows (reversible)
  Monitor  - live CPU / RAM / disk / temperature and the busiest programs
  Display  - checks your screen is running at its best refresh rate
  Storage  - shows where your disk space went, folder by folder (read-only)
  Network  - a ladder of network fixes, safest first (needs administrator)
  Ping     - tests your connection to servers worldwide and points out what could be hurting it
  Performance - cool / balanced / performance power modes with Undo, plus a background-app helper
  Log      - record a session, change one thing, record again, and compare (temperature, speed, load)
"""
import customtkinter as ctk

from ui.cleanup_tab import CleanupTab
from ui.display_tab import DisplayTab
from ui.log_tab import LogTab
from ui.monitor_tab import MonitorTab
from ui.network_tab import NetworkTab
from ui.performance_tab import PerformanceTab
from ui.ping_tab import PingTab
from ui.startup_tab import StartupTab
from ui.storage_tab import StorageTab

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PC Tidy")
        self.geometry("900x760")
        self.minsize(820, 640)

        ctk.CTkLabel(self, text="PC Tidy", font=ctk.CTkFont(size=26, weight="bold")).pack(
            anchor="w", padx=24, pady=(18, 0))
        ctk.CTkLabel(self, text="Clean up, trim startup, and keep an eye on performance.",
                     text_color="gray70").pack(anchor="w", padx=24, pady=(0, 6))

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        for name, tab_class in (("Cleanup", CleanupTab), ("Startup", StartupTab), ("Monitor", MonitorTab),
                                ("Display", DisplayTab), ("Storage", StorageTab),
                                ("Network", NetworkTab), ("Ping", PingTab),
                                ("Performance", PerformanceTab), ("Log", LogTab)):
            holder = self.tabs.add(name)
            tab_class(holder).pack(fill="both", expand=True)


if __name__ == "__main__":
    App().mainloop()
