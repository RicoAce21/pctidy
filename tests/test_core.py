import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import safety
from core.cleaner import clean_result, order_for_cleaning, has_trash_and_bin
from core.scanner import ScanResult
from core.scanner import Category, scan_category, format_size


def make_file(path: Path, size=100, age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "junk"
        self.root.mkdir()
        safety.APP_DIR = self.tmp / "app"          # keep the log out of real folders
        safety.LOG_FILE = safety.APP_DIR / "actions.log"

    def test_old_files_found_new_files_skipped(self):
        make_file(self.root / "old.tmp", 500, age_days=5)
        make_file(self.root / "new.tmp", 500, age_days=0)
        cat = Category("t", "t", "t", [self.root], min_age_days=1)
        r = scan_category(cat)
        self.assertEqual([f.path.name for f in r.files], ["old.tmp"])
        self.assertEqual(r.total_size, 500)

    def test_clean_deletes_only_scanned_files(self):
        make_file(self.root / "old.tmp", 500, age_days=5)
        make_file(self.root / "new.tmp", 500, age_days=0)
        cat = Category("t", "t", "t", [self.root], min_age_days=1)
        out = clean_result(scan_category(cat))
        self.assertEqual(out.deleted, 1)
        self.assertFalse((self.root / "old.tmp").exists())
        self.assertTrue((self.root / "new.tmp").exists())

    def test_extension_filter_and_non_recursive(self):
        make_file(self.root / "setup.exe", 10, age_days=40)
        make_file(self.root / "notes.txt", 10, age_days=40)
        make_file(self.root / "sub" / "deep.exe", 10, age_days=40)
        cat = Category("t", "t", "t", [self.root], min_age_days=30,
                       recursive=False, extensions=(".exe",))
        r = scan_category(cat)
        self.assertEqual([f.path.name for f in r.files], ["setup.exe"])

    def test_file_outside_root_is_refused(self):
        outside = self.tmp / "precious.txt"
        make_file(outside, 10, age_days=99)
        self.assertFalse(safety.is_safe_to_delete(outside, [self.root]))

    def test_dotdot_escape_is_refused(self):
        outside = self.tmp / "precious.txt"
        make_file(outside, 10, age_days=99)
        sneaky = self.root / ".." / "precious.txt"
        self.assertFalse(safety.is_safe_to_delete(sneaky, [self.root]))

    def test_never_touch_wins_over_allowed_root(self):
        protected = self.tmp / "Documents"
        make_file(protected / "thesis.docx", 10, age_days=99)
        safety.NEVER_TOUCH.append(protected)
        try:
            self.assertFalse(safety.is_safe_to_delete(protected / "thesis.docx", [protected]))
        finally:
            safety.NEVER_TOUCH.remove(protected)

    def test_symlink_is_refused(self):
        target = self.tmp / "precious.txt"
        make_file(target, 10, age_days=99)
        link = self.root / "link.tmp"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self.assertFalse(safety.is_safe_to_delete(link, [self.root]))

    def test_root_folder_itself_is_refused(self):
        self.assertFalse(safety.is_safe_to_delete(self.root, [self.root]))

    def test_action_log_written(self):
        make_file(self.root / "old.tmp", 5, age_days=5)
        cat = Category("t", "t", "t", [self.root], min_age_days=1)
        clean_result(scan_category(cat))
        self.assertTrue(safety.LOG_FILE.exists())

    def test_recycle_bin_is_always_cleaned_first(self):
        installers = ScanResult(Category("old_installers", "i", "i", [self.root], mode="trash"))
        temp = ScanResult(Category("temp", "t", "t", [self.root]))
        bin_ = ScanResult(Category("recycle_bin", "r", "r", [], kind="recycle_bin"))
        ordered = order_for_cleaning([installers, temp, bin_])
        self.assertEqual(ordered[0].category.id, "recycle_bin")
        self.assertEqual([r.category.id for r in ordered[1:]], ["old_installers", "temp"])

    def test_warns_when_trash_and_bin_are_mixed(self):
        installers = ScanResult(Category("old_installers", "i", "i", [self.root], mode="trash"))
        bin_ = ScanResult(Category("recycle_bin", "r", "r", [], kind="recycle_bin"))
        temp = ScanResult(Category("temp", "t", "t", [self.root]))
        self.assertTrue(has_trash_and_bin([installers, bin_]))
        self.assertFalse(has_trash_and_bin([temp, bin_]))
        self.assertFalse(has_trash_and_bin([installers, temp]))

    def test_format_size(self):
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
