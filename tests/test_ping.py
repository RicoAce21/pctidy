import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ping
from core.ping import Adapter, Environment, PingResult, Target


class FakeConn:
    def close(self):
        pass


def result(name="X", kind="reference", avg=30.0, jitter=5.0, loss=0.0):
    """A ready-made result, so advice rules can be tested without any network."""
    target = Target(name.lower(), name, kind, "example.invalid")
    if avg is None:
        stats = ping.summarize([None, None])
    else:
        stats = ping.PingStats(10, int(10 * (1 - loss / 100)), loss, avg, avg - 2, avg + 5, jitter)
    return PingResult(target, stats)


class MeasureTests(unittest.TestCase):
    def test_summary_numbers(self):
        s = ping.summarize([20.0, 30.0, None, 40.0])
        self.assertEqual((s.sent, s.received, s.loss_pct), (4, 3, 25.0))
        self.assertEqual((s.avg, s.low, s.high), (30.0, 20.0, 40.0))
        self.assertEqual(s.jitter, 10.0)       # |30-20| and |40-30|, averaged

    def test_all_failed_is_unreachable(self):
        s = ping.summarize([None, None, None])
        self.assertFalse(s.reachable)
        self.assertEqual((s.loss_pct, s.avg), (100.0, None))
        self.assertEqual(ping.rate(s), "Unreachable")

    def test_nothing_sent_is_not_tested(self):
        self.assertEqual(ping.rate(ping.summarize([])), "Not tested")

    def test_measure_times_connections_and_records_failures(self):
        times = iter([0.0, 0.020, 1.0, 2.0, 2.050])            # clock reads: try1 start/end, try2 start (fails, no end), try3 start/end
        outcomes = iter(["ok", "fail", "ok"])
        def connect(address, timeout):
            if next(outcomes) == "fail":
                raise OSError("timed out")
            return FakeConn()
        out = ping.measure(("1.2.3.4", 443), samples=3, connect=connect, clock=lambda: next(times), sleep=lambda s: None)
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(out[0], 20.0)
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[2], 50.0)

    def test_cancel_stops_early(self):
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(ping.measure(("1.2.3.4", 443), samples=5, cancel=cancel, connect=lambda *a, **k: FakeConn()), [])

    def test_run_all_keeps_order_reports_progress_and_survives_dns_failure(self):
        targets = [Target("a", "A", "reference", "a.test"), Target("b", "B", "region", "bad.test"), Target("c", "C", "region", "c.test")]
        def resolve(host, port):
            if host == "bad.test":
                raise OSError("no such host")
            return "9.9.9.9"
        seen = []
        results = ping.run_all(targets, samples=2, connect=lambda *a, **k: FakeConn(), resolve=resolve,
                               sleep=lambda s: None, progress=seen.append)
        self.assertEqual([r.target.id for r in results], ["a", "b", "c"])
        self.assertEqual(len(seen), 3)
        self.assertTrue(results[0].stats.reachable)
        self.assertFalse(results[1].stats.reachable)
        self.assertIn("look up", results[1].error)

    def test_rating_thresholds(self):
        self.assertEqual(ping.rate(result(avg=30).stats), "Great")
        self.assertEqual(ping.rate(result(avg=80).stats), "OK")
        self.assertEqual(ping.rate(result(avg=150).stats), "Poor")

    def test_target_list_is_sane(self):
        ids = [t.id for t in ping.TARGETS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(t.kind == "reference" for t in ping.TARGETS), 2)
        self.assertTrue(all(t.port == 443 and t.kind in ("reference", "region") for t in ping.TARGETS))


