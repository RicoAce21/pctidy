"""
startup.py - list and switch on/off the programs that launch when Windows starts.

How disabling works (important - it is NOT deleting):
Windows Task Manager's "Startup apps" tab keeps an on/off flag for every startup entry in a
registry area called StartupApproved. We flip that same flag. The original entry stays exactly
where it was, so switching it back on restores everything. Takes effect at next restart.

Only works on Windows (uses the built-in `winreg` module).
"""
import os
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .safety import log_event

try:
    import winreg
except ImportError:      # not on Windows (e.g. when running the tests elsewhere)
    winreg = None

RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32_PATH = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED_BASE = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


@dataclass
class StartupItem:
    name: str
    command: str
    source: str             # human-readable place it was found
    enabled: bool
    needs_admin: bool       # changing it requires administrator rights
    hive: object            # registry hive holding the on/off flag
    approved_path: str      # registry path of the on/off flag
    rating: str             # "Broken" | "Optional" | "Unknown" | "Keep"
    reason: str


# ---------------------------------------------------------------- helping guess what is what
# These lists are a GUESS based on names - not a measurement of real startup time.
KEEP_WORDS = [
    "securityhealth", "defender", "realtek", "nvidia", "intel", "igfx", "amd", "radeon",
    "synaptics", "touchpad", "elan", "hotkey", "antivirus", "kaspersky", "norton",
    "mcafee", "bitdefender", "avast", "windowsdefender",
]
OPTIONAL_WORDS = [
    "teams", "discord", "spotify", "steam", "epicgames", "epic games", "onedrive", "dropbox",
    "googledrive", "adobe", "acrobat", "creative cloud", "ccxprocess", "skype", "zoom",
    "slack", "whatsapp", "telegram", "utorrent", "bittorrent", "jusched", "java update",
    "itunes", "ipod", "cortana", "msedge", "microsoftedge", "googlechrome", "chrome",
    "brave", "opera", "onenote", "nahimic",
]


def extract_exe(command: str) -> str:
    """Pull the program path out of a startup command like '"C:\\x\\a.exe" --minimized'."""
    command = os.path.expandvars(command.strip())
    if command.startswith('"'):
        end = command.find('"', 1)
        if end > 0:
            return command[1:end]
    match = re.match(r"^(.*?\.(?:exe|bat|cmd|com|lnk))", command, re.IGNORECASE)
    if match:
        return match.group(1)
    return command.split(" ")[0] if command else ""


def classify(name: str, command: str, exe_missing: bool) -> Tuple[str, str]:
    if exe_missing:
        return "Broken", "The program it points to no longer exists, so it does nothing."
    text = f"{name} {command}".lower()
    if name.lower().startswith(KEEP_PREFIXES):
        return "Keep", "Looks like a driver or security tool. Safer to leave on."
    for word in KEEP_WORDS:
        if word in text:
            return "Keep", "Looks like a driver or security tool. Safer to leave on."
    for word in OPTIONAL_WORDS:
        if word in text:
            return "Optional", "Usually fine to disable. The app still works when you open it yourself."
    return "Unknown", "Not recognised. Search the name before disabling."


# Names that START with these are keepers (a plain substring match on "rtk" would also hit
# words like "StartKey", so Realtek's short name "Rtk..." is matched at the beginning only).
KEEP_PREFIXES = ("rtk",)

RATING_ORDER = {"Broken": 0, "Optional": 1, "Unknown": 2, "Keep": 3}


