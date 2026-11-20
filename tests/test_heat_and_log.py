import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import heat, monitor, safety, session_log
from core.session_log import Recorder, Session, compare, summarize


def row(name, cpu):
    return {"pid": 1, "name": name, "cpu": cpu, "mem_mb": 1.0, "protected": False, "count": 1, "pids": [1]}


class HeatTests(unittest.TestCase):
    def test_shares_add_up_to_100_with_others(self):
        t = heat.HeatTracker(window=5)
        t.add([row("chrome.exe", 20), row("game.exe", 10), row("a.exe", 4), row("b.exe", 3), row("c.exe", 2),
               row("d.exe", 1), row("e.exe", 0.5)])
        s = t.shares(top=3)
        self.assertEqual([e.name for e in s.entries], ["chrome.exe", "game.exe", "a.exe"])
        self.assertAlmostEqual(sum(e.share for e in s.entries) + s.others_share, 100.0)
        self.assertAlmostEqual(s.entries[0].share, 100 * 20 / 40.5)
        self.assertTrue(s.busy)

    def test_rolling_window_forgets_old_readings(self):
        t = heat.HeatTracker(window=3)
        for _ in range(3):
            t.add([row("old.exe", 50)])
        for _ in range(3):
            t.add([row("new.exe", 50)])
        s = t.shares()
        self.assertEqual([e.name for e in s.entries], ["new.exe"])
        self.assertEqual(s.samples, 3)

    def test_apps_missing_from_some_readings_count_as_zero(self):
        t = heat.HeatTracker(window=4)
        t.add([row("burst.exe", 40)])
        for _ in range(3):
            t.add([row("steady.exe", 10)])
        s = {e.name: e for e in t.shares().entries}
        self.assertAlmostEqual(s["burst.exe"].avg_cpu, 10.0)      # 40 over 4 readings
        self.assertAlmostEqual(s["steady.exe"].avg_cpu, 7.5)

    def test_idle_machine_is_reported_as_not_busy(self):
        t = heat.HeatTracker()
        t.add([row("a.exe", 1), row("b.exe", 2)])
        self.assertFalse(t.shares().busy)

    def test_empty_and_zero_cpu(self):
        t = heat.HeatTracker()
        self.assertEqual(t.shares().entries, [])
        t.add([row("idle.exe", 0)])
        self.assertEqual(t.shares().entries, [])

    def test_names_are_merged_ignoring_case(self):
        t = heat.HeatTracker()
        t.add([row("Chrome.exe", 5), row("chrome.EXE", 5)])
        self.assertEqual(len(t.shares().entries), 1)


class MonitorSharingTests(unittest.TestCase):
    def test_two_quick_callers_get_the_same_reading_and_cannot_alter_it(self):
        monitor._cache.clear()
        a = monitor.top_processes(limit=3, sort_by="memory", group=False)
        b = monitor.top_processes(limit=3, sort_by="memory", group=False)
        self.assertEqual([r["pid"] for r in a], [r["pid"] for r in b])
        a[0]["pids"].append(-1)
        a[0]["name"] = "tampered"
        c = monitor.top_processes(limit=3, sort_by="memory", group=False)
        self.assertNotEqual(c[0]["name"], "tampered")
        self.assertNotIn(-1, c[0]["pids"])


def sample(temp, mhz=3000.0, cpu=50.0, apps=None):
    return {"t": time.time(), "cpu": cpu, "mhz": mhz, "ram": 60.0, "temp": temp, "apps": apps or {"game.exe": cpu}}


