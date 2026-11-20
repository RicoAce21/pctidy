"""Monitor tab: live CPU / RAM / disk / temperature, a history graph, and the busiest programs."""
import tkinter as tk
from collections import deque

import customtkinter as ctk
from tkinter import messagebox

from core import monitor
from core.heat import HeatTracker
from .bg import BackgroundMixin

REFRESH_MS = 2000
ROWS = 8
HEAT_ROWS = 5
CPU_COLOR = "#4C9BE8"
TEMP_COLOR = "#E8924C"


class Graph(tk.Canvas):
    """A small line graph with a fixed 0-100 scale. Holds several named series."""

    def __init__(self, master, height=120):
        super().__init__(master, height=height, bg="#1c1c1c", highlightthickness=0)
        self.series = {}
        self.bind("<Configure>", lambda _e: self.redraw())

    def add_series(self, name, color, maxlen=60):
        self.series[name] = (color, deque(maxlen=maxlen))

    def push(self, name, value):
        if value is not None:
            self.series[name][1].append(max(0.0, min(100.0, float(value))))

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        for pct in (0, 50, 100):
            y = h - 4 - (h - 8) * pct / 100
            self.create_line(0, y, w, y, fill="#2c2c2c")
        for color, values in self.series.values():
            if len(values) < 2:
                continue
            step = w / (values.maxlen - 1)
            points = []
            for i, v in enumerate(values):
                x = w - (len(values) - 1 - i) * step
                points += [x, h - 4 - (h - 8) * v / 100]
            self.create_line(*points, fill=color, width=2, smooth=True)


def _temp_color(celsius):
    return "#6FBF73" if celsius < 70 else "#E0A94A" if celsius < 85 else "#E0594A"