# ---------------------------------------------------------------- registry helpers
def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _sources():
    w = winreg
    user_folder = Path(os.environ.get("APPDATA", "")).joinpath(
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    common_folder = Path(os.environ.get("ProgramData", "")).joinpath(
        "Microsoft", "Windows", "Start Menu", "Programs", "StartUp")
    return [
        dict(kind="registry", source="Registry (your account)", hive=w.HKEY_CURRENT_USER,
             path=RUN_PATH, approved=APPROVED_BASE + r"\Run", admin=False),
        dict(kind="registry", source="Registry (all users)", hive=w.HKEY_LOCAL_MACHINE,
             path=RUN_PATH, approved=APPROVED_BASE + r"\Run", admin=True),
        dict(kind="registry", source="Registry (all users, 32-bit)", hive=w.HKEY_LOCAL_MACHINE,
             path=RUN32_PATH, approved=APPROVED_BASE + r"\Run32", admin=True),
        dict(kind="folder", source="Startup folder (your account)", hive=w.HKEY_CURRENT_USER,
             path=user_folder, approved=APPROVED_BASE + r"\StartupFolder", admin=False),
        dict(kind="folder", source="Startup folder (all users)", hive=w.HKEY_LOCAL_MACHINE,
             path=common_folder, approved=APPROVED_BASE + r"\StartupFolder", admin=True),
    ]


def _read_run_values(hive, path):
    """All (name, command) pairs under a Run key. Missing key -> empty list."""
    out = []
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _type = winreg.EnumValue(key, index)
                except OSError:
                    break
                out.append((name, str(value)))
                index += 1
    except OSError:
        pass
    return out


def _is_enabled(hive, approved_path, name) -> bool:
    """Task Manager's rule: first byte odd (03) = disabled, even (02/06) = enabled. Missing = enabled."""
    try:
        with winreg.OpenKey(hive, approved_path, 0, winreg.KEY_READ) as key:
            data, _type = winreg.QueryValueEx(key, name)
            if isinstance(data, (bytes, bytearray)) and len(data) > 0:
                return (data[0] & 1) == 0
    except OSError:
        pass
    return True


def _approved_bytes(enabled: bool) -> bytes:
    if enabled:
        return b"\x02" + b"\x00" * 11
    filetime = int((time.time() + 11644473600) * 10_000_000)   # Windows FILETIME
    return b"\x03\x00\x00\x00" + struct.pack("<Q", filetime)


# ---------------------------------------------------------------- public API
def list_startup_items() -> List[StartupItem]:
    if winreg is None:
        return []
    items: List[StartupItem] = []
    for src in _sources():
        if src["kind"] == "registry":
            entries = [(n, c, c) for n, c in _read_run_values(src["hive"], src["path"])]
        else:
            folder = Path(src["path"])
            entries = []
            if folder.exists():
                for f in folder.iterdir():
                    if f.is_file() and f.name.lower() != "desktop.ini":
                        entries.append((f.name, str(f), str(f)))
        for name, command, target in entries:
            exe = extract_exe(target)
            exe_missing = bool(exe) and not os.path.exists(exe)
            rating, reason = classify(name, command, exe_missing)
            items.append(StartupItem(
                name=name, command=command, source=src["source"],
                enabled=_is_enabled(src["hive"], src["approved"], name),
                needs_admin=src["admin"], hive=src["hive"], approved_path=src["approved"],
                rating=rating, reason=reason))
    items.sort(key=lambda i: (RATING_ORDER.get(i.rating, 9), i.name.lower()))
    return items


def set_enabled(item: StartupItem, enabled: bool) -> Tuple[bool, str]:
    """Switch an item on or off. Returns (success, message to show the user)."""
    if winreg is None:
        return False, "Startup management only works on Windows."
    try:
        key = winreg.CreateKeyEx(item.hive, item.approved_path, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, item.name, 0, winreg.REG_BINARY, _approved_bytes(enabled))
    except PermissionError:
        return False, ("Windows needs administrator rights to change this one. "
                       "Close the app and run it from a terminal opened as administrator.")
    except OSError as error:
        return False, f"Windows refused the change: {error}"
    item.enabled = enabled
    word = "enabled" if enabled else "disabled"
    log_event(f"startup_{word}", f"{item.name} ({item.source})")
    return True, f"{item.name} {word}. Takes effect at your next restart."
