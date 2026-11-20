"""
recycle.py - size / empty the Windows Recycle Bin.
Uses Windows' own functions via ctypes. Does nothing on other systems.
"""
import ctypes
import sys
from ctypes import wintypes


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def query():
    """Returns (size_in_bytes, number_of_items) of the Recycle Bin."""
    if sys.platform != "win32":
        return 0, 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    result = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    if result != 0:
        return 0, 0
    return info.i64Size, info.i64NumItems


def empty():
    """Empties the Recycle Bin without Windows' extra popups."""
    if sys.platform != "win32":
        return False
    # 1 = no confirmation, 2 = no progress window, 4 = no sound
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 1 | 2 | 4)
    return result == 0
