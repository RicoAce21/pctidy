import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import safety, startup


class _Handle:
    def __init__(self, fake, key):
        self.fake, self.key = fake, key

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeWinreg:
    """A tiny in-memory stand-in for Windows' registry so we can test without Windows."""
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_BINARY = 3

    def __init__(self):
        self.data = {}
        self.readonly_hives = set()

    def add(self, hive, path, name, value):
        self.data.setdefault((hive, path.lower()), {})[name] = value

    def OpenKey(self, hive, path, reserved=0, access=1):
        key = (hive, path.lower())
        if key not in self.data:
            raise FileNotFoundError(path)
        if access & self.KEY_SET_VALUE and hive in self.readonly_hives:
            raise PermissionError(path)
        return _Handle(self, key)

    def CreateKeyEx(self, hive, path, reserved=0, access=2):
        if hive in self.readonly_hives:
            raise PermissionError(path)
        key = (hive, path.lower())
        self.data.setdefault(key, {})
        return _Handle(self, key)

    def EnumValue(self, handle, index):
        items = list(self.data[handle.key].items())
        if index >= len(items):
            raise OSError("no more")
        name, value = items[index]
        return name, value, self.REG_SZ

    def QueryValueEx(self, handle, name):
        if name not in self.data[handle.key]:
            raise FileNotFoundError(name)
        return self.data[handle.key][name], self.REG_BINARY

    def SetValueEx(self, handle, name, reserved, typ, value):
        self.data[handle.key][name] = value


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        safety.APP_DIR = self.tmp / "app"
        safety.LOG_FILE = safety.APP_DIR / "actions.log"
        os.environ["APPDATA"] = str(self.tmp / "appdata")
        os.environ["ProgramData"] = str(self.tmp / "progdata")
        self.fake = FakeWinreg()
        self._real = startup.winreg
        startup.winreg = self.fake
        real_exe = self.tmp / "real.exe"
        real_exe.write_bytes(b"x")
        self.real_exe = str(real_exe)
        f = self.fake
        f.add("HKCU", startup.RUN_PATH, "Spotify", f'"{self.real_exe}" --autostart')
        f.add("HKCU", startup.RUN_PATH, "SecurityHealth", self.real_exe)
        f.add("HKCU", startup.RUN_PATH, "RandomTool", self.real_exe)
        f.add("HKCU", startup.RUN_PATH, "GhostApp", r'"C:\definitely\not\here.exe"')
        f.add("HKLM", startup.RUN_PATH, "VendorUpdater", self.real_exe)

    def tearDown(self):
        startup.winreg = self._real

    def _by_name(self):
        return {i.name: i for i in startup.list_startup_items()}

    def test_lists_everything_and_rates_it(self):
        items = self._by_name()
        self.assertEqual(set(items), {"Spotify", "SecurityHealth", "RandomTool", "GhostApp", "VendorUpdater"})
        self.assertEqual(items["Spotify"].rating, "Optional")
        self.assertEqual(items["SecurityHealth"].rating, "Keep")
        self.assertEqual(items["RandomTool"].rating, "Unknown")
        self.assertEqual(items["GhostApp"].rating, "Broken")

    def test_sorted_with_most_useful_to_disable_first(self):
        order = [i.rating for i in startup.list_startup_items()]
        self.assertEqual(order, sorted(order, key=lambda r: startup.RATING_ORDER[r]))

    def test_admin_flag_only_for_all_users_entries(self):
        items = self._by_name()
        self.assertFalse(items["Spotify"].needs_admin)
        self.assertTrue(items["VendorUpdater"].needs_admin)

    def test_disable_writes_flag_and_keeps_original_entry(self):
        item = self._by_name()["Spotify"]
        self.assertTrue(item.enabled)
        ok, msg = startup.set_enabled(item, False)
        self.assertTrue(ok, msg)
        flag = self.fake.data[("HKCU", (startup.APPROVED_BASE + r"\Run").lower())]["Spotify"]
        self.assertEqual(flag[0], 3)                     # 03 = disabled, same as Task Manager
        self.assertEqual(len(flag), 12)
        self.assertIn("Spotify", self.fake.data[("HKCU", startup.RUN_PATH.lower())])  # NOT deleted
        self.assertFalse(self._by_name()["Spotify"].enabled)

    def test_reenable_restores(self):
        item = self._by_name()["Spotify"]
        startup.set_enabled(item, False)
        item = self._by_name()["Spotify"]
        ok, _ = startup.set_enabled(item, True)
        self.assertTrue(ok)
        self.assertTrue(self._by_name()["Spotify"].enabled)

    def test_all_users_item_without_admin_gives_friendly_error(self):
        self.fake.readonly_hives.add("HKLM")
        item = self._by_name()["VendorUpdater"]
        ok, msg = startup.set_enabled(item, False)
        self.assertFalse(ok)
        self.assertIn("administrator", msg)
        self.assertTrue(item.enabled)    # unchanged

    def test_startup_folder_items_are_listed_and_toggleable(self):
        folder = Path(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        folder.mkdir(parents=True)
        (folder / "MyThing.lnk").write_bytes(b"x")
        (folder / "desktop.ini").write_bytes(b"x")
        items = self._by_name()
        self.assertIn("MyThing.lnk", items)
        self.assertNotIn("desktop.ini", items)
        ok, _ = startup.set_enabled(items["MyThing.lnk"], False)
        self.assertTrue(ok)
        self.assertFalse(self._by_name()["MyThing.lnk"].enabled)

    def test_realtek_helper_is_a_keeper_but_lookalikes_are_not(self):
        self.assertEqual(startup.classify("RtkAudUService", r'"C:\WINDOWS\System32\RtkAudUService64.exe" -background', False)[0], "Keep")
        self.assertEqual(startup.classify("RtkNGUI64", r"C:\x\RtkNGUI64.exe", False)[0], "Keep")
        self.assertEqual(startup.classify("StartKeyHelper", r"C:\x\helper.exe", False)[0], "Unknown")

    def test_extract_exe(self):
        self.assertEqual(startup.extract_exe(r'"C:\Program Files\A\a.exe" --x'), r"C:\Program Files\A\a.exe")
        self.assertEqual(startup.extract_exe(r"C:\Tools\b.exe /silent"), r"C:\Tools\b.exe")

    def test_actions_are_logged(self):
        startup.set_enabled(self._by_name()["Spotify"], False)
        self.assertIn("startup_disabled", safety.LOG_FILE.read_text())

    def test_not_windows_is_safe(self):
        startup.winreg = None
        self.assertEqual(startup.list_startup_items(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
