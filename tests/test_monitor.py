import http.server
import json
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import monitor

SAMPLE_LHM = {
    "Text": "Sensor", "Children": [{
        "Text": "MY-LAPTOP", "Children": [
            {"Text": "Intel Core i7-1165G7", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"Text": "CPU Package", "Value": "71.0 °C", "Children": []},
                    {"Text": "Core #1", "Value": "68,5 °C", "Children": []},
                ]},
                {"Text": "Load", "Children": [{"Text": "CPU Total", "Value": "23.1 %", "Children": []}]},
                {"Text": "Clocks", "Children": [{"Text": "Core #1", "Value": "3600 MHz", "Children": []}]},
            ]},
            {"Text": "NVIDIA GeForce MX450", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"Text": "GPU Core", "Value": "55.0 \u00c2\u00b0C", "Children": []},   # garbled degree sign
                ]},
                {"Text": "Fans", "Children": [{"Text": "GPU Fan", "Value": "1800 RPM", "Children": []}]},
            ]},
        ]}]}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        # Several tests swap these functions for stubs. Put the real ones back afterwards so tests can't affect each other.
        for name in ("_read_lhm", "_read_wmi", "_lhm_status", "LHM_URL"):
            self.addCleanup(setattr, monitor, name, getattr(monitor, name))

    def test_parse_lhm_finds_temps_and_fans_only(self):
        temps, fans = monitor.parse_lhm(SAMPLE_LHM)
        names = {(t["hardware"], t["name"]): t["celsius"] for t in temps}
        self.assertEqual(names[("Intel Core i7-1165G7", "CPU Package")], 71.0)
        self.assertEqual(names[("Intel Core i7-1165G7", "Core #1")], 68.5)     # comma decimal
        self.assertEqual(names[("NVIDIA GeForce MX450", "GPU Core")], 55.0)    # garbled ° sign
        self.assertEqual(len(temps), 3)                                          # load/clock ignored
        self.assertEqual(fans[0]["rpm"], 1800.0)

    def test_headline_prefers_cpu(self):
        temps, _ = monitor.parse_lhm(SAMPLE_LHM)
        self.assertEqual(monitor.headline_temp(temps), 71.0)
        self.assertIsNone(monitor.headline_temp([]))

    def test_junk_temperature_ignored(self):
        data = {"Children": [{"Children": [{"Text": "X", "Children": [
            {"Text": "bad", "Value": "0.0 °C"}, {"Text": "bad2", "Value": "999 °C"}]}]}]}
        self.assertEqual(monitor.parse_lhm(data)[0], [])

    def test_no_source_gives_helpful_hint(self):
        monitor._read_lhm = lambda timeout=0.6: None
        monitor._read_wmi = lambda: None
        result = monitor.get_temperatures()
        self.assertIsNone(result["source"])
        self.assertIn("LibreHardwareMonitor", result["hint"])

    def test_hint_explains_why_there_are_no_temperatures(self):
        monitor._read_lhm = lambda timeout=0.6: None
        monitor._read_wmi = lambda: None
        for status, words in (("unreachable", "Remote Web Server"), ("running_no_sensors", "administrator"),
                              ("bad_response", "port 8085")):
            monitor._lhm_status = lambda timeout=0.6, s=status: s
            result = monitor.get_temperatures()
            self.assertEqual(result["status"], status)
            self.assertIn(words, result["hint"])

    def test_connected_but_empty_counts_as_running_without_sensors(self):
        monitor._read_lhm = lambda timeout=0.6: ([], [])
        monitor._read_wmi = lambda: None
        monitor._lhm_status = lambda timeout=0.6: "running_no_sensors"
        self.assertEqual(monitor.get_temperatures()["status"], "running_no_sensors")

    def test_working_source_reports_ok(self):
        monitor._read_lhm = lambda timeout=0.6: ([{"hardware": "CPU", "name": "CPU Package", "celsius": 60.0}], [])
        result = monitor.get_temperatures()
        self.assertEqual((result["status"], result["source"], result["hint"]), ("ok", "LibreHardwareMonitor", None))

    def _serve(self, status=200, body=b""):
        """A real little web server on a free port, standing in for LibreHardwareMonitor."""
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self_inner):
                self_inner.send_response(status)
                self_inner.end_headers()
                self_inner.wfile.write(body)

            def log_message(self_inner, *a):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_port}/data.json"

    def test_status_against_a_real_server_in_every_situation(self):
        original = monitor.LHM_URL
        self.addCleanup(setattr, monitor, "LHM_URL", original)
        # 1. nothing listening on the port at all
        monitor.LHM_URL = "http://127.0.0.1:1/data.json"
        self.assertEqual(monitor._lhm_status(), "unreachable")
        # 2. running, but no temperatures in the data (typical when not started as administrator)
        monitor.LHM_URL = self._serve(200, json.dumps({"Children": [{"Children": [{"Text": "CPU", "Children": []}]}]}).encode())
        self.assertEqual(monitor._lhm_status(), "running_no_sensors")
        # 3. a different program answering with junk
        monitor.LHM_URL = self._serve(200, b"<html>hello</html>")
        self.assertEqual(monitor._lhm_status(), "bad_response")
        # 4. a different program answering 404
        monitor.LHM_URL = self._serve(404, b"nope")
        self.assertEqual(monitor._lhm_status(), "bad_response")
        # 5. healthy
        monitor.LHM_URL = self._serve(200, json.dumps(SAMPLE_LHM).encode())
        self.assertEqual(monitor._lhm_status(), "ok")
        self.assertEqual(monitor._read_lhm()[0][0]["celsius"], 71.0)

    def test_protected_processes(self):
        self.assertTrue(monitor.is_protected("lsass.exe", 700))
        self.assertTrue(monitor.is_protected("Explorer.EXE", 5000))
        self.assertTrue(monitor.is_protected("anything", 4))
        self.assertTrue(monitor.is_protected("python.exe", os.getpid()))
        self.assertFalse(monitor.is_protected("spotify.exe", 99999))

    def test_cannot_end_itself(self):
        ok, msg = monitor.end_process(os.getpid())
        self.assertFalse(ok)

    def test_snapshot_has_expected_fields(self):
        snap = monitor.snapshot()
        for key in ("cpu_percent", "ram_percent", "ram_total", "disk"):
            self.assertIn(key, snap)
        self.assertGreater(snap["ram_total"], 0)

    def test_grouping_sums_and_locks_if_any_member_is_protected(self):
        rows = [
            {"pid": 10, "name": "chrome.exe", "cpu": 1.0, "mem_mb": 300.0, "protected": False, "count": 1, "pids": [10]},
            {"pid": 11, "name": "Chrome.exe", "cpu": 2.5, "mem_mb": 200.0, "protected": False, "count": 1, "pids": [11]},
            {"pid": 12, "name": "svchost.exe", "cpu": 0.5, "mem_mb": 50.0, "protected": True, "count": 1, "pids": [12]},
            {"pid": 13, "name": "svchost.exe", "cpu": 0.5, "mem_mb": 25.0, "protected": True, "count": 1, "pids": [13]},
        ]
        by = {g["name"].lower(): g for g in monitor.group_by_app(rows)}
        self.assertEqual(len(by), 2)
        self.assertEqual((by["chrome.exe"]["count"], by["chrome.exe"]["cpu"], by["chrome.exe"]["mem_mb"]), (2, 3.5, 500.0))
        self.assertEqual(by["chrome.exe"]["pids"], [10, 11])
        self.assertFalse(by["chrome.exe"]["protected"])
        self.assertTrue(by["svchost.exe"]["protected"])

    def test_grouped_top_processes_have_unique_names(self):
        monitor.top_processes()
        names = [r["name"].lower() for r in monitor.top_processes(limit=500)]
        self.assertEqual(len(names), len(set(names)))

    def test_end_processes_refuses_protected_and_reports(self):
        ok, msg = monitor.end_processes([os.getpid()])
        self.assertFalse(ok)
        self.assertIn("Closed 0 of 1", msg)

    def test_top_processes_sorted_and_limited(self):
        monitor.top_processes()                     # prime
        rows = monitor.top_processes(limit=5, sort_by="memory")
        self.assertLessEqual(len(rows), 5)
        mems = [r["mem_mb"] for r in rows]
        self.assertEqual(mems, sorted(mems, reverse=True))
        self.assertTrue(all("pid" in r and "name" in r for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