class MonitorTab(ctk.CTkFrame, BackgroundMixin):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.busy = False
        self.sort_by = "cpu"
        self.heat = HeatTracker(window=15)       # the last ~30 seconds
        self.init_background()
        self._build_ui()
        monitor.top_processes()          # prime psutil so the first real reading isn't all zeros
        monitor.snapshot()
        self.after(1200, self._tick)

    # ---------- layout ----------
    def _card(self, parent, title):
        card = ctk.CTkFrame(parent)
        card.pack(side="left", fill="both", expand=True, padx=4)
        ctk.CTkLabel(card, text=title, text_color="gray65", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=12, pady=(10, 0))
        value = ctk.CTkLabel(card, text="-", font=ctk.CTkFont(size=26, weight="bold"))
        value.pack(anchor="w", padx=12)
        detail = ctk.CTkLabel(card, text="", text_color="gray60", font=ctk.CTkFont(size=11),
                              justify="left", anchor="w", wraplength=130)
        detail.pack(anchor="w", padx=12, pady=(0, 4))
        bar = ctk.CTkProgressBar(card, height=6)
        bar.set(0)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        return value, detail, bar

    def _build_ui(self):
        page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        page.pack(fill="both", expand=True)
        cards = ctk.CTkFrame(page, fg_color="transparent")
        cards.pack(fill="x", padx=4, pady=(2, 8))
        self.cpu_value, self.cpu_detail, self.cpu_bar = self._card(cards, "CPU")
        self.ram_value, self.ram_detail, self.ram_bar = self._card(cards, "Memory")
        self.disk_value, self.disk_detail, self.disk_bar = self._card(cards, "Disk (system drive)")
        self.temp_value, self.temp_detail, self.temp_bar = self._card(cards, "Temperature")

        graph_card = ctk.CTkFrame(page)
        graph_card.pack(fill="x", padx=8, pady=(0, 8))
        legend = ctk.CTkFrame(graph_card, fg_color="transparent")
        legend.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(legend, text="CPU %", text_color=CPU_COLOR, font=ctk.CTkFont(size=11)).pack(side="left")
        ctk.CTkLabel(legend, text="Temperature (°C, same 0-100 scale)", text_color=TEMP_COLOR,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=14)
        ctk.CTkLabel(legend, text="last ~2 minutes", text_color="gray55",
                     font=ctk.CTkFont(size=11)).pack(side="right")
        self.graph = Graph(graph_card)
        self.graph.add_series("cpu", CPU_COLOR)
        self.graph.add_series("temp", TEMP_COLOR)
        self.graph.pack(fill="x", padx=12, pady=(0, 10))

        self.sensor_label = ctk.CTkLabel(page, text="", text_color="gray60", anchor="w", justify="left",
                                         wraplength=800, font=ctk.CTkFont(size=11))
        self.sensor_label.pack(fill="x", padx=10, pady=(0, 6))


        heat = ctk.CTkFrame(page)
        heat.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(heat, text="What's making it hot?", font=ctk.CTkFont(weight="bold"), anchor="w").pack(
            fill="x", padx=14, pady=(10, 0))
        self.heat_summary = ctk.CTkLabel(heat, text="Collecting readings...", anchor="w", justify="left",
                                         wraplength=760, text_color="gray80")
        self.heat_summary.pack(fill="x", padx=14, pady=(2, 6))
        self.heat_rows = []
        for _ in range(HEAT_ROWS):
            line = ctk.CTkFrame(heat, fg_color="transparent")
            line.pack(fill="x", padx=14, pady=1)
            name = ctk.CTkLabel(line, text="", width=210, anchor="w")
            name.pack(side="left")
            bar = ctk.CTkProgressBar(line, width=300, height=10, progress_color=TEMP_COLOR)
            bar.set(0)
            bar.pack(side="left", padx=8)
            pct = ctk.CTkLabel(line, text="", width=46, anchor="e", font=ctk.CTkFont(weight="bold"))
            pct.pack(side="left")
            detail = ctk.CTkLabel(line, text="", width=130, anchor="w", text_color="gray60", font=ctk.CTkFont(size=11))
            detail.pack(side="left", padx=6)
            self.heat_rows.append((name, bar, pct, detail))
        ctk.CTkLabel(
            heat, anchor="w", justify="left", wraplength=760, text_color="gray55", font=ctk.CTkFont(size=11),
            text=("This is an estimate. Windows doesn't measure heat for each app, so this shows each app's share of the "
                  "CPU's work over the last 30 seconds, because that work is what heats the chip. Graphics (GPU) work isn't "
                  "counted, and on laptops where the graphics are built into the CPU chip it heats the same silicon."),
        ).pack(fill="x", padx=14, pady=(6, 12))

        proc_head = ctk.CTkFrame(page, fg_color="transparent")
        proc_head.pack(fill="x", padx=8)
        ctk.CTkLabel(proc_head, text="Busiest programs (all of a program\u2019s processes combined)", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.sort_btn = ctk.CTkSegmentedButton(proc_head, values=["CPU", "Memory"], command=self._on_sort)
        self.sort_btn.set("CPU")
        self.sort_btn.pack(side="right")

        table = ctk.CTkFrame(page)
        table.pack(fill="both", expand=True, padx=8, pady=(6, 4))
        self.rows = []
        for _ in range(ROWS):
            row = ctk.CTkFrame(table, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=1)
            name = ctk.CTkLabel(row, text="", anchor="w", width=300)
            name.pack(side="left")
            cpu = ctk.CTkLabel(row, text="", width=70, anchor="e", text_color="gray75")
            cpu.pack(side="left")
            mem = ctk.CTkLabel(row, text="", width=90, anchor="e", text_color="gray75")
            mem.pack(side="left")
            button = ctk.CTkButton(row, text="End", width=54, height=22, fg_color="gray30",
                                   hover_color="#963B33", font=ctk.CTkFont(size=11))
            button.pack(side="right")
            self.rows.append((row, name, cpu, mem, button))

        self.status = ctk.CTkLabel(page, text="", text_color="gray60", anchor="w", wraplength=800)
        self.status.pack(fill="x", padx=10, pady=(0, 4))

    # ---------- refresh loop ----------
    def _on_sort(self, value):
        self.sort_by = "memory" if value == "Memory" else "cpu"

    def _collect(self, sort_by):
        rows = monitor.top_processes(limit=5000, sort_by=sort_by)       # everything, once
        return {
            "snap": monitor.snapshot(),
            "procs": rows[:ROWS],
            "all": rows,
            "temp": monitor.get_temperatures(),
        }

    def _tick(self):
        # Only do work while this tab is actually on screen, so the monitor doesn't heat the PC it measures.
        try:
            visible = self.winfo_ismapped()
        except Exception:
            return
        if visible and not self.busy:
            self.busy = True
            sort_by = self.sort_by
            self.run_bg(lambda: self._collect(sort_by), self._update)
        self.after(REFRESH_MS, self._tick)

    def _update(self, data, error):
        self.busy = False
        if error:
            self.status.configure(text=f"Couldn't read system stats: {error}")
            return
        snap, procs, temp = data["snap"], data["procs"], data["temp"]

        self.cpu_value.configure(text=f"{snap['cpu_percent']:.0f}%")
        self.cpu_bar.set(snap["cpu_percent"] / 100)
        mhz = snap["cpu_freq_mhz"]
        self.cpu_detail.configure(text=f"{mhz / 1000:.2f} GHz" if mhz else "")

        self.ram_value.configure(text=f"{snap['ram_percent']:.0f}%")
        self.ram_bar.set(snap["ram_percent"] / 100)
        self.ram_detail.configure(text=f"{snap['ram_used'] / 1024**3:.1f} / {snap['ram_total'] / 1024**3:.1f} GB")

        disk = snap["disk"]
        self.disk_value.configure(text=f"{disk['percent']:.0f}%")
        self.disk_bar.set(disk["percent"] / 100)
        self.disk_detail.configure(text=f"{disk['used'] / 1024**3:.0f} / {disk['total'] / 1024**3:.0f} GB used")

        headline = monitor.headline_temp(temp["temps"])
        if headline is None:
            self.temp_value.configure(text="n/a", text_color="gray60")
            self.temp_detail.configure(text="no sensor found")
            self.temp_bar.set(0)
        else:
            self.temp_value.configure(text=f"{headline:.0f}°C", text_color=_temp_color(headline))
            short = {"LibreHardwareMonitor": "LibreHW Monitor", "Windows thermal zone": "Windows"}
            self.temp_detail.configure(text=short.get(temp['source'], temp['source']))
            self.temp_bar.set(min(headline, 100) / 100)

        self.graph.push("cpu", snap["cpu_percent"])
        self.graph.push("temp", headline)
        self.graph.redraw()

        if temp["hint"]:
            self.sensor_label.configure(text=temp["hint"])
        else:
            parts = [f"{t['name']}: {t['celsius']:.0f}°C" for t in temp["temps"][:6]]
            parts += [f"{f['name']}: {f['rpm']:.0f} RPM" for f in temp["fans"][:3]]
            self.sensor_label.configure(text="   |   ".join(parts))

        self.heat.add(data["all"])
        self._update_heat(headline)

        for (row, name, cpu, mem, button), proc in zip(self.rows, procs + [None] * ROWS):
            if proc is None:
                name.configure(text=""), cpu.configure(text=""), mem.configure(text="")
                button.configure(state="disabled", text="")
                continue
            name.configure(text=proc["name"] + (f"   x{proc['count']}" if proc["count"] > 1 else ""))
            cpu.configure(text=f"{proc['cpu']:.1f}%")
            mem.configure(text=f"{proc['mem_mb']:.0f} MB")
            if proc["protected"]:
                button.configure(text="Locked", state="disabled")
            else:
                button.configure(text="End", state="normal",
                                 command=lambda p=proc: self._end_task(p["pids"], p["name"]))

    # ---------- the "what's making it hot?" meter ----------
    def _update_heat(self, temp):
        result = self.heat.shares(HEAT_ROWS)
        hot = f"CPU is at {temp:.0f}°C. " if temp is not None else ""
        if not result.entries or not result.busy:
            self.heat_summary.configure(
                text=f"{hot}Your apps aren't doing much work right now ({result.total_cpu:.0f}% CPU), so they're adding "
                     "little heat. Start your game or heavy task and watch this fill in.")
            lines = result.entries if result.entries else []
        else:
            top = result.entries[0]
            self.heat_summary.configure(
                text=f"{hot}Over the last {result.samples * REFRESH_MS // 1000} seconds, {top.name} did about "
                     f"{top.share:.0f}% of the CPU's work (apps together are using {result.total_cpu:.0f}% of the CPU).")
            lines = result.entries
        for i, (name, bar, pct, detail) in enumerate(self.heat_rows):
            if i < len(lines):
                entry = lines[i]
                name.configure(text=entry.name)
                bar.set(min(entry.share / 100.0, 1.0))
                pct.configure(text=f"{entry.share:.0f}%")
                detail.configure(text=f"avg {entry.avg_cpu:.1f}% CPU")
            else:
                name.configure(text="")
                bar.set(0)
                pct.configure(text="")
                detail.configure(text="")

    # ---------- ending a program ----------
    def _end_task(self, pids, name):
        what = f"{name} and all {len(pids)} of its processes" if len(pids) > 1 else name
        if not messagebox.askyesno(
                f"End {name}?",
                f"This closes {what}. Anything unsaved in it will be lost.\n\nContinue?"):
            return
        self.run_bg(lambda: monitor.end_processes(pids), self._end_done)

    def _end_done(self, result, error):
        self.status.configure(text=f"Failed: {error}" if error else result[1])
