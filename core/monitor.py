"""
monitor.py - live CPU / RAM / disk / process / temperature readings.

Temperatures: Windows does not give Python a reliable CPU temperature by itself.
  1. Best: the free LibreHardwareMonitor app, with its "Remote Web Server" switched on
     (Options -> Remote Web Server -> Run). We read http://localhost:8085/data.json.
  2. Fallback: Windows' own ACPI thermal zone (works on some laptops, often needs admin).
  3. Otherwise we say "unavailable" and show how to enable option 1.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

import psutil

LHM_URL = "http://localhost:8085/data.json"
# Talk to LibreHardwareMonitor directly, never through a proxy. Without this, a system proxy (some VPNs,
# college or office networks) can swallow the request and make a running program look unreachable.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HINT_NO_TEMP = ("No temperature source found. Install LibreHardwareMonitor, run it as administrator, "
                "then Options -> Remote Web Server -> Run. This app picks it up automatically.")


# ---------------------------------------------------------------- shared readings
# psutil measures CPU use "since the last time anyone asked", so two callers (the live Monitor tab and a
# session recording) would keep stealing each other's measuring window and get noisy numbers. Readings taken
# within CACHE_SECONDS of each other are therefore shared.
CACHE_SECONDS = 0.9
_cache_lock = threading.Lock()
_cache: Dict[str, tuple] = {}


def _cached(key: str, make):
    with _cache_lock:
        stamp, value = _cache.get(key, (0.0, None))
        if value is not None and time.time() - stamp < CACHE_SECONDS:
            return value
        value = make()
        _cache[key] = (time.time(), value)
        return value


# ---------------------------------------------------------------- basic stats
def snapshot() -> Dict:
    return dict(_cached("snapshot", _snapshot))


def _snapshot() -> Dict:
    drive = (os.environ.get("SystemDrive", "C:") + "\\") if sys.platform == "win32" else "/"
    vm = psutil.virtual_memory()
    try:
        du = psutil.disk_usage(drive)
        disk = {"percent": du.percent, "used": du.used, "total": du.total}
    except OSError:
        disk = {"percent": 0.0, "used": 0, "total": 0}
    freq = None
    try:
        f = psutil.cpu_freq()
        freq = f.current if f else None
    except Exception:
        pass
    battery = None
    try:
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if b:
            battery = {"percent": b.percent, "plugged": b.power_plugged}
    except Exception:
        pass
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_freq_mhz": freq,
        "ram_percent": vm.percent,
        "ram_used": vm.used,
        "ram_total": vm.total,
        "disk": disk,
        "battery": battery,
    }


# ---------------------------------------------------------------- processes
PROTECTED_NAMES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "lsm.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "explorer.exe", "memcompression", "memory compression",
    "securityhealthservice.exe", "msmpeng.exe", "sihost.exe", "ctfmon.exe",
}


def is_protected(name: str, pid: int) -> bool:
    """Processes this app will never end: Windows essentials, security tools, and itself."""
    return pid <= 4 or pid == os.getpid() or (name or "").lower() in PROTECTED_NAMES


def group_by_app(rows: List[Dict]) -> List[Dict]:
    """Merge rows with the same program name (all of Chrome's processes become one line)."""
    groups: Dict[str, Dict] = {}
    for r in rows:
        g = groups.setdefault(r["name"].lower(), {"pid": r["pid"], "name": r["name"], "cpu": 0.0,
                                                  "mem_mb": 0.0, "protected": False, "count": 0, "pids": []})
        g["cpu"] += r["cpu"]
        g["mem_mb"] += r["mem_mb"]
        g["count"] += 1
        g["pids"].append(r["pid"])
        g["protected"] = g["protected"] or r["protected"]    # one protected member locks the whole group
    return list(groups.values())


def top_processes(limit: int = 8, sort_by: str = "cpu", group: bool = True) -> List[Dict]:
    """
    Busiest processes. CPU % is "share of the whole machine", like Task Manager.
    The first call always shows 0% CPU (psutil needs two readings to measure) - call it once
    at startup to 'prime' it.
    """
    rows = [dict(r, pids=list(r["pids"])) for r in _cached("procs", _read_processes)]
    if group:
        rows = group_by_app(rows)
    key = "mem_mb" if sort_by == "memory" else "cpu"
    rows.sort(key=lambda r: r[key], reverse=True)
    return rows[:limit]


def _read_processes() -> List[Dict]:
    ncpu = psutil.cpu_count() or 1
    rows = []
    for proc in psutil.process_iter():
        try:
            if proc.pid == 0:
                continue
            cpu = proc.cpu_percent(None) / ncpu
            mem = proc.memory_info().rss
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        rows.append({"pid": proc.pid, "name": name, "cpu": cpu, "mem_mb": mem / (1024 * 1024),
                     "protected": is_protected(name, proc.pid), "count": 1, "pids": [proc.pid]})
    return rows


def end_process(pid: int) -> Tuple[bool, str]:
    """Politely ask a process to close (like clicking X). Refuses protected ones."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if is_protected(name, pid):
            return False, f"{name} is needed by Windows or by this app, so it was not touched."
        proc.terminate()
        try:
            proc.wait(3)
        except psutil.TimeoutExpired:
            return False, f"{name} didn't close in 3 seconds. It may be busy or frozen."
        return True, f"Closed {name}."
    except psutil.NoSuchProcess:
        return True, "That process had already closed."
    except psutil.AccessDenied:
        return False, "Windows won't let this app close it. Try running the app as administrator."


def end_processes(pids: List[int]) -> Tuple[bool, str]:
    """Close every process in a group. Each one still goes through the protection check."""
    closed, problems = 0, []
    for pid in pids:
        ok, msg = end_process(pid)
        if ok:
            closed += 1
        else:
            problems.append(msg)
    if problems:
        return False, f"Closed {closed} of {len(pids)}. {problems[0]}"
    return True, f"Closed {closed} process{'es' if closed != 1 else ''}."


# ---------------------------------------------------------------- temperatures
_TEMP_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(?:°|Â°|º)?\s*C\b")
_RPM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*RPM", re.IGNORECASE)


def _walk(node, hardware, temps, fans):
    text = node.get("Text", "")
    value = node.get("Value")
    if isinstance(value, str):
        rpm = _RPM_RE.search(value)
        temp = _TEMP_RE.search(value)
        if rpm:
            fans.append({"hardware": hardware, "name": text, "rpm": float(rpm.group(1).replace(",", "."))})
        elif temp:
            celsius = float(temp.group(1).replace(",", "."))
            if 0 < celsius < 150:      # ignore junk readings
                temps.append({"hardware": hardware, "name": text, "celsius": celsius})
    for child in node.get("Children") or []:
        _walk(child, hardware, temps, fans)


def parse_lhm(data: dict) -> Tuple[List[Dict], List[Dict]]:
    """Turn LibreHardwareMonitor's JSON tree into flat temperature and fan lists."""
    temps, fans = [], []
    for computer in data.get("Children") or []:
        for hardware in computer.get("Children") or []:
            _walk(hardware, hardware.get("Text", ""), temps, fans)
    return temps, fans


def _read_lhm(timeout: float = 0.6):
    try:
        with _opener.open(LHM_URL, timeout=timeout) as resp:
            return parse_lhm(json.loads(resp.read().decode("utf-8", errors="replace")))
    except Exception:
        return None


_wmi_cache = {"at": 0.0, "value": None, "ttl": 20}


def _read_wmi():
    """Windows ACPI thermal zone. Slow (starts PowerShell), so the result is cached."""
    if sys.platform != "win32":
        return None
    now = time.time()
    if now - _wmi_cache["at"] < _wmi_cache["ttl"]:
        return _wmi_cache["value"]
    value = None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
             "| Select-Object -ExpandProperty CurrentTemperature"],
            capture_output=True, text=True, timeout=4, creationflags=0x08000000)
        temps = []
        for i, line in enumerate(out.stdout.split(), start=1):
            if line.strip().isdigit():
                celsius = int(line) / 10 - 273.15      # reported in tenths of a kelvin
                if 0 < celsius < 150:
                    temps.append({"hardware": "Windows thermal zone", "name": f"Zone {i}", "celsius": celsius})
        value = (temps, []) if temps else None
    except Exception:
        value = None
    _wmi_cache.update(at=now, value=value, ttl=20 if value else 90)
    return value


