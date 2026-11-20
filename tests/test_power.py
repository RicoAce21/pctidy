import base64
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import background_apps, elevate, power, safety

OTHER = "11111111-2222-3333-4444-555555555555"


def done(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class FakePowercfg:
    """Behaves like powercfg: keeps plans in memory, so we can check what really changed."""
    WRITES = {"/setactive", "/duplicatescheme", "/changename", "/setacvalueindex", "/setdcvalueindex", "/delete"}

    def __init__(self, plans=None, active=power.BALANCED, deny=False, no_duplicate=False):
        self.plans = dict(plans or {power.BALANCED: "Balanced", power.HIGH_PERFORMANCE: "High performance"})
        self.active, self.deny, self.no_duplicate = active, deny, no_duplicate
        self.caps, self.calls = {}, []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        sub = command[1]
        if self.deny and sub in self.WRITES:
            return done(1, "", "Access is denied.")
        if sub == "/list":
            lines = [f"Power Scheme GUID: {g}  ({n})" + (" *" if g == self.active else "") for g, n in self.plans.items()]
            return done(0, "Existing Power Schemes (* Active)\n-----------------\n" + "\n".join(lines))
        if sub == "/query":
            cap = self.caps.get(command[2], 100)
            return done(0, f"Power Scheme GUID: {command[2]}\n  Minimum Possible Setting: 0x00000000\n"
                           f"  Maximum Possible Setting: 0x00000064\n  Possible Settings increment: 0x00000001\n"
                           f"  Current AC Power Setting Index: 0x{cap:08x}\n  Current DC Power Setting Index: 0x{cap:08x}\n")
        if sub == "/duplicatescheme":
            base, new = command[2], command[3]
            if self.no_duplicate or base not in self.plans:
                return done(1, "", "The power scheme, subgroup or setting specified does not exist.")
            self.plans[new] = self.plans[base]
            return done(0, f"Power Scheme GUID: {new}")
        if sub == "/changename":
            self.plans[command[2]] = command[3]
            return done(0)
        if sub in ("/setacvalueindex", "/setdcvalueindex"):
            self.caps[command[2]] = int(command[5])
            return done(0)
        if sub == "/setactive":
            if command[2] not in self.plans:
                return done(1, "", "Invalid scheme")
            self.active = command[2]
            return done(0)
        if sub == "/delete":
            if command[2] == self.active:
                return done(1, "", "Cannot delete the active scheme")
            self.plans.pop(command[2], None)
            return done(0)
        return done(1, "", "unknown")

    def cool_guid(self):
        return next((g for g, n in self.plans.items() if n == power.COOL_NAME), None)


class PowerBase(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        safety.APP_DIR, safety.LOG_FILE = tmp, tmp / "actions.log"
        power.IS_WINDOWS = True
        self._admin = power.is_admin
        power.is_admin = lambda: False

    def tearDown(self):
        power.is_admin = self._admin


class ParsingTests(PowerBase):
    def test_parse_list_finds_active_and_names_with_brackets(self):
        text = (f"Existing Power Schemes (* Active)\n----\nPower Scheme GUID: {power.BALANCED}  (Balanced) *\n"
                f"Power Scheme GUID: {OTHER}  (Dell (Optimized))\n")
        plans = power.parse_list(text)
        self.assertEqual([(p.guid, p.name, p.active) for p in plans],
                         [(power.BALANCED, "Balanced", True), (OTHER, "Dell (Optimized)", False)])

    def test_parse_list_ignores_language_of_the_headings(self):
        text = f"Bestehende Energieschemas\nGUID für Energieschema: {OTHER}  (Ausbalanciert) *"
        self.assertEqual(power.parse_list(text)[0].name, "Ausbalanciert")

    def test_parse_throttle_max(self):
        text = "Maximum Possible Setting: 0x00000064\nCurrent AC Power Setting Index: 0x00000063\nCurrent DC Power Setting Index: 0x00000050\n"
        self.assertEqual(power.parse_throttle_max(text), (99, 80))
        self.assertIsNone(power.parse_throttle_max("nothing here"))

    def test_detect_mode(self):
        P = power.Plan
        self.assertEqual(power.detect_mode(P(power.BALANCED, "Balanced")), "balanced")
        self.assertEqual(power.detect_mode(P(power.HIGH_PERFORMANCE, "High performance")), "performance")
        self.assertEqual(power.detect_mode(P(OTHER, power.COOL_NAME)), "cool")
        self.assertEqual(power.detect_mode(P(OTHER, "Dell Optimized")), "other")
        self.assertEqual(power.detect_mode(None), "other")

    def test_get_state_reads_plan_and_cpu_cap(self):
        fake = FakePowercfg()
        fake.caps[power.BALANCED] = 99
        state = power.get_state(fake)
        self.assertEqual((state.mode, state.throttle, state.active.name), ("balanced", (99, 99), "Balanced"))


class PlanningTests(PowerBase):
    NEW = "99999999-8888-7777-6666-555555555555"

    def plans(self, *extra):
        base = [power.Plan(power.BALANCED, "Balanced", True), power.Plan(power.HIGH_PERFORMANCE, "High performance")]
        return base + list(extra)

    def test_cool_makes_a_copy_of_balanced_and_never_edits_windows_plans(self):
        ps = power.build_plan("cool", self.plans(), 90, self.NEW)
        self.assertEqual(ps.steps[0], ["powercfg", "/duplicatescheme", power.BALANCED, self.NEW])
        self.assertEqual(ps.creates, self.NEW)
        written = [s[2] for s in ps.steps if s[1] in ("/setacvalueindex", "/setdcvalueindex", "/changename")]
        self.assertTrue(written and all(g == self.NEW for g in written))       # only ever the copy
        self.assertEqual(ps.steps[-1], ["powercfg", "/setactive", self.NEW])
        self.assertIn(["powercfg", "/setacvalueindex", self.NEW, "SUB_PROCESSOR", "PROCTHROTTLEMAX", "90"], ps.steps)
        self.assertIn(["powercfg", "/setdcvalueindex", self.NEW, "SUB_PROCESSOR", "PROCTHROTTLEMAX", "90"], ps.steps)

    def test_cool_reuses_its_existing_plan_and_just_updates_the_cap(self):
        ps = power.build_plan("cool", self.plans(power.Plan(OTHER, power.COOL_NAME)), 80, self.NEW)
        self.assertIsNone(ps.creates)
        self.assertFalse(any(s[1] == "/duplicatescheme" for s in ps.steps))
        self.assertEqual(ps.target, OTHER)

    def test_cap_limits(self):
        for bad in (49, 100, 0, -5, "abc"):
            with self.assertRaises(ValueError):
                power.build_plan("cool", self.plans(), bad, self.NEW)
        power.build_plan("cool", self.plans(), 50, self.NEW)
        power.build_plan("cool", self.plans(), 99, self.NEW)

    def test_balanced_and_performance_just_switch_to_windows_plans(self):
        self.assertEqual(power.build_plan("balanced", self.plans()).steps, [["powercfg", "/setactive", power.BALANCED]])
        self.assertEqual(power.build_plan("performance", self.plans()).steps,
                         [["powercfg", "/setactive", power.HIGH_PERFORMANCE]])

    def test_performance_tries_to_create_high_performance_when_missing(self):
        ps = power.build_plan("performance", [power.Plan(power.BALANCED, "Balanced", True)], new_guid=self.NEW)
        self.assertEqual(ps.steps[0], ["powercfg", "/duplicatescheme", power.HIGH_PERFORMANCE, self.NEW])

    def test_every_built_step_passes_the_safety_check(self):
        for mode in power.MODES:
            for plans in (self.plans(), [power.Plan(power.BALANCED, "Balanced", True)], []):
                power.validate_steps(power.build_plan(mode, plans, 85, self.NEW).steps)

    def test_validator_rejects_anything_unexpected(self):
        for bad in (["cmd", "/c", "dir"], ["powercfg", "/setactive", "not-a-guid"],
                    ["powercfg", "/restoredefaultschemes", power.BALANCED], ["powercfg", "/setactive"],
                    ["powercfg", "/duplicatescheme", power.BALANCED, "oops"]):
            with self.assertRaises(ValueError):
                power.validate_steps([bad])


class ApplyTests(PowerBase):
    def test_cool_mode_end_to_end_and_remembers_original(self):
        fake = FakePowercfg()
        result = power.apply_mode("cool", 90, runner=fake)
        self.assertTrue(result.ok, result.message)
        cool = fake.cool_guid()
        self.assertEqual((fake.active, fake.caps[cool]), (cool, 90))
        self.assertNotIn(power.BALANCED, fake.caps)                        # Windows' Balanced untouched
        self.assertEqual(power.load_saved()["original_guid"], power.BALANCED)

    def test_original_is_kept_across_several_changes_then_undo_restores_it(self):
        fake = FakePowercfg(active=OTHER, plans={power.BALANCED: "Balanced", OTHER: "Dell Optimized"})
        power.apply_mode("cool", 95, runner=fake)
        power.apply_mode("balanced", runner=fake)
        self.assertEqual(power.load_saved()["original_guid"], OTHER)        # still the very first plan
        undone = power.undo(runner=fake)
        self.assertTrue(undone.ok, undone.message)
        self.assertEqual(fake.active, OTHER)
        self.assertIsNone(fake.cool_guid())                                  # PC Tidy's plan removed
        self.assertEqual(power.load_saved(), {})

    def test_undo_with_nothing_to_undo(self):
        result = power.undo(runner=FakePowercfg())
        self.assertFalse(result.ok)
        self.assertIn("Nothing to undo", result.message)

    def test_without_admin_rights_asks_for_them_and_changes_nothing(self):
        fake = FakePowercfg(deny=True)
        result = power.apply_mode("cool", runner=fake)
        self.assertFalse(result.ok)
        self.assertTrue(result.needs_admin)
        self.assertIsNone(fake.cool_guid())
        self.assertEqual(power.load_saved(), {})                             # nothing remembered, nothing changed

    def test_laptop_without_high_performance_plan_is_explained(self):
        power.is_admin = lambda: True
        fake = FakePowercfg(plans={power.BALANCED: "Balanced"}, no_duplicate=True)
        result = power.apply_mode("performance", runner=fake)
        self.assertFalse(result.ok)
        self.assertFalse(result.needs_admin)
        self.assertIn("maker", result.message)
        self.assertEqual(fake.active, power.BALANCED)

    def test_actions_are_logged(self):
        power.apply_mode("balanced", runner=FakePowercfg(active=OTHER, plans={power.BALANCED: "Balanced", OTHER: "X"}))
        self.assertIn("power_mode", safety.LOG_FILE.read_text())


class ElevatedTests(PowerBase):
    @staticmethod
    def script(command):
        return base64.b64decode(command[command.index("-EncodedCommand") + 1]).decode("utf-16-le")

    def runner(self, code=0, seen=None, output="done"):
        def run(command, **kwargs):
            script = self.script(command)
            if seen is not None:
                seen.append(script)
            m = re.search(r'> "(.*?)" 2>&1', script)
            if m:
                Path(m.group(1)).write_text(output)
            return done(code)
        return run

    def test_elevated_apply_runs_only_powercfg_in_one_prompt(self):
        seen = []
        result = power.apply_mode("cool", 90, runner=FakePowercfg(), elevated=True, elevated_runner=self.runner(0, seen))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(seen), 1)
        self.assertIn("-Verb RunAs", seen[0])
        self.assertIn('"PC Tidy - Cool and quiet"', seen[0])
        inner = re.search(r"-ArgumentList '/c \((.*)\) >", seen[0]).group(1)
        for part in inner.split(" && "):
            self.assertTrue(part.startswith("powercfg /"), part)

    def test_declining_the_windows_prompt_changes_nothing(self):
        result = power.apply_mode("cool", runner=FakePowercfg(), elevated=True,
                                  elevated_runner=self.runner(elevate.CANCELLED))
        self.assertFalse(result.ok)
        self.assertIn("cancelled", result.message)
        self.assertEqual(power.load_saved(), {})

    def test_elevated_undo(self):
        fake = FakePowercfg()
        power.apply_mode("cool", runner=fake)
        seen = []
        result = power.undo(runner=fake, elevated=True, elevated_runner=self.runner(0, seen))
        self.assertTrue(result.ok, result.message)
        self.assertIn("/delete", seen[0])

    def test_unsafe_characters_are_refused(self):
        for bad in ('a"b', "a&b", "a|b", "a%b", "a;b", "a'b"):
            with self.assertRaises(ValueError):
                elevate._quote(bad)

    def test_not_windows_runs_nothing(self):
        calls = []
        r = elevate.run_elevated([["powercfg", "/setactive", power.BALANCED]], runner=lambda *a, **k: calls.append(a), is_windows=False)
        self.assertEqual(calls, [])
        self.assertNotEqual(r.returncode, 0)


class BackgroundAppTests(unittest.TestCase):
    def row(self, name, mem, protected=False):
        return {"pid": 1, "name": name, "cpu": 1.0, "mem_mb": mem, "protected": protected, "count": 2, "pids": [1, 2]}

    def test_finds_known_apps_sorted_by_memory_and_skips_everything_else(self):
        rows = [self.row("Spotify.exe", 200), self.row("chrome.exe", 900), self.row("OneDrive.exe", 300),
                self.row("svchost.exe", 50, True), self.row("explorer.exe", 80)]
        found = background_apps.find_background_apps(rows)
        self.assertEqual([a["label"] for a in found], ["OneDrive", "Spotify"])
        self.assertEqual(found[0]["pids"], [1, 2])

    def test_never_offers_protected_processes(self):
        self.assertEqual(background_apps.find_background_apps([self.row("OneDrive.exe", 300, protected=True)]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
