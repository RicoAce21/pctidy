"""
cleaner.py - actually deletes files. Only call this AFTER the user confirmed.
"""
import os
from dataclasses import dataclass

from . import recycle
from .safety import is_safe_to_delete, log_action
from .scanner import ScanResult

try:
    from send2trash import send2trash
except ImportError:          # lets the core run even if the package is missing
    send2trash = None


@dataclass
class CleanResult:
    deleted: int = 0
    failed: int = 0
    bytes_freed: int = 0


def clean_result(result: ScanResult) -> CleanResult:
    category = result.category
    out = CleanResult()

    if category.kind == "recycle_bin":
        before = result.total_size
        if recycle.empty():
            out.deleted = result.item_count
            out.bytes_freed = before
        else:
            out.failed = result.item_count
        log_action(category.id, "empty", out.deleted, out.failed, out.bytes_freed)
        return out

    for found in result.files:
        # Re-check safety at the last moment, not just at scan time
        if not is_safe_to_delete(found.path, category.roots):
            out.failed += 1
            continue
        try:
            if category.mode == "trash":
                if send2trash is None:
                    raise RuntimeError("send2trash not installed")
                send2trash(str(found.path))
            else:
                os.remove(found.path)
            out.deleted += 1
            out.bytes_freed += found.size
        except Exception:
            out.failed += 1   # file in use, no permission, etc. - just move on

    log_action(category.id, category.mode, out.deleted, out.failed, out.bytes_freed)
    return out


def order_for_cleaning(results):
    """
    Put the Recycle Bin first.

    Why: some categories (old installers) are sent TO the Recycle Bin so you can undo them.
    If the bin were emptied after that, those files would be wiped too. Emptying it first
    means anything sent there during this run stays there.
    """
    return sorted(results, key=lambda r: r.category.kind != "recycle_bin")


def has_trash_and_bin(results) -> bool:
    """True if the selection mixes 'send to Recycle Bin' items with 'empty the Recycle Bin'."""
    has_bin = any(r.category.kind == "recycle_bin" for r in results)
    has_trash = any(r.category.kind == "files" and r.category.mode == "trash" for r in results)
    return has_bin and has_trash
