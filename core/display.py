"""
display.py - checks whether each screen is running at its best refresh rate.

This is READ-ONLY: it asks Windows what each screen is set to and what it can do.
It never changes a display setting (a wrong mode can blank a screen), it only points you
to the Windows page where you change it yourself.
"""
import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple


# ---- Windows structures. Sizes are written explicitly (not wintypes) so the layout is the same
# ---- everywhere and can be checked by a test: DEVMODEW must be 220 bytes, DISPLAY_DEVICEW 840.
def _make_devmode(wchar):
    u32, i32, u16, i16 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint16, ctypes.c_int16

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wchar * 32), ("dmSpecVersion", u16), ("dmDriverVersion", u16),
            ("dmSize", u16), ("dmDriverExtra", u16), ("dmFields", u32),
            ("dmPositionX", i32), ("dmPositionY", i32),
            ("dmDisplayOrientation", u32), ("dmDisplayFixedOutput", u32),
            ("dmColor", i16), ("dmDuplex", i16), ("dmYResolution", i16), ("dmTTOption", i16),
            ("dmCollate", i16), ("dmFormName", wchar * 32), ("dmLogPixels", u16),
            ("dmBitsPerPel", u32), ("dmPelsWidth", u32), ("dmPelsHeight", u32),
            ("dmDisplayFlags", u32), ("dmDisplayFrequency", u32), ("dmICMMethod", u32),
            ("dmICMIntent", u32), ("dmMediaType", u32), ("dmDitherType", u32),
            ("dmReserved1", u32), ("dmReserved2", u32), ("dmPanningWidth", u32),
            ("dmPanningHeight", u32),
        ]
    return DEVMODEW


def _make_display_device(wchar):
    u32 = ctypes.c_uint32

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", u32), ("DeviceName", wchar * 32), ("DeviceString", wchar * 128),
                    ("StateFlags", u32), ("DeviceID", wchar * 128), ("DeviceKey", wchar * 128)]
    return DISPLAY_DEVICEW


DEVMODEW = _make_devmode(ctypes.c_wchar)
DISPLAY_DEVICEW = _make_display_device(ctypes.c_wchar)

ATTACHED_TO_DESKTOP = 0x1
PRIMARY_DEVICE = 0x4
CURRENT_SETTINGS = 0xFFFFFFFF      # Windows' "-1": give me what is set right now


def _windows_displays() -> List[Dict]:
    """Ask Windows for every connected screen, its current mode and every mode it supports."""
    user32 = ctypes.windll.user32
    user32.EnumDisplayDevicesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                           ctypes.POINTER(DISPLAY_DEVICEW), ctypes.c_uint32]
    user32.EnumDisplayDevicesW.restype = ctypes.c_int
    user32.EnumDisplaySettingsW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(DEVMODEW)]
    user32.EnumDisplaySettingsW.restype = ctypes.c_int

    screens, index = [], 0
    while True:
        dev = DISPLAY_DEVICEW()
        dev.cb = ctypes.sizeof(dev)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(dev), 0):
            break
        index += 1
        if not dev.StateFlags & ATTACHED_TO_DESKTOP:
            continue
        name = dev.DeviceString
        monitor = DISPLAY_DEVICEW()
        monitor.cb = ctypes.sizeof(monitor)
        if user32.EnumDisplayDevicesW(dev.DeviceName, 0, ctypes.byref(monitor), 0) and monitor.DeviceString:
            name = monitor.DeviceString

        now = DEVMODEW()
        now.dmSize = ctypes.sizeof(now)
        if not user32.EnumDisplaySettingsW(dev.DeviceName, CURRENT_SETTINGS, ctypes.byref(now)):
            continue
        modes, n = set(), 0
        while n < 4000:
            mode = DEVMODEW()
            mode.dmSize = ctypes.sizeof(mode)
            if not user32.EnumDisplaySettingsW(dev.DeviceName, n, ctypes.byref(mode)):
                break
            modes.add((mode.dmPelsWidth, mode.dmPelsHeight, mode.dmDisplayFrequency))
            n += 1
        screens.append({
            "name": name, "adapter": dev.DeviceString, "primary": bool(dev.StateFlags & PRIMARY_DEVICE),
            "current": (now.dmPelsWidth, now.dmPelsHeight, now.dmDisplayFrequency),
            "modes": sorted(modes),
        })
    return screens


@dataclass
class DisplayInfo:
    name: str
    adapter: str
    primary: bool
    width: int
    height: int
    current_hz: int
    best_hz: int          # highest refresh rate at THIS resolution
    max_any_hz: int       # highest at any resolution
    status: str           # "ok" | "low" | "unknown"
    message: str


def analyze(raw: List[Dict]) -> List[DisplayInfo]:
    """Turn raw Windows data into plain advice. Pure logic, so it can be tested anywhere."""
    results = []
    for screen in raw:
        width, height, hz = screen["current"]
        modes = screen["modes"]
        if hz <= 1:     # Windows reports 0 or 1 to mean 'hardware default'
            results.append(DisplayInfo(screen["name"], screen["adapter"], screen["primary"], width, height,
                                       hz, hz, hz, "unknown",
                                       "Windows doesn't report a refresh rate for this screen."))
            continue
        same_res = [m[2] for m in modes if (m[0], m[1]) == (width, height)]
        best = max(same_res + [hz])
        max_any = max([m[2] for m in modes] + [hz])
        # 143 vs 144 or 59 vs 60 is rounding, not a problem. 60 vs 144 is.
        if hz < best - 5 and hz < best * 0.95:
            status = "low"
            message = (f"This screen can run at {best} Hz at its current resolution but is set to {hz} Hz. "
                       f"Switching to {best} Hz makes motion smoother and games feel more responsive.")
        else:
            status = "ok"
            message = f"Running at its best rate ({hz} Hz) for this resolution."
            if max_any > best * 1.05:
                message += f" Lower resolutions can go up to {max_any} Hz."
        results.append(DisplayInfo(screen["name"], screen["adapter"], screen["primary"], width, height,
                                   hz, best, max_any, status, message))
    return results


def get_displays() -> List[DisplayInfo]:
    if sys.platform != "win32":
        return []
    try:
        return analyze(_windows_displays())
    except Exception:       # never let a display quirk crash the app
        return []


def open_display_settings() -> bool:
    """Opens Windows' 'Advanced display' page, where the refresh rate is changed."""
    try:
        os.startfile("ms-settings:display-advanced")      # type: ignore[attr-defined]
        return True
    except Exception:
        return False
