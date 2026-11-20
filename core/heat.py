"""
heat.py - "who is making my laptop hot?"

IMPORTANT: Windows does not measure heat for each app. Heat comes from the whole chip. What we CAN see is how
much of the CPU's work each app is doing, and that work is what heats the chip. So this reports each app's
SHARE OF THE CPU'S WORK over the last half minute. It's an estimate (a guide), not a temperature per app.
Graphics (GPU) load isn't counted here.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple


@dataclass
class HeatEntry:
    name: str
    share: float        # percent of all the CPU work done by apps, 0-100
    avg_cpu: float      # that app's average CPU use, in % of the whole machine


@dataclass
class HeatShares:
    entries: List[HeatEntry] = field(default_factory=list)
    others_share: float = 0.0
    total_cpu: float = 0.0          # average CPU % being used by all apps together
    busy: bool = False              # False = the PC is mostly idle, so shares aren't meaningful
    samples: int = 0


class HeatTracker:
    def __init__(self, window: int = 15, idle_below: float = 8.0):
        self.window = window
        self.idle_below = idle_below
        self.samples: Deque[Dict[str, Tuple[str, float]]] = deque(maxlen=window)

    def add(self, rows: List[Dict]) -> None:
        """Add one reading: grouped process rows from core.monitor (each has name and cpu)."""
        reading: Dict[str, Tuple[str, float]] = {}
        for row in rows:
            cpu = float(row.get("cpu", 0.0))
            if cpu <= 0:
                continue
            key = row["name"].lower()
            name, total = reading.get(key, (row["name"], 0.0))
            reading[key] = (name, total + cpu)
        self.samples.append(reading)

    def shares(self, top: int = 5) -> HeatShares:
        n = len(self.samples)
        if n == 0:
            return HeatShares()
        sums: Dict[str, float] = {}
        names: Dict[str, str] = {}
        for reading in self.samples:
            for key, (name, cpu) in reading.items():
                sums[key] = sums.get(key, 0.0) + cpu
                names[key] = name
        averages = {k: v / n for k, v in sums.items()}      # apps missing from a reading count as 0
        total = sum(averages.values())
        if total <= 0:
            return HeatShares(samples=n)
        ranked = sorted(averages.items(), key=lambda kv: kv[1], reverse=True)
        entries = [HeatEntry(names[k], 100.0 * v / total, v) for k, v in ranked[:top]]
        others = 100.0 * sum(v for _k, v in ranked[top:]) / total
        return HeatShares(entries, others, total, total >= self.idle_below, n)
