"""Storage tab: where did my disk space go? Read-only - it shows, you decide."""
import os
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core import storage
from core.scanner import format_size
from .bg import BackgroundMixin

MAX_ROWS = 40


class StorageTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.result = None
        self.stack = []                 # folders from the top down to the one being viewed
        self.cancel = None
        self.scanning = False
        self.progress = {"files": 0, "bytes": 0, "current": ""}
        self.view = "Folders"
        self.init_background()
        self._build_ui()

    # ---------- layout ----------
    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(4, 0))
        self.user_btn = ctk.CTkButton(top, text="My user folder", width=120,
                                      command=lambda: self.scan_path(str(Path.home())))
        self.user_btn.pack(side="left")
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        self.drive_btn = ctk.CTkButton(top, text="Whole drive (slow)", width=140, fg_color="gray30",
                                       hover_color="gray40", command=lambda: self.scan_path(drive))
        self.drive_btn.pack(side="left", padx=8)
        self.pick_btn = ctk.CTkButton(top, text="Choose folder...", width=120, fg_color="gray30",
                                      hover_color="gray40", command=self._choose)
        self.pick_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(top, text="Cancel", width=80, state="disabled", fg_color="#B5483E",
                                        hover_color="#963B33", command=self._cancel)
        self.cancel_btn.pack(side="right")

        ctk.CTkLabel(
            self, anchor="w", justify="left", wraplength=780, text_color="gray65", font=ctk.CTkFont(size=12),
            text=("Shows what is using your space, folder by folder. This tab only LOOKS - it can't delete "
                  "anything. Use 'Show' to open a folder in Explorer. For safe junk, use the Cleanup tab."),
        ).pack(fill="x", padx=8, pady=(8, 4))

        self.status = ctk.CTkLabel(self, text="Pick where to look. 'My user folder' takes about a minute.",
                                   text_color="gray70", anchor="w", wraplength=780, justify="left")
        self.status.pack(fill="x", padx=8)

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=8, pady=(8, 2))
        self.back_btn = ctk.CTkButton(nav, text="< Back", width=70, state="disabled", command=self._back)
        self.back_btn.pack(side="left")
        self.path_label = ctk.CTkLabel(nav, text="", anchor="w", font=ctk.CTkFont(weight="bold"))
        self.path_label.pack(side="left", padx=10, fill="x", expand=True)
        self.switch = ctk.CTkSegmentedButton(nav, values=["Folders", "Biggest files"], command=self._switch_view)
        self.switch.set("Folders")
        self.switch.pack(side="right")

        self.folder_frame = ctk.CTkScrollableFrame(self)
        self.file_frame = ctk.CTkScrollableFrame(self)
        self.folder_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    # ---------- scanning ----------
    def _choose(self):
        folder = filedialog.askdirectory(title="Choose a folder to analyze")
        if folder:
            self.scan_path(folder)

    def scan_path(self, path):
        if self.scanning:
            return
        self.scanning = True
        self.cancel = threading.Event()
        self.progress.update(files=0, bytes=0, current=path)
        for b in (self.user_btn, self.drive_btn, self.pick_btn):
            b.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status.configure(text=f"Scanning {path} ...")

        def on_progress(files, size, current):
            self.progress.update(files=files, bytes=size, current=current)

        self.run_bg(lambda: storage.scan(path, self.cancel, on_progress), self._scan_done)
        self.after(300, self._tick_progress)

    def _tick_progress(self):
        if not self.scanning:
            return
        p = self.progress
        self.status.configure(text=f"Scanning... {p['files']:,} files, {format_size(p['bytes'])} so far\n{p['current']}")
        self.after(300, self._tick_progress)

    def _cancel(self):
        if self.cancel:
            self.cancel.set()
            self.status.configure(text="Cancelling...")

    def _scan_done(self, result, error):
        self.scanning = False
        for b in (self.user_btn, self.drive_btn, self.pick_btn):
            b.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if error:
            self.status.configure(text=f"Scan failed: {error}")
            return
        if result.cancelled:
            self.status.configure(text="Scan cancelled. Nothing was changed.")
            return
        self.result = result
        self.stack = [result.root]
        notes = f"Done in {result.seconds:.0f}s. Total {format_size(result.root.size)}."
        if result.skipped:
            notes += f" {result.skipped} folders were off-limits to this app and aren't counted."
        if result.offline_skipped:
            notes += f" {result.offline_skipped} online-only cloud files ignored (they use almost no disk)."
        self.status.configure(text=notes)
        self._render()

    # ---------- navigating ----------
    def _switch_view(self, value):
        self.view = value
        self._render()

    def _open(self, node):
        self.stack.append(node)
        self._render()

    def _back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self._render()

    def _render(self):
        if not self.stack:
            return
        node = self.stack[-1]
        self.path_label.configure(text=f"{node.path}   -   {format_size(node.size)}")
        self.back_btn.configure(state="normal" if len(self.stack) > 1 else "disabled")
        if self.view == "Folders":
            self.file_frame.pack_forget()
            self.folder_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
            self._render_folders(node)
        else:
            self.folder_frame.pack_forget()
            self.file_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
            self._render_files(node)

    def _clear(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _render_folders(self, node):
        frame = self.folder_frame
        self._clear(frame)
        total = max(node.size, 1)
        rows = [(c.name, c.size, c) for c in node.children[:MAX_ROWS]]
        if node.files_size:
            rows.append((f"(files directly in this folder: {node.file_count - sum(c.file_count for c in node.children):,})",
                         node.files_size, None))
        if node.hidden_count:
            rows.append((f"({node.hidden_count} smaller folders)", node.hidden_size, None))
        rows.sort(key=lambda r: r[1], reverse=True)
        if not rows:
            ctk.CTkLabel(frame, text="This folder is empty.", text_color="gray60").pack(pady=24)
        for name, size, child in rows:
            row = ctk.CTkFrame(frame)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=name, anchor="w", width=290,
                         font=ctk.CTkFont(weight="bold" if child else "normal"),
                         text_color="white" if child else "gray65").pack(side="left", padx=(10, 4), pady=7)
            ctk.CTkLabel(row, text=format_size(size), width=76, anchor="e").pack(side="left")
            bar = ctk.CTkProgressBar(row, width=130, height=8)
            bar.set(min(size / total, 1.0))
            bar.pack(side="left", padx=10)
            ctk.CTkLabel(row, text=f"{100 * size / total:.0f}%", width=36, anchor="e",
                         text_color="gray60").pack(side="left")
            if child is not None:
                ctk.CTkButton(row, text="Show", width=52, height=24, fg_color="gray30", hover_color="gray40",
                              command=lambda p=child.path: self._reveal(p, False)).pack(side="right", padx=8)
                if child.children:
                    ctk.CTkButton(row, text="Open >", width=62, height=24,
                                  command=lambda c=child: self._open(c)).pack(side="right")

    def _render_files(self, node):
        frame = self.file_frame
        self._clear(frame)
        files = storage.largest_files_under(self.result, node.path, limit=25)
        if not files:
            ctk.CTkLabel(frame, text="No large files found in this folder.", text_color="gray60").pack(pady=24)
        for f in files:
            row = ctk.CTkFrame(frame)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=format_size(f.size), width=76, anchor="e",
                         font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(8, 8), pady=6)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text, text=os.path.basename(f.path), anchor="w").pack(fill="x")
            path = f.path if len(f.path) <= 80 else "..." + f.path[-77:]
            ctk.CTkLabel(text, text=path, anchor="w", text_color="gray55", font=ctk.CTkFont(size=10)).pack(fill="x")
            ctk.CTkLabel(row, text=f"changed {storage.age_label(f.mtime)}", width=120, anchor="e",
                         text_color="gray60", font=ctk.CTkFont(size=11)).pack(side="left", padx=4)
            ctk.CTkButton(row, text="Show", width=52, height=24, fg_color="gray30", hover_color="gray40",
                          command=lambda p=f.path: self._reveal(p, True)).pack(side="right", padx=8)

    def _reveal(self, path, is_file):
        if not storage.reveal(path, is_file):
            self.status.configure(text=f"Couldn't open {path} in Explorer.")
