import base64
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import network, safety

FIX = {f.id: f for f in network.FIXES}


class Runner:
    """Stands in for subprocess.run so no real command ever executes in tests."""
    def __init__(self, results=None, raises=None):
        self.calls, self.results, self.raises = [], list(results or []), raises

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if self.raises:
            raise self.raises
        return self.results.pop(0) if self.results else SimpleNamespace(returncode=0, stdout="ok", stderr="")


def done(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        safety.APP_DIR = tmp
        safety.LOG_FILE = tmp / "actions.log"
        network.IS_WINDOWS = True
        self._admin = network.is_admin
        network.is_admin = lambda: True

    def tearDown(self):
        network.is_admin = self._admin

    def test_ladder_order_safest_first_and_only_standard_commands(self):
        self.assertEqual([f.id for f in network.FIXES], ["flush_dns", "renew_ip", "reset_winsock"])
        allowed = {("ipconfig", "/flushdns"), ("ipconfig", "/release"), ("ipconfig", "/renew"),
                   ("netsh", "winsock", "reset")}
        for fix in network.FIXES:
            for step in fix.steps:
                self.assertIn(tuple(step), allowed)

    def test_flush_runs_exactly_one_command(self):
        r = Runner()
        result = network.run_fix(FIX["flush_dns"], r)
        self.assertTrue(result.ok)
        self.assertEqual(r.calls, [["ipconfig", "/flushdns"]])

    def test_renew_runs_release_then_renew(self):
        r = Runner()
        self.assertTrue(network.run_fix(FIX["renew_ip"], r).ok)
        self.assertEqual(r.calls, [["ipconfig", "/release"], ["ipconfig", "/renew"]])

    def test_failed_release_skips_renew_and_mentions_admin(self):
        network.is_admin = lambda: False
        r = Runner([done(1, "", "Access is denied.")])
        result = network.run_fix(FIX["renew_ip"], r)
        self.assertFalse(result.ok)
        self.assertEqual(r.calls, [["ipconfig", "/release"]])          # never dropped the connection
        self.assertIn("administrator", result.message)

    def test_renew_failing_after_release_tells_user_how_to_recover(self):
        r = Runner([done(0, "released"), done(1, "", "No adapter")])
        result = network.run_fix(FIX["renew_ip"], r)
        self.assertFalse(result.ok)
        self.assertIn("reconnect your Wi-Fi", result.message)

    def test_timeout_is_reported_not_raised(self):
        r = Runner(raises=subprocess.TimeoutExpired("ipconfig", 30))
        result = network.run_fix(FIX["flush_dns"], r)
        self.assertFalse(result.ok)
        self.assertIn("took too long", result.message)

    def test_winsock_reset_tells_user_to_restart(self):
        result = network.run_fix(FIX["reset_winsock"], Runner())
        self.assertTrue(result.ok)
        self.assertIn("Restart", result.message)

    def test_actions_are_logged(self):
        network.run_fix(FIX["flush_dns"], Runner())
        self.assertIn("network_fix_ok", safety.LOG_FILE.read_text())

    def test_not_windows_runs_nothing(self):
        network.IS_WINDOWS = False
        r = Runner()
        self.assertFalse(network.run_fix(FIX["flush_dns"], r).ok)
        self.assertEqual(r.calls, [])


class ElevatedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        safety.APP_DIR = self.tmp / "app"
        safety.LOG_FILE = safety.APP_DIR / "actions.log"
        network.IS_WINDOWS = True

    @staticmethod
    def script_of(command):
        return base64.b64decode(command[command.index("-EncodedCommand") + 1]).decode("utf-16-le")

    def runner(self, code=0, output="", seen=None):
        def run(command, **kwargs):
            script = self.script_of(command)
            if seen is not None:
                seen.append(script)
            match = re.search(r'> "(.*?)" 2>&1', script)
            if match and output:
                Path(match.group(1)).write_text(output)
            return SimpleNamespace(returncode=code, stdout="", stderr="boom" if code == 1 else "")
        return run

    def test_script_asks_for_admin_and_runs_only_the_fixed_commands(self):
        for fix in network.FIXES:
            script = network.build_elevated_script(fix, r"C:\Temp\o.txt")
            self.assertIn("-Verb RunAs", script)
            self.assertIn("-Wait -PassThru", script)
            inner = re.search(r"-ArgumentList '/c \((.*?)\) >", script).group(1)
            steps = {tuple(part.strip().split(" ")) for part in inner.split("&&")}
            self.assertEqual(steps, {tuple(s) for s in fix.steps})
            self.assertIn("if ($null -eq $code) { exit 1 }", script)     # never report success by accident

    def test_apostrophes_in_the_path_are_escaped(self):
        script = network.build_elevated_script(FIX["flush_dns"], r"C:\Users\O'Brien\o.txt")
        self.assertIn("O''Brien", script)

    def test_success_returns_the_commands_output(self):
        result = network.run_fix_elevated(FIX["flush_dns"], self.runner(0, "Successfully flushed the DNS Resolver Cache."),
                                          temp_dir=str(self.tmp))
        self.assertTrue(result.ok)
        self.assertIn("Successfully flushed", result.output)
        self.assertEqual(list(self.tmp.glob("pctidy_*.txt")), [])       # temp file cleaned up

    def test_renew_uses_a_single_prompt_for_both_steps(self):
        seen = []
        network.run_fix_elevated(FIX["renew_ip"], self.runner(0, "ok", seen), temp_dir=str(self.tmp))
        self.assertEqual(len(seen), 1)
        self.assertIn("ipconfig /release && ipconfig /renew", seen[0])

    def test_declining_the_prompt_changes_nothing_and_says_so(self):
        result = network.run_fix_elevated(FIX["flush_dns"], self.runner(network.CANCELLED), temp_dir=str(self.tmp))
        self.assertFalse(result.ok)
        self.assertIn("cancelled", result.message)

    def test_failure_even_with_admin_is_reported(self):
        result = network.run_fix_elevated(FIX["flush_dns"], self.runner(1, "Function failed"), temp_dir=str(self.tmp))
        self.assertFalse(result.ok)
        self.assertIn("Function failed", result.output)

    def test_waiting_for_the_prompt_has_a_generous_timeout(self):
        seen = {}
        def run(command, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        network.run_fix_elevated(FIX["flush_dns"], run, temp_dir=str(self.tmp))
        self.assertGreaterEqual(seen["timeout"], 180)

    def test_timeout_is_reported(self):
        def run(command, **kwargs):
            raise subprocess.TimeoutExpired("powershell", 1)
        self.assertFalse(network.run_fix_elevated(FIX["flush_dns"], run, temp_dir=str(self.tmp)).ok)

    def test_not_windows_runs_nothing(self):
        network.IS_WINDOWS = False
        calls = []
        network.run_fix_elevated(FIX["flush_dns"], lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])


class DnsCacheTests(unittest.TestCase):
    def setUp(self):
        network.IS_WINDOWS = True

    def run_with(self, stdout="", code=0, raises=None):
        def runner(command, **kwargs):
            if raises:
                raise raises
            return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
        return network.dns_cache_info(runner)

    def test_counts_entries_and_estimates_size(self):
        info = self.run_with("142\r\n")
        self.assertEqual(info.entries, 142)
        self.assertEqual(info.approx_bytes, 142 * network.APPROX_BYTES_PER_ENTRY)

    def test_empty_cache(self):
        self.assertEqual(self.run_with("0\n").entries, 0)
        self.assertIn("Empty", network.describe_dns_cache(self.run_with("0\n")))

    def test_failures_become_unknown_not_crashes(self):
        for info in (self.run_with("", code=1), self.run_with("not a number"), self.run_with(""),
                     self.run_with(raises=subprocess.TimeoutExpired("powershell", 20)),
                     self.run_with(raises=OSError("no powershell"))):
            self.assertIsNone(info.entries)
            self.assertIn("Couldn't", network.describe_dns_cache(info))

    def test_description_says_it_is_an_estimate(self):
        text = network.describe_dns_cache(self.run_with("142\n"))
        self.assertIn("142 saved addresses", text)
        self.assertIn("rough estimate", text)
        self.assertIn("1 saved address,", network.describe_dns_cache(self.run_with("1\n")))

    def test_only_reads_never_changes_anything(self):
        seen = []
        def runner(command, **kwargs):
            seen.append(command)
            return SimpleNamespace(returncode=0, stdout="5", stderr="")
        network.dns_cache_info(runner)
        self.assertEqual(len(seen), 1)
        self.assertIn("Get-DnsClientCache", " ".join(seen[0]))
        self.assertNotIn("Clear", " ".join(seen[0]))

    def test_not_windows(self):
        network.IS_WINDOWS = False
        self.assertIsNone(network.dns_cache_info().entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)
