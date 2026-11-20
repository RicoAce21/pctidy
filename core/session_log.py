"""
session_log.py - record how your laptop behaves, then compare two recordings.

Workflow: record a normal session (same activity each time!), change ONE thing (a power mode, closing apps,
cleaning the fans...), record again, then compare. It only reads numbers the Monitor tab already shows
(CPU use, CPU speed, memory, temperature), so it changes nothing on your PC.
"""
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import safety

INTERVAL = 2.0
AUTOSAVE_EVERY = 30          # samples
MAX_SAMPLES = 7200           # 4 hours at 2 seconds: a safety cap
HOT_C = 85.0


@dataclass
class Session:
    id: str
    name: str
    started: float
    interval: float
    plan: str = ""
    samples: List[Dict] = field(default_factory=list)
    ended: Optional[float] = None
    partial: bool = False

    @property
    def duration(self) -> float:
        return len(self.samples) * self.interval


# ---------------------------------------------------------------- recording
def default_sampler() -> Dict:
    """One reading of everything we log. Uses the same numbers as the Monitor tab."""
    from . import monitor
    snap = monitor.snapshot()
    temps = monitor.get_temperatures()
    procs = monitor.top_processes(limit=5, sort_by="cpu")
    return {"t": time.time(), "cpu": snap["cpu_percent"], "mhz": snap["cpu_freq_mhz"], "ram": snap["ram_percent"],
            "temp": monitor.headline_temp(temps["temps"]),
            "apps": {p["name"]: round(p["cpu"], 1) for p in procs if p["cpu"] > 0}}


def sessions_dir() -> Path:
    return safety.APP_DIR / "sessions"


class Recorder:
    def __init__(self, name: str, sampler: Optional[Callable[[], Dict]] = None, interval: Optional[float] = None,
                 plan: str = "", directory: Optional[Path] = None):
        self.sampler = sampler or default_sampler
        self.session = Session(uuid.uuid4().hex[:10], name.strip() or "Untitled session", time.time(),
                               interval if interval is not None else INTERVAL, plan)
        self.directory = directory
        self.errors = 0
        self.latest: Optional[Dict] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set() and len(self.session.samples) < MAX_SAMPLES:
            try:
                sample = self.sampler()
                self.session.samples.append(sample)
                self.latest = sample
                if len(self.session.samples) % AUTOSAVE_EVERY == 0:
                    self.session.partial = True
                    save(self.session, self.directory)      # so a crash doesn't lose the whole recording
            except Exception:                                   # one bad reading must not end the recording
                self.errors += 1
            self._stop.wait(self.session.interval)

    def stop(self) -> Session:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.session.interval + 5)
        self.session.ended = time.time()
        self.session.partial = False
        save(self.session, self.directory)
        return self.session


# ---------------------------------------------------------------- saving and loading
def _path(session_id: str, directory: Optional[Path]) -> Path:
    return (directory or sessions_dir()) / f"{session_id}.json"


def save(session: Session, directory: Optional[Path] = None) -> Optional[Path]:
    target = _path(session.id, directory)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(session.__dict__), encoding="utf-8")
        os.replace(temp, target)                    # replace in one step so a half-written file never exists
        return target
    except OSError:
        return None


def load(path: Path) -> Optional[Session]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return Session(**data)
    except (OSError, ValueError, TypeError):
        return None


def list_sessions(directory: Optional[Path] = None) -> List[Session]:
    folder = directory or sessions_dir()
    sessions = [s for s in (load(p) for p in sorted(folder.glob("*.json"))) if s is not None] if folder.exists() else []
    return sorted(sessions, key=lambda s: s.started, reverse=True)


def delete(session_id: str, directory: Optional[Path] = None) -> bool:
    try:
        _path(session_id, directory).unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- summarising and comparing
@dataclass
class Summary:
    name: str
    duration: float
    samples: int
    plan: str
    cpu_avg: float
    mhz_avg: Optional[float]
    ram_avg: float
    temp_avg: Optional[float]
    temp_peak: Optional[float]
    seconds_hot: float
    top_apps: List[tuple]           # (name, average CPU %) busiest first