def _lhm_status(timeout: float = 0.6) -> str:
    """Why isn't LibreHardwareMonitor giving us temperatures? 'unreachable' | 'running_no_sensors' | 'bad_response' | 'ok'."""
    try:
        resp = _opener.open(LHM_URL, timeout=timeout)
    except urllib.error.HTTPError:
        return "bad_response"          # something answered on that port, but it isn't LibreHardwareMonitor's data
    except OSError:
        return "unreachable"           # connection refused or timed out: not running, or its web server is off
    try:
        with resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        temps, _fans = parse_lhm(data)
    except (ValueError, AttributeError, TypeError):
        return "bad_response"
    return "ok" if temps else "running_no_sensors"


HINTS = {
    "unreachable": ("Can't reach LibreHardwareMonitor. Check that it's open, that you started it as administrator, and "
                    "that Options -> Remote Web Server -> Run is ticked."),
    "running_no_sensors": ("LibreHardwareMonitor is running but isn't reporting any temperatures. Close it, then reopen it "
                           "with right-click -> Run as administrator. If it still shows none, Windows Security may be "
                           "blocking its sensor driver."),
    "bad_response": ("Something is answering on port 8085 but it isn't LibreHardwareMonitor. Close the other program, or "
                     "change the port in LibreHardwareMonitor's Remote Web Server options."),
}


def get_temperatures() -> Dict:
    for source, reader in (("LibreHardwareMonitor", _read_lhm), ("Windows thermal zone", _read_wmi)):
        result = reader()
        if result and (result[0] or result[1]):
            return {"source": source, "temps": result[0], "fans": result[1], "hint": None, "status": "ok"}
    status = _lhm_status()
    return {"source": None, "temps": [], "fans": [], "hint": HINTS.get(status, HINT_NO_TEMP), "status": status}


def headline_temp(temps: List[Dict]) -> Optional[float]:
    """The one number to show big: prefer CPU package/core sensors, else the hottest reading."""
    if not temps:
        return None
    cpu = [t for t in temps if "cpu" in (t["hardware"] + t["name"]).lower()
           or "core" in t["name"].lower() or "package" in t["name"].lower()]
    pool = cpu or temps
    return max(t["celsius"] for t in pool)
