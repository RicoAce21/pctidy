import ctypes
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import display


def screen(current, modes, name="Panel", primary=True):
    return {"name": name, "adapter": "GPU", "primary": primary, "current": current, "modes": modes}


class DisplayTests(unittest.TestCase):
    def test_windows_structures_have_the_documented_sizes(self):
        # Built with 2-byte characters like Windows. If these fail, calls to Windows would misread data.
        devmode = display._make_devmode(ctypes.c_uint16)
        device = display._make_display_device(ctypes.c_uint16)
        self.assertEqual(ctypes.sizeof(devmode), 220)
        self.assertEqual(ctypes.sizeof(device), 840)
        self.assertEqual(devmode.dmPelsWidth.offset, 172)
        self.assertEqual(devmode.dmDisplayFrequency.offset, 184)
        self.assertEqual(devmode.dmFormName.offset, 102)
        self.assertEqual(device.StateFlags.offset, 324)

    def test_fast_screen_stuck_at_60_is_flagged(self):
        modes = [(1920, 1080, 60), (1920, 1080, 144), (1280, 720, 60)]
        info = display.analyze([screen((1920, 1080, 60), modes)])[0]
        self.assertEqual(info.status, "low")
        self.assertEqual(info.best_hz, 144)
        self.assertIn("144 Hz", info.message)

    def test_screen_at_its_best_is_ok(self):
        modes = [(1920, 1080, 60), (1920, 1080, 144)]
        info = display.analyze([screen((1920, 1080, 144), modes)])[0]
        self.assertEqual(info.status, "ok")

    def test_rounding_difference_is_not_a_problem(self):
        modes = [(1920, 1080, 143), (1920, 1080, 144)]
        self.assertEqual(display.analyze([screen((1920, 1080, 143), modes)])[0].status, "ok")
        modes = [(1920, 1080, 59), (1920, 1080, 60)]
        self.assertEqual(display.analyze([screen((1920, 1080, 59), modes)])[0].status, "ok")

    def test_only_same_resolution_counts(self):
        # 4K tops out at 60 even though 1080p goes to 144: 60 at 4K is NOT a problem
        modes = [(3840, 2160, 60), (1920, 1080, 144)]
        info = display.analyze([screen((3840, 2160, 60), modes)])[0]
        self.assertEqual(info.status, "ok")
        self.assertIn("144 Hz", info.message)        # but we still mention the option

    def test_default_rate_reported_as_unknown(self):
        self.assertEqual(display.analyze([screen((1920, 1080, 0), [])])[0].status, "unknown")
        self.assertEqual(display.analyze([screen((1920, 1080, 1), [])])[0].status, "unknown")

    def test_two_screens(self):
        raw = [screen((1920, 1080, 60), [(1920, 1080, 165)], "Laptop"),
               screen((2560, 1440, 60), [(2560, 1440, 60)], "External", primary=False)]
        a, b = display.analyze(raw)
        self.assertEqual((a.status, b.status), ("low", "ok"))

    def test_not_windows_returns_nothing(self):
        if sys.platform != "win32":
            self.assertEqual(display.get_displays(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