class EnvironmentTests(unittest.TestCase):
    def test_adapter_classification(self):
        c = ping.classify_adapter
        self.assertEqual(c("Wi-Fi"), "wifi")
        self.assertEqual(c("Ethernet"), "ethernet")
        self.assertEqual(c("Ethernet 2"), "ethernet")
        self.assertEqual(c("ProtonVPN"), "vpn")
        self.assertEqual(c("Proton VPN TUN"), "vpn")
        self.assertEqual(c("OpenVPN Wintun"), "vpn")
        for virtual in ("vEthernet (WSL)", "Loopback Pseudo-Interface 1", "Local Area Connection* 3",
                        "VirtualBox Host-Only Network", "Bluetooth Network Connection"):
            self.assertEqual(c(virtual), "virtual", virtual)

    def test_parse_wlan(self):
        text = """
    Name                   : Wi-Fi
    State                  : connected
    Radio type             : 802.11ac
    Signal                 : 48%
"""
        self.assertEqual(ping.parse_wlan(text), {"signal": 48, "radio": "802.11ac"})
        self.assertEqual(ping.parse_wlan("    State                  : disconnected\n    Signal : 90%"),
                         {"signal": None, "radio": None})
        self.assertEqual(ping.parse_wlan("nonsense"), {"signal": None, "radio": None})

    def test_wifi_vs_ethernet(self):
        wifi = Environment([Adapter("Wi-Fi", "wifi")])
        both = Environment([Adapter("Wi-Fi", "wifi"), Adapter("Ethernet", "ethernet")])
        self.assertTrue(wifi.using_wifi)
        self.assertFalse(both.using_wifi)          # Windows prefers the cable when both are up
        self.assertTrue(both.using_ethernet)


class DiagnosisTests(unittest.TestCase):
    def d(self, results, env=None):
        return ping.diagnose(results, env or Environment([Adapter("Ethernet", "ethernet")]))

    def titles(self, diagnosis):
        return [f.title for f in diagnosis.findings]

    def test_healthy_connection(self):
        d = self.d([result("Cloudflare", avg=25), result("Mumbai", "region", avg=40)])
        self.assertEqual(d.verdict.level, "good")
        self.assertIn("distance", d.verdict.detail)

    def test_no_internet(self):
        d = self.d([result(avg=None), result("M", "region", avg=None)])
        self.assertEqual(d.verdict.level, "bad")
        self.assertIn("No connection", d.verdict.title)

    def test_vpn_is_called_out_by_name(self):
        env = Environment([Adapter("Wi-Fi", "wifi"), Adapter("ProtonVPN", "vpn")])
        self.assertTrue(any("ProtonVPN" in t for t in self.titles(self.d([result(avg=30)], env))))

    def test_weak_wifi_signal(self):
        env = Environment([Adapter("Wi-Fi", "wifi")], wifi_signal=40)
        self.assertTrue(any("Weak Wi-Fi signal (40%)" in t for t in self.titles(self.d([result(avg=30)], env))))

    def test_wifi_with_spikes_suggests_a_cable(self):
        env = Environment([Adapter("Wi-Fi", "wifi")], wifi_signal=80)
        d = self.d([result(avg=40, jitter=35)], env)
        text = " ".join(f.detail for f in d.findings)
        self.assertIn("cable", text)
        self.assertTrue(any("Unsteady" in t for t in self.titles(d)))

    def test_packet_loss_flagged(self):
        d = self.d([result(avg=40, loss=10)])
        self.assertTrue(any("lost" in t for t in self.titles(d)))
        self.assertEqual(d.verdict.level, "bad")

    def test_slow_even_nearby_points_at_local_cause(self):
        d = self.d([result(avg=180)])
        self.assertTrue(any("Slow even to nearby" in t for t in self.titles(d)))

    def test_names_the_closest_region(self):
        d = self.d([result("Cloudflare", avg=20), result("Tokyo", "region", avg=90), result("Mumbai", "region", avg=35)])
        self.assertTrue(any("Closest game-cloud region: Mumbai (35 ms)" in t for t in self.titles(d)))

    def test_healthy_cable_connection_has_no_warnings(self):
        d = self.d([result(avg=20), result("Mumbai", "region", avg=30)])
        self.assertFalse([f for f in d.findings if f.level == "warn"])

    def test_advice_is_honest_about_what_does_not_help(self):
        self.assertIn("flushing DNS", ping.MYTHS)
        self.assertTrue(any("closest server region" in t for t in ping.TIPS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
