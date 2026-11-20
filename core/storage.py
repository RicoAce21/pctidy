"""
storage.py - works out where your disk space went. READ-ONLY: it never deletes or changes anything.

It walks a folder once, adds up every sub-folder's size, and remembers the biggest files so the
window can let you drill down instantly without re-scanning.

Things it deliberately does NOT count (they'd give wrong numbers):
  - shortcuts / junction folders (they point at other places, and can loop forever)
  - online-only cloud files (OneDrive etc.), which show a big size but use almost no disk
"""
import heapq
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

MAX_CHILDREN = 60        # folders kept per level; the rest are lumped together ("smaller folders")
KEEP_BIGGEST = 2000      # how many of the largest files to remember overall
# Windows file flags: OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS = "lives in the cloud"
OFFLINE_FLAGS = 0x1000 | 0x40000 | 0x400000


class DirNode:
    __slots__ = ("name", "path", "size", "files_size", "file_count", "children",
                 "hidden_count", "hidden_size", "newest")

    def __init__(self, name: str, path: str):
        self.name, self.path = name, path
        self.size = 0              # everything inside, including sub-folders
        self.files_size = 0        # only files sitting directly in this folder
        self.file_count = 0
        self.children: List["DirNode"] = []
        self.hidden_count = 0
        self.hidden_size = 0
        self.newest = 0.0          # most recent change to anything inside


@dataclass
class BigFile:
    path: str
    size: int
    mtime: float


@dataclass
class ScanResult:
    root: DirNode
    big_files: List[BigFile] = field(default_factory=list)
    skipped: int = 0                 # folders/files Windows wouldn't let us read
    offline_skipped: int = 0         # cloud-only files ignored
    cancelled: bool = False
    seconds: float = 0.0


class _State:
    def __init__(self, cancel, progress):
        self.cancel = cancel
        self.progress = progress
        self.heap: list = []
        self.skipped = 0
        self.offline = 0
        self.files = 0
        self.bytes = 0


def _is_offline(attrs: int) -> bool:
    return bool(attrs & OFFLINE_FLAGS)


def _is_link(entry) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None:
            return bool(is_junction())
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & 0x400) and entry.is_dir(follow_symlinks=False)
    except OSError:
        return True


def _scan_dir(path: str, name: str, state: _State) -> DirNode:
    node = DirNode(name, path)
    try:
        iterator = os.scandir(path)
    except OSError:
        state.skipped += 1
        return node
    with iterator:
        for entry in iterator:
            if state.cancel.is_set():
                break
            try:
                if _is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    child = _scan_dir(entry.path, entry.name, state)
                    node.children.append(child)
                    node.size += child.size
                    node.file_count += child.file_count
                    node.newest = max(node.newest, child.newest)
                else:
                    st = entry.stat(follow_symlinks=False)
                    if _is_offline(getattr(st, "st_file_attributes", 0)):
                        state.offline += 1
                        continue
                    node.files_size += st.st_size
                    node.size += st.st_size
                    node.file_count += 1
                    node.newest = max(node.newest, st.st_mtime)
                    item = (st.st_size, entry.path, st.st_mtime)
                    if len(state.heap) < KEEP_BIGGEST:
                        heapq.heappush(state.heap, item)
                    elif st.st_size > state.heap[0][0]:
                        heapq.heapreplace(state.heap, item)
                    state.files += 1
                    state.bytes += st.st_size
                    if state.progress and state.files % 500 == 0:
                        state.progress(state.files, state.bytes, path)
            except OSError:
                state.skipped += 1
    node.children.sort(key=lambda c: c.size, reverse=True)
    if len(node.children) > MAX_CHILDREN:
        extra = node.children[MAX_CHILDREN:]
        node.hidden_count = len(extra)
        node.hidden_size = sum(c.size for c in extra)
        node.children = node.children[:MAX_CHILDREN]
    return node


def scan(root: str, cancel: Optional[threading.Event] = None,
         progress: Optional[Callable[[int, int, str], None]] = None) -> ScanResult:
    """Walk `root` once. `progress(files_seen, bytes_seen, current_folder)` is called now and then."""
    started = time.time()
    state = _State(cancel or threading.Event(), progress)
    root = os.path.abspath(root)
    name = os.path.basename(root.rstrip("\\/")) or root
    node = _scan_dir(root, name, state)
    biggest = [BigFile(p, s, m) for s, p, m in sorted(state.heap, reverse=True)]
    return ScanResult(node, biggest, state.skipped, state.offline, state.cancel.is_set(),
                      time.time() - started)


def largest_files_under(result: ScanResult, folder: str, limit: int = 15) -> List[BigFile]:
    """Biggest remembered files inside `folder` (matching whole folder names, so 'a' != 'ab')."""
    prefix = os.path.normcase(os.path.abspath(folder)).rstrip("\\/") + os.sep
    out = [f for f in result.big_files if os.path.normcase(f.path).startswith(prefix)]
    return out[:limit]


def age_label(mtime: float, now: Optional[float] = None) -> str:
    days = int(((now or time.time()) - mtime) / 86400)
    if days < 1:
        return "today"
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def reveal(path: str, is_file: bool) -> bool:
    """Show a file (selected) or folder in Windows Explorer. Opens it only - never changes it."""
    if sys.platform != "win32":
        return False
    try:
        if is_file:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(path)      # type: ignore[attr-defined]
        return True
    except Exception:
        return False