def summarize(session: Session) -> Summary:
    s = session.samples
    n = len(s)

    def mean(key):
        vals = [x[key] for x in s if x.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    temps = [x["temp"] for x in s if x.get("temp") is not None]
    app_totals: Dict[str, float] = {}
    for x in s:
        for name, cpu in (x.get("apps") or {}).items():
            app_totals[name] = app_totals.get(name, 0.0) + cpu
    top = sorted(((k, v / n) for k, v in app_totals.items()), key=lambda kv: kv[1], reverse=True)[:5] if n else []
    return Summary(session.name, session.duration, n, session.plan, mean("cpu") or 0.0, mean("mhz"),
                   mean("ram") or 0.0, (sum(temps) / len(temps)) if temps else None, max(temps) if temps else None,
                   sum(1 for t in temps if t > HOT_C) * session.interval, top)


@dataclass
class Row:
    metric: str
    before: str
    after: str
    change: str
    verdict: str        # "better" | "worse" | "same" | "info"


@dataclass
class Comparison:
    rows: List[Row]
    sentences: List[str]
    warnings: List[str]


def _fmt(value: Optional[float], unit: str = "", digits: int = 0) -> str:
    return "-" if value is None else f"{value:.{digits}f}{unit}"


def compare(before: Summary, after: Summary) -> Comparison:
    rows: List[Row] = []
    sentences: List[str] = []
    warnings: List[str] = []

    def lower_is_better(metric, b, a, unit, digits, threshold):
        if b is None or a is None:
            rows.append(Row(metric, _fmt(b, unit, digits), _fmt(a, unit, digits), "-", "info"))
            return None
        diff = a - b
        verdict = "better" if diff <= -threshold else "worse" if diff >= threshold else "same"
        rows.append(Row(metric, _fmt(b, unit, digits), _fmt(a, unit, digits), f"{diff:+.{digits}f}{unit}", verdict))
        return diff

    t_avg = lower_is_better("Average temperature", before.temp_avg, after.temp_avg, " °C", 1, 1.5)
    t_peak = lower_is_better("Peak temperature", before.temp_peak, after.temp_peak, " °C", 0, 2)
    lower_is_better("Time above 85 °C", before.seconds_hot, after.seconds_hot, " s", 0, 5) if (
        before.temp_avg is not None and after.temp_avg is not None) else None

    speed_pct = None
    if before.mhz_avg and after.mhz_avg:
        speed_pct = 100.0 * (after.mhz_avg - before.mhz_avg) / before.mhz_avg
        verdict = "worse" if speed_pct <= -3 else "better" if speed_pct >= 3 else "same"
        rows.append(Row("Average CPU speed", _fmt(before.mhz_avg, " MHz"), _fmt(after.mhz_avg, " MHz"),
                        f"{speed_pct:+.0f}%", verdict))
    else:
        rows.append(Row("Average CPU speed", _fmt(before.mhz_avg, " MHz"), _fmt(after.mhz_avg, " MHz"), "-", "info"))
    rows.append(Row("Average CPU load", _fmt(before.cpu_avg, "%"), _fmt(after.cpu_avg, "%"),
                    f"{after.cpu_avg - before.cpu_avg:+.0f} pts", "info"))
    rows.append(Row("Average memory use", _fmt(before.ram_avg, "%"), _fmt(after.ram_avg, "%"),
                    f"{after.ram_avg - before.ram_avg:+.0f} pts", "info"))
    rows.append(Row("Length of recording", f"{before.duration / 60:.1f} min", f"{after.duration / 60:.1f} min", "", "info"))

    # ---- plain-language summary
    if t_peak is not None and t_avg is not None:
        if t_peak <= -2 or t_avg <= -1.5:
            sentences.append(f"Cooler: average temperature {before.temp_avg:.0f} → {after.temp_avg:.0f} °C "
                             f"and the peak {before.temp_peak:.0f} → {after.temp_peak:.0f} °C.")
        elif t_peak >= 2 or t_avg >= 1.5:
            sentences.append(f"Hotter: average temperature {before.temp_avg:.0f} → {after.temp_avg:.0f} °C "
                             f"and the peak {before.temp_peak:.0f} → {after.temp_peak:.0f} °C.")
        else:
            sentences.append("Temperature is about the same in both recordings.")
    if speed_pct is not None:
        if speed_pct <= -3:
            sentences.append(f"The CPU ran about {abs(speed_pct):.0f}% slower on average, which is the cost of the change.")
        elif speed_pct >= 3:
            sentences.append(f"The CPU ran about {speed_pct:.0f}% faster on average.")
        else:
            sentences.append("CPU speed stayed about the same, so you lost no noticeable performance.")
    if t_peak is not None and speed_pct is not None and (t_peak <= -2 or (t_avg or 0) <= -1.5):
        sentences.append("That's a good trade: cooler with little speed lost." if speed_pct > -5 else
                         "Cooler, but at a real cost in speed. Whether that's worth it is your call.")

    # ---- is the comparison fair?
    if before.temp_avg is None or after.temp_avg is None:
        warnings.append("One recording has no temperature (LibreHardwareMonitor wasn't running), so heat can't be compared.")
    longer, shorter = max(before.duration, after.duration), min(before.duration, after.duration)
    if shorter > 0 and longer / shorter > 1.5:
        warnings.append("The recordings are very different lengths, which can make the comparison unfair.")
    if abs(after.cpu_avg - before.cpu_avg) > 15:
        warnings.append("The CPU was doing quite different amounts of work in the two recordings, so they aren't a fair "
                        "comparison. Repeat the same activity (for example the same game for the same time).")
    if before.samples < 15 or after.samples < 15:
        warnings.append("A recording is under about 30 seconds long. Record a few minutes for reliable numbers.")
    return Comparison(rows, sentences, warnings)
