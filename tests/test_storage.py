import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import scanner, storage


def write(path: Path, size: int, age_days: float = 0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        write(self.root / "a" / "f1", 1000)
        write(self.root / "a" / "f2", 2000)
        write(self.root / "b" / "c" / "f3", 5000)
        write(self.root / "b" / "d" / "f4", 300)
        write(self.root / "big.bin", 10000, age_days=800)
        (self.root / "empty").mkdir()

    def test_sizes_add_up_through_the_tree(self):
        r = storage.scan(str(self.root))
        self.assertEqual(r.root.size, 18300)
        self.assertEqual(r.root.files_size, 10000)
        names = [c.name for c in r.root.children]
        self.assertEqual(names, ["b", "a", "empty"])             # biggest first
        b = r.root.children[0]
        self.assertEqual((b.size, [c.name for c in b.children]), (5300, ["c", "d"]))

    def test_biggest_files_sorted_largest_first(self):
        r = storage.scan(str(self.root))
        sizes = [f.size for f in r.big_files]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 10000)
        self.assertEqual(len(sizes), 5)

    def test_shortcut_loops_are_ignored(self):
        try:
            (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        r = storage.scan(str(self.root))
        self.assertEqual(r.root.size, 18300)        # not doubled, and it finished

    def test_many_folders_are_lumped_but_totals_stay_right(self):
        for i in range(100):
            write(self.root / "many" / f"d{i:03}" / "f", 10 + i)
        r = storage.scan(str(self.root))
        many = next(c for c in r.root.children if c.name == "many")
        self.assertEqual(len(many.children), storage.MAX_CHILDREN)
        self.assertEqual(many.hidden_count, 40)
        self.assertEqual(many.size, sum(10 + i for i in range(100)))
        self.assertEqual(many.hidden_size + sum(c.size for c in many.children), many.size)

    def test_cancel_stops_early(self):
        cancel = threading.Event()
        cancel.set()
        r = storage.scan(str(self.root), cancel=cancel)
        self.assertTrue(r.cancelled)
        self.assertLess(r.root.size, 18300)

    def test_progress_is_reported(self):
        for i in range(1200):
            write(self.root / "lots" / f"f{i}", 1)
        calls = []
        storage.scan(str(self.root), progress=lambda f, b, p: calls.append(f))
        self.assertTrue(calls)
        self.assertEqual(calls, sorted(calls))

    def test_largest_files_under_respects_folder_boundaries(self):
        write(self.root / "ab" / "other", 99999)
        r = storage.scan(str(self.root))
        under_a = storage.largest_files_under(r, str(self.root / "a"))
        self.assertEqual([os.path.basename(f.path) for f in under_a], ["f2", "f1"])   # no 'ab' leakage

    def test_cloud_only_files_flag_detection(self):
        self.assertTrue(storage._is_offline(0x400000))
        self.assertTrue(storage._is_offline(0x1000 | 0x20))
        self.assertFalse(storage._is_offline(0x20))

    def test_unreadable_folder_is_counted_not_fatal(self):
        r = storage.scan(str(self.root / "does-not-exist"))
        self.assertEqual(r.root.size, 0)
        self.assertEqual(r.skipped, 1)

    def test_age_labels(self):
        now = 1_000_000_000
        day = 86400
        self.assertEqual(storage.age_label(now - 5, now), "today")
        self.assertEqual(storage.age_label(now - 3 * day, now), "3 days ago")
        self.assertEqual(storage.age_label(now - 90 * day, now), "3 months ago")
        self.assertEqual(storage.age_label(now - 800 * day, now), "2 years ago")


class ShaderCacheCategoryTests(unittest.TestCase):
    def setUp(self):
        self.local = Path(tempfile.mkdtemp())
        self._env = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "ProgramData")}
        os.environ["LOCALAPPDATA"] = str(self.local)
        os.environ["ProgramData"] = str(self.local / "pd")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _cat(self):
        return {c.id: c for c in scanner.get_categories()}["shader_caches"]

    def test_finds_directx_nvidia_amd_intel_caches_and_nothing_else(self):
        write(self.local / "D3DSCache" / "a.bin", 100)
        write(self.local / "NVIDIA" / "DXCache" / "b.bin", 200)
        write(self.local / "NVIDIA" / "GLCache" / "c.bin", 300)
        write(self.local / "AMD" / "DxCache" / "d.bin", 400)
        write(self.local / "Intel" / "ShaderCache" / "e.bin", 500)
        write(self.local / "pd" / "NVIDIA Corporation" / "NV_Cache" / "f.bin", 600)
        write(self.local / "NVIDIA" / "settings.cfg", 999)           # NOT a cache: must be left alone
        write(self.local / "AMD" / "profile.xml", 999)
        r = scanner.scan_category(self._cat())
        self.assertEqual(sorted(f.path.name for f in r.files), ["a.bin", "b.bin", "c.bin", "d.bin", "e.bin", "f.bin"])
        self.assertEqual(r.total_size, 2100)

    def test_recent_files_are_included_and_deleted_permanently(self):
        cat = self._cat()
        self.assertEqual(cat.min_age_days, 0)
        self.assertEqual(cat.mode, "permanent")

    def test_recycle_bin_is_still_last(self):
        self.assertEqual(scanner.get_categories()[-1].id, "recycle_bin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
