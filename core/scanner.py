"""
scanner.py - finds junk files. It only LOOKS. It never deletes anything.

Each "category" is a kind of junk (temp files, browser cache, ...).
A category says: which folders to look in, how old a file must be,
and how it should be deleted later.
"""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import recycle
from .safety import is_safe_to_delete


@dataclass
class Category:
    id: str
    name: str
    description: str
    roots: List[Path]
    mode: str = "permanent"          # "permanent" or "trash" (Recycle Bin)
    min_age_days: float = 1.0        # skip files newer than this (they may be in use)
    recursive: bool = True           # look inside sub-folders too?
    extensions: Optional[Tuple[str, ...]] = None   # e.g. (".exe", ".msi")
    kind: str = "files"              # "files" or "recycle_bin"


@dataclass
class FoundFile:
    path: Path
    size: int


@dataclass
class ScanResult:
    category: Category
    files: List[FoundFile] = field(default_factory=list)
    total_size: int = 0
    item_count: int = 0


def format_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _iter_files(root: Path, recursive: bool):
    """Yield every file path under root. Never follows symlinks."""
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                yield Path(dirpath) / name
    else:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
        except OSError:
            return


def scan_category(category: Category) -> ScanResult:
    result = ScanResult(category=category)

    if category.kind == "recycle_bin":
        size, count = recycle.query()
        result.total_size = size
        result.item_count = count
        return result

    cutoff = time.time() - category.min_age_days * 86400
    for root in category.roots:
        if not Path(root).exists():
            continue
        for path in _iter_files(Path(root), category.recursive):
            try:
                if category.extensions and path.suffix.lower() not in category.extensions:
                    continue
                st = os.lstat(path)
                if st.st_mtime > cutoff:
                    continue
                if not is_safe_to_delete(path, category.roots):
                    continue
                result.files.append(FoundFile(path=path, size=st.st_size))
                result.total_size += st.st_size
            except OSError:
                continue  # locked or vanished file: just skip it
    result.item_count = len(result.files)
    return result


def get_categories() -> List[Category]:
    """The built-in list of junk categories for Windows."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    temp = Path(os.environ.get("TEMP", local / "Temp"))
    downloads = Path.home() / "Downloads"

    # Chromium browsers keep one cache set per profile (Default, Profile 1, ...)
    browser_cache_roots = []
    for browser_dir in (
        local / "Google" / "Chrome" / "User Data",
        local / "Microsoft" / "Edge" / "User Data",
        local / "BraveSoftware" / "Brave-Browser" / "User Data",
    ):
        if browser_dir.exists():
            for profile in list(browser_dir.glob("Default")) + list(browser_dir.glob("Profile *")):
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    browser_cache_roots.append(profile / sub)
    firefox_profiles = local / "Mozilla" / "Firefox" / "Profiles"
    if firefox_profiles.exists():
        browser_cache_roots += list(firefox_profiles.glob("*/cache2"))

    # Graphics shader caches: compiled GPU code that Windows, drivers and games rebuild on their own.
    shader_roots = [
        local / "D3DSCache", local / "Microsoft" / "DirectX Shader Cache",
        local / "NVIDIA" / "DXCache", local / "NVIDIA" / "GLCache", local / "NVIDIA Corporation" / "NV_Cache",
        local / "AMD" / "DxCache", local / "AMD" / "GLCache", local / "AMD" / "VkCache",
        local / "AMD" / "DxcCache", local / "Intel" / "ShaderCache",
    ]
    program_data = os.environ.get("ProgramData")
    if program_data:
        shader_roots.append(Path(program_data) / "NVIDIA Corporation" / "NV_Cache")

    return [
        Category("user_temp", "User temp files",
                 "Temporary files apps left behind. Only files older than 1 day.",
                 [temp], min_age_days=1),
        Category("windows_temp", "Windows temp files",
                 "System temp folder. Some files need admin rights and will be skipped without it.",
                 [windir / "Temp"], min_age_days=1),
        Category("browser_cache", "Browser caches",
                 "Chrome / Edge / Brave / Firefox cache. Sites reload a bit slower once, then it rebuilds.",
                 browser_cache_roots, min_age_days=0),
        Category("crash_dumps", "Crash dumps",
                 "Error reports from crashed programs. Only useful for debugging.",
                 [local / "CrashDumps", windir / "Minidump"], min_age_days=0),
        Category("shader_caches", "Game shader caches",
                 "DirectX / NVIDIA / AMD / Intel graphics caches. They rebuild themselves, but games may "
                 "stutter briefly on the first launch afterwards. Best used when a game started stuttering "
                 "after an update. Close your games first.",
                 shader_roots, min_age_days=0),
        Category("windows_update", "Windows Update leftovers",
                 "Downloaded update files that are already installed. Needs admin rights.",
                 [windir / "SoftwareDistribution" / "Download"], min_age_days=7),
        Category("old_installers", "Old installers in Downloads",
                 "Setup files (.exe/.msi) older than 30 days. Sent to Recycle Bin so you can undo.",
                 [downloads], mode="trash", min_age_days=30, recursive=False,
                 extensions=(".exe", ".msi", ".msix")),
        Category("recycle_bin", "Recycle Bin",
                 "Files you already deleted. Emptying this is permanent.",
                 [], kind="recycle_bin"),
    ]
