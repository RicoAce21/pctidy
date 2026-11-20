"""
safety.py - the "seatbelt" of the app.

Every delete in this app must pass through is_safe_to_delete() first.
If you ever add a new feature, route it through here too.
"""
import json
import os
import time
from functools import lru_cache
from pathlib import Path

# Where the app keeps its own files (the action log lives here)
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PCTidy"
LOG_FILE = APP_DIR / "actions.log"


def _build_never_touch():
    """Folders this app will refuse to delete from, no matter what."""
    home = Path.home()
    paths = [
        home / "Documents",
        home / "Desktop",
        home / "Pictures",
        home / "Videos",
        home / "Music",
        home / "OneDrive",
    ]
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    paths += [sysroot / "System32", sysroot / "SysWOW64", sysroot / "WinSxS", sysroot / "Boot"]
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value:
            paths.append(Path(value))
    return paths


NEVER_TOUCH = _build_never_touch()


@lru_cache(maxsize=None)
def _norm_dir(folder: str) -> str:
    """Resolve a folder once (following '..' and shortcuts) and remember it.
    Resolving hits the disk, so we only ever do it once per folder."""
    try:
        resolved = os.path.realpath(folder)
    except OSError:
        resolved = os.path.abspath(folder)
    return os.path.normcase(resolved)


@lru_cache(maxsize=None)
def _prefix(folder: str) -> str:
    """Folder as text with a trailing slash, so 'C:\\Temp' doesn't match 'C:\\Temp2'."""
    norm = _norm_dir(folder)
    return norm if norm.endswith(os.sep) else norm + os.sep


def is_within(child: Path, parent: Path) -> bool:
    """True if `child` is inside `parent` (after resolving shortcuts like '..')."""
    full = os.path.normcase(os.path.realpath(str(child)))
    return full.startswith(_prefix(str(parent)))


def is_safe_to_delete(path: Path, allowed_roots) -> bool:
    """
    A file is only deletable if ALL of these are true:
      1. it is not a symlink/shortcut (we never follow links out of a folder)
      2. it sits inside one of the category's own folders
      3. it is not the category folder itself
      4. it is not inside a NEVER_TOUCH folder

    Implementation note: we resolve only the file's parent folder (cached), glue the
    file name back on, then compare plain text. That still catches '..' tricks but is
    fast enough to check hundreds of thousands of files.
    """
    path_str = str(path)
    if os.path.islink(path_str):
        return False
    parent = _norm_dir(os.path.dirname(path_str) or ".")
    full = os.path.join(parent, os.path.normcase(os.path.basename(path_str)))

    inside_allowed = False
    for root in allowed_roots:
        root_prefix = _prefix(str(root))
        if full + os.sep == root_prefix:      # it IS the category folder itself
            return False
        if full.startswith(root_prefix):
            inside_allowed = True
            break
    if not inside_allowed:
        return False

    for protected in NEVER_TOUCH:
        if full.startswith(_prefix(str(protected))):
            return False
    return True


def log_action(category: str, mode: str, deleted: int, failed: int, bytes_freed: int):
    """Append one line to the action log so you can always see what the app did."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "mode": mode,
            "deleted": deleted,
            "failed": failed,
            "bytes_freed": bytes_freed,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # logging must never crash a cleanup


def log_event(action: str, detail: str):
    """Log a non-delete action (e.g. 'startup disabled') to the same action log."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, "detail": detail}
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
