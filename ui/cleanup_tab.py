"""Cleanup tab: scan -> preview -> tick -> confirm -> clean."""
import customtkinter as ctk
from tkinter import messagebox

from core.cleaner import clean_result, has_trash_and_bin, order_for_cleaning
from core.scanner import format_size, get_categories, scan_category
from .bg import BackgroundMixin


class CleanupTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.categories = get_categories()
        self.results = {}
        self.check_vars = {}
        self.checkboxes = {}
        self.size_labels = {}
        self.preview_buttons = {}
        self.last_clean_msg = ""
        self.init_background()
        self._build_ui()

    # ---------- layout ----------
    def _build_ui(self):
        ctk.CTkLabel(self, text="Find junk, preview it, then clean only what you choose.",
                     text_color="gray70").pack(anchor="w", padx=8, pady=(4, 10))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8)
        self.scan_btn = ctk.CTkButton(row, text="Scan", width=110,
                                      command=lambda: self.start_scan(clear_message=True))
        self.scan_btn.pack(side="left")
        self.clean_btn = ctk.CTkButton(row, text="Clean selected", width=140, state="disabled",
                                       fg_color="#B5483E", hover_color="#963B33",
                                       command=self.confirm_clean)
        self.clean_btn.pack(side="left", padx=10)
        self.total_label = ctk.CTkLabel(row, text="", text_color="gray70")
        self.total_label.pack(side="right")

        self.list_frame = ctk.CTkScrollableFrame(self)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=12)
        for cat in self.categories:
            self._add_category_row(cat)

        self.status = ctk.CTkLabel(self, text="Ready. Press Scan to look for junk.",
                                   text_color="gray70", anchor="w", wraplength=760, justify="left")
        self.status.pack(fill="x", padx=8, pady=(0, 6))

    def _add_category_row(self, cat):
        row = ctk.CTkFrame(self.list_frame)
        row.pack(fill="x", pady=4)

        var = ctk.BooleanVar(value=False)
        self.check_vars[cat.id] = var
        box = ctk.CTkCheckBox(row, text="", variable=var, width=24, state="disabled",
                              command=self._update_total)
        box.pack(side="left", padx=(12, 4), pady=12)
        self.checkboxes[cat.id] = box

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(text_col, text=cat.name, font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(text_col, text=cat.description, text_color="gray65", anchor="w", wraplength=420,
                     justify="left", font=ctk.CTkFont(size=11)).pack(fill="x")

        size_label = ctk.CTkLabel(row, text="-", width=80, anchor="e")
        size_label.pack(side="left", padx=6)
        self.size_labels[cat.id] = size_label

        preview = ctk.CTkButton(row, text="Preview", width=70, height=26, state="disabled",
                                fg_color="gray30", hover_color="gray40",
                                command=lambda c=cat: self.show_preview(c))
        preview.pack(side="left", padx=(0, 12))
        self.preview_buttons[cat.id] = preview

    # ---------- scan ----------
    def start_scan(self, clear_message=False):
        if clear_message:
            self.last_clean_msg = ""
        self.scan_btn.configure(state="disabled")
        self.clean_btn.configure(state="disabled")
        for label in self.size_labels.values():
            label.configure(text="...")
        if self.last_clean_msg:
            self.status.configure(text=f"{self.last_clean_msg}  Rescanning to update sizes (about 30s)...")
        else:
            self.status.configure(text="Scanning... this can take a minute on a big drive.")
        self.run_bg(lambda: {c.id: scan_category(c) for c in self.categories}, self._scan_done)

    def _scan_done(self, results, error):
        self.scan_btn.configure(state="normal")
        if error:
            self.status.configure(text=f"Scan failed: {error}")
            return
        self.results = results
        for cat in self.categories:
            r = results[cat.id]
            self.size_labels[cat.id].configure(text=format_size(r.total_size))
            has_something = r.total_size > 0 or r.item_count > 0
            self.checkboxes[cat.id].configure(state="normal" if has_something else "disabled")
            self.check_vars[cat.id].set(False)     # nothing is pre-ticked: you opt in
            can_preview = has_something and cat.kind == "files"
            self.preview_buttons[cat.id].configure(state="normal" if can_preview else "disabled")
        if self.last_clean_msg:
            self.status.configure(text=f"{self.last_clean_msg}  Sizes below are updated.")
        else:
            self.status.configure(text="Scan done. Preview what you like, tick categories, then Clean.")
        self._update_total()

    # ---------- preview ----------
    def show_preview(self, cat):
        result = self.results.get(cat.id)
        if not result:
            return
        win = ctk.CTkToplevel(self)
        win.title(f"Preview - {cat.name}")
        win.geometry("760x460")
        win.after(150, win.focus)
        mode = "sent to the Recycle Bin (undoable)" if cat.mode == "trash" else "permanently deleted"
        ctk.CTkLabel(win, text=f"{result.item_count} files, {format_size(result.total_size)} - would be "
                               f"{mode}. Showing the 300 largest.", anchor="w").pack(fill="x", padx=16, pady=(14, 6))
        box = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas", size=11))
        box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        for f in sorted(result.files, key=lambda x: x.size, reverse=True)[:300]:
            box.insert("end", f"{format_size(f.size):>10}   {f.path}\n")
        box.configure(state="disabled")

    # ---------- totals + clean ----------
    def _selected(self):
        picked = [self.results[c.id] for c in self.categories
                  if self.check_vars[c.id].get() and c.id in self.results]
        return order_for_cleaning(picked)        # Recycle Bin first - see core/cleaner.py

    def _update_total(self):
        selected = self._selected()
        total = sum(r.total_size for r in selected)
        self.total_label.configure(text=f"Selected: {format_size(total)}" if selected else "")
        self.clean_btn.configure(state="normal" if selected else "disabled")

    def confirm_clean(self):
        selected = self._selected()
        if not selected:
            return
        lines = []
        for r in selected:
            permanent = r.category.kind == "recycle_bin" or r.category.mode != "trash"
            how = "PERMANENT" if permanent else "Recycle Bin, undoable"
            lines.append(f"  - {r.category.name}: {format_size(r.total_size)} ({how})")
        total = sum(r.total_size for r in selected)
        note = ""
        if has_trash_and_bin(selected):
            note = ("\n\nNote: the Recycle Bin is emptied FIRST, so installers sent there during "
                    "this clean will still be in it afterwards.")
        ok = messagebox.askyesno(
            "Confirm clean",
            "This will clean, in this order:\n\n" + "\n".join(lines) +
            f"\n\nTotal: {format_size(total)}{note}\n\nPERMANENT items cannot be undone. Continue?")
        if not ok:
            return
        self.scan_btn.configure(state="disabled")
        self.clean_btn.configure(state="disabled")
        self.status.configure(text="Cleaning...")
        self.run_bg(lambda: [(r, clean_result(r)) for r in selected], self._clean_done)

    def _clean_done(self, outcomes, error):
        self.scan_btn.configure(state="normal")
        if error:
            self.status.configure(text=f"Clean failed: {error}")
            return
        freed = sum(o.bytes_freed for _r, o in outcomes)
        failed = sum(o.failed for _r, o in outcomes)
        msg = f"Freed {format_size(freed)}."
        if failed:
            msg += f" {failed} files were skipped (in use or no permission)."
        self.last_clean_msg = msg
        self.start_scan()