def session(temps, mhz=3000.0, cpu=50.0, name="S", plan="Balanced", interval=2.0):
    return Session(uuid_ := name + str(len(temps)), name, 1000.0, interval, plan,
                   [sample(t, mhz, cpu) for t in temps], 2000.0)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_records_samples_and_saves_on_stop(self):
        counter = iter(range(1000))
        rec = Recorder("Before", sampler=lambda: sample(60 + next(counter)), interval=0.01, directory=self.tmp)
        rec.start()
        deadline = time.time() + 5
        while len(rec.session.samples) < 5 and time.time() < deadline:
            time.sleep(0.01)
        done = rec.stop()
        self.assertGreaterEqual(len(done.samples), 5)
        self.assertFalse(rec.running)
        loaded = session_log.list_sessions(self.tmp)
        self.assertEqual(len(loaded), 1)
        self.assertEqual((loaded[0].name, len(loaded[0].samples), loaded[0].partial), ("Before", len(done.samples), False))

    def test_a_failing_sensor_reading_does_not_end_the_recording(self):
        calls = {"n": 0}
        def flaky():
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise RuntimeError("sensor hiccup")
            return sample(70)
        rec = Recorder("x", sampler=flaky, interval=0.01, directory=self.tmp)
        rec.start()
        deadline = time.time() + 5
        while len(rec.session.samples) < 3 and time.time() < deadline:
            time.sleep(0.01)
        rec.stop()
        self.assertGreaterEqual(len(rec.session.samples), 3)
        self.assertGreater(rec.errors, 0)

    def test_autosaves_while_recording(self):
        old = session_log.AUTOSAVE_EVERY
        session_log.AUTOSAVE_EVERY = 3
        self.addCleanup(setattr, session_log, "AUTOSAVE_EVERY", old)
        rec = Recorder("long", sampler=lambda: sample(65), interval=0.01, directory=self.tmp)
        rec.start()
        deadline = time.time() + 5
        while not list(self.tmp.glob("*.json")) and time.time() < deadline:
            time.sleep(0.01)
        saved = session_log.list_sessions(self.tmp)
        rec.stop()
        self.assertTrue(saved and saved[0].partial)                    # a crash mid-recording would keep this

    def test_stop_is_prompt_even_with_a_long_interval(self):
        rec = Recorder("slow", sampler=lambda: sample(60), interval=30, directory=self.tmp)
        rec.start()
        time.sleep(0.1)
        t0 = time.time()
        rec.stop()
        self.assertLess(time.time() - t0, 3)

    def test_blank_name_gets_a_default(self):
        self.assertEqual(Recorder("   ", sampler=lambda: {}, directory=self.tmp).session.name, "Untitled session")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_round_trip_list_order_and_delete(self):
        a, b = session([60, 61]), session([70, 71, 72])
        a.id, b.id, a.started, b.started = "aaa", "bbb", 100.0, 200.0
        session_log.save(a, self.tmp)
        session_log.save(b, self.tmp)
        listed = session_log.list_sessions(self.tmp)
        self.assertEqual([s.id for s in listed], ["bbb", "aaa"])                 # newest first
        self.assertEqual(listed[0].samples[2]["temp"], 72)
        self.assertTrue(session_log.delete("aaa", self.tmp))
        self.assertEqual([s.id for s in session_log.list_sessions(self.tmp)], ["bbb"])
        self.assertFalse(session_log.delete("aaa", self.tmp))

    def test_corrupt_file_is_skipped_not_fatal(self):
        (self.tmp / "bad.json").write_text("{not json")
        session_log.save(session([60]), self.tmp)
        self.assertEqual(len(session_log.list_sessions(self.tmp)), 1)

    def test_missing_folder_is_fine(self):
        self.assertEqual(session_log.list_sessions(self.tmp / "nope"), [])


class SummaryAndCompareTests(unittest.TestCase):
    def test_summary_numbers(self):
        s = summarize(session([80, 90, 88, 70]))
        self.assertAlmostEqual(s.temp_avg, 82.0)
        self.assertEqual(s.temp_peak, 90)
        self.assertEqual(s.seconds_hot, 2 * 2.0)            # 90 and 88 are above 85, 2s each
        self.assertEqual(s.samples, 4)
        self.assertEqual(s.top_apps[0][0], "game.exe")

    def test_summary_without_temperatures(self):
        s = summarize(session([None, None]))
        self.assertIsNone(s.temp_avg)
        self.assertIsNone(s.temp_peak)

    def test_good_trade_is_described_plainly(self):
        before = summarize(session([88] * 40, mhz=3200))
        after = summarize(session([78] * 40, mhz=3150))
        c = compare(before, after)
        text = " ".join(c.sentences)
        self.assertIn("Cooler", text)
        self.assertIn("good trade", text)
        rows = {r.metric: r for r in c.rows}
        self.assertEqual(rows["Average temperature"].verdict, "better")
        self.assertEqual(rows["Peak temperature"].change, "-10 °C")
        self.assertEqual(rows["Average CPU speed"].verdict, "same")
        self.assertEqual(c.warnings, [])

    def test_big_speed_loss_is_called_a_cost(self):
        c = compare(summarize(session([85] * 40, mhz=3500)), summarize(session([70] * 40, mhz=2500)))
        text = " ".join(c.sentences)
        self.assertIn("slower", text)
        self.assertIn("real cost", text)
        self.assertNotIn("good trade", text)

    def test_hotter_is_reported(self):
        c = compare(summarize(session([70] * 40)), summarize(session([85] * 40)))
        self.assertIn("Hotter", " ".join(c.sentences))

    def test_unfair_comparisons_get_warnings(self):
        c = compare(summarize(session([80] * 100, cpu=30)), summarize(session([80] * 20, cpu=80)))
        joined = " ".join(c.warnings)
        self.assertIn("different lengths", joined)
        self.assertIn("different amounts of work", joined)

    def test_missing_temperature_is_flagged_not_crashed(self):
        c = compare(summarize(session([None] * 40)), summarize(session([70] * 40)))
        self.assertTrue(any("no temperature" in w for w in c.warnings))

    def test_very_short_recordings_are_flagged(self):
        c = compare(summarize(session([70] * 5)), summarize(session([70] * 5)))
        self.assertTrue(any("under about 30 seconds" in w for w in c.warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
