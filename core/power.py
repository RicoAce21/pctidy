"""
power.py - three performance modes built on Windows power plans.

SAFETY DESIGN
  - Windows' own plans (Balanced, High performance) are never edited. "Cool and quiet" is a COPY of
    Balanced named "PC Tidy - Cool and quiet", with the CPU's top speed capped a little. That's all it changes.
  - The plan you were using before is remembered the first time, so Undo puts it back exactly.
  - Only the `powercfg` commands listed in ALLOWED are ever run, and every ID is checked.

WHY "COOL AND QUIET" WORKS
  Most CPU heat comes from short bursts at the very top speed (turbo boost). Capping the maximum
  processor state at 99% usually switches turbo off, which cuts heat and fan noise a lot for a small
  loss in speed during heavy bursts. Lower caps (e.g. 80%) cool more but slow the laptop more.
  Results differ between laptops, so check the Monitor tab's temperature before and after.
"""
import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from . import elevate, safety
from .startup import is_admin

IS_WINDOWS = sys.platform == "win32"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
PLAN_PREFIX = "PC Tidy - "
COOL_NAME = PLAN_PREFIX + "Cool and quiet"
PERF_NAME = PLAN_PREFIX + "Performance"
BAL_NAME = PLAN_PREFIX + "Balanced"
DESCRIPTION = "Created by PC Tidy"
DEFAULT_CAP, MIN_CAP, MAX_CAP = 99, 50, 99
ALLOWED = {"/setactive", "/duplicatescheme", "/changename", "/setacvalueindex", "/setdcvalueindex", "/delete"}
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_LINE_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s+\((.*)\)\s*(\*)?\s*$")

MODES = {
    "cool": "Cool and quiet",
    "balanced": "Balanced",
    "performance": "Performance",
}


@dataclass
class Plan:
    guid: str
    name: str
    active: bool = False


@dataclass
class PowerState:
    plans: List[Plan] = field(default_factory=list)
    active: Optional[Plan] = None
    throttle: Optional[Tuple[int, int]] = None      # (plugged in %, battery %) max processor state
    mode: str = "other"
    original_name: Optional[str] = None             # the plan Undo will restore


@dataclass
class PlanSteps:
    steps: List[List[str]]
    creates: Optional[str]          # id of a new plan this will create (if any)
    target: str                     # id of the plan that ends up active


@dataclass
class ModeResult:
    ok: bool
    message: str
    output: str = ""
    needs_admin: bool = False


# ---------------------------------------------------------------- reading
def parse_list(text: str) -> List[Plan]:
    plans = []
    for line in text.splitlines():
        m = _LINE_RE.search(line.strip())
        if m:
            plans.append(Plan(m.group(1).lower(), m.group(2).strip(), bool(m.group(3))))
    return plans


def parse_throttle_max(text: str) -> Optional[Tuple[int, int]]:
    """powercfg prints the plugged-in value then the battery value last. Reading the hex numbers keeps this language-independent."""
    values = re.findall(r"0x([0-9a-fA-F]{8})", text)
    if len(values) >= 2:
        return int(values[-2], 16), int(values[-1], 16)
    return None


def detect_mode(active: Optional[Plan]) -> str:
    if active is None:
        return "other"
    guid = active.guid.lower()
    if guid == BALANCED or active.name == BAL_NAME:
        return "balanced"
    if guid == HIGH_PERFORMANCE or active.name == PERF_NAME:
        return "performance"
    if active.name == COOL_NAME:
        return "cool"
    return "other"


def _run(command: List[str], runner: Optional[Callable] = None, timeout: int = 30):
    runner = runner or subprocess.run
    kwargs = {"capture_output": True, "text": True, "errors": "replace", "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    return runner(command, **kwargs)


def _state_file():
    return safety.APP_DIR / "power_state.json"


def load_saved() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_state(runner: Optional[Callable] = None) -> PowerState:
    if not IS_WINDOWS:
        return PowerState()
    try:
        listing = _run(["powercfg", "/list"], runner)
        plans = parse_list((listing.stdout or ""))
        active = next((p for p in plans if p.active), None)
        throttle = None
        if active:
            q = _run(["powercfg", "/query", active.guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX"], runner)
            throttle = parse_throttle_max(q.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return PowerState()
    return PowerState(plans, active, throttle, detect_mode(active), load_saved().get("original_name"))


# ---------------------------------------------------------------- planning (pure, so it can be tested)
def validate_steps(steps: List[List[str]]) -> None:
    for step in steps:
        if len(step) < 3 or step[0] != "powercfg" or step[1] not in ALLOWED or not GUID_RE.match(step[2]):
            raise ValueError(f"Refusing to run unexpected command: {step}")
        if step[1] == "/duplicatescheme" and not (len(step) == 4 and GUID_RE.match(step[3])):
            raise ValueError(f"Refusing to run unexpected command: {step}")


def build_plan(mode: str, plans: List[Plan], cap: int = DEFAULT_CAP, new_guid: Optional[str] = None) -> PlanSteps:
    by_guid = {p.guid.lower(): p for p in plans}
    by_name = {p.name: p for p in plans}
    new_guid = (new_guid or str(uuid.uuid4())).lower()

    def copy_of(base: str, name: str) -> List[List[str]]:
        return [["powercfg", "/duplicatescheme", base, new_guid],
                ["powercfg", "/changename", new_guid, name, DESCRIPTION]]

    if mode == "balanced":
        if BALANCED in by_guid:
            return PlanSteps([["powercfg", "/setactive", BALANCED]], None, BALANCED)
        steps = copy_of(BALANCED, BAL_NAME) + [["powercfg", "/setactive", new_guid]]
        return PlanSteps(steps, new_guid, new_guid)

    if mode == "performance":
        if HIGH_PERFORMANCE in by_guid:
            return PlanSteps([["powercfg", "/setactive", HIGH_PERFORMANCE]], None, HIGH_PERFORMANCE)
        if PERF_NAME in by_name:
            g = by_name[PERF_NAME].guid
            return PlanSteps([["powercfg", "/setactive", g]], None, g)
        steps = copy_of(HIGH_PERFORMANCE, PERF_NAME) + [["powercfg", "/setactive", new_guid]]
        return PlanSteps(steps, new_guid, new_guid)

    if mode == "cool":
        cap = int(cap)
        if not MIN_CAP <= cap <= MAX_CAP:
            raise ValueError(f"The CPU speed cap must be between {MIN_CAP} and {MAX_CAP}.")
        steps: List[List[str]] = []
        creates = None
        if COOL_NAME in by_name:
            guid = by_name[COOL_NAME].guid
        else:
            active = next((p for p in plans if p.active), None)
            base = BALANCED if BALANCED in by_guid else (active.guid if active else (plans[0].guid if plans else BALANCED))
            steps += copy_of(base, COOL_NAME)
            guid, creates = new_guid, new_guid
        steps += [["powercfg", "/setacvalueindex", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(cap)],
                  ["powercfg", "/setdcvalueindex", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(cap)],
                  ["powercfg", "/setactive", guid]]
        return PlanSteps(steps, creates, guid)

    raise ValueError(f"Unknown mode: {mode}")


# ---------------------------------------------------------------- doing it
def _run_steps(steps: List[List[str]], runner: Optional[Callable]):
    validate_steps(steps)
    logs = []
    for step in steps:
        done = _run(step, runner)
        text = ((done.stdout or "") + (done.stderr or "")).strip()
        logs.append(f"> {' '.join(step)}\n{text}")
        if done.returncode != 0:
            return False, "\n\n".join(logs), step
    return True, "\n\n".join(logs), None


def _save_original(state: PowerState) -> None:
    saved = load_saved()
    if saved.get("original_guid"):
        return                                      # keep the very first plan, even after several changes
    active = state.active
    if active is None or active.name.startswith(PLAN_PREFIX):
        original = next((p for p in state.plans if p.guid == BALANCED), None)
    else:
        original = active
    if original is None:
        return
    try:
        safety.APP_DIR.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps({"original_guid": original.guid, "original_name": original.name}),
                                 encoding="utf-8")
    except OSError:
        pass


def _execute(steps: List[List[str]], runner, elevated: bool, elevated_runner) -> Tuple[bool, str, Optional[List[str]], str]:
    """Returns (ok, output, failed_step, note). note: 'cancelled' | 'timeout' | ''."""
    if not elevated:
        ok, output, failed = _run_steps(steps, runner)
        return ok, output, failed, ""
    validate_steps(steps)
    result = elevate.run_elevated(steps, runner=elevated_runner, is_windows=IS_WINDOWS)
    if result.cancelled:
        return False, "", None, "cancelled"
    if result.timed_out:
        return False, "", None, "timeout"
    if result.returncode != 0:
        return False, result.output or result.error, steps[-1], ""
    return True, result.output, None, ""


def apply_mode(mode: str, cap: int = DEFAULT_CAP, runner: Optional[Callable] = None, elevated: bool = False,
               elevated_runner: Optional[Callable] = None, new_guid: Optional[str] = None) -> ModeResult:
    if not IS_WINDOWS:
        return ModeResult(False, "Performance modes only work on Windows.")
    state = get_state(runner)
    if not state.plans:
        return ModeResult(False, "Couldn't read your power plans, so nothing was changed.")
    plan = build_plan(mode, state.plans, cap, new_guid)
    ok, output, failed, note = _execute(plan.steps, runner, elevated, elevated_runner)
    if note == "cancelled":
        return ModeResult(False, "You cancelled the administrator prompt, so nothing was changed.")
    if note == "timeout":
        return ModeResult(False, "Timed out waiting for the administrator prompt.")
    if not ok:
        if not elevated and not is_admin():
            return ModeResult(False, "Windows wouldn't allow this change without administrator rights.", output,
                              needs_admin=True)
        if failed and failed[1] == "/duplicatescheme":
            return ModeResult(False, "Your laptop's maker doesn't offer this power plan, so it can't be created. "
                                     "Nothing was changed.", output)
        return ModeResult(False, "That step failed, so the mode wasn't applied. Details are below.", output)
    _save_original(state)
    safety.log_event("power_mode", f"{mode} (cap {cap})" if mode == "cool" else mode)
    label = MODES[mode]
    extra = {"cool": f" The CPU is now capped at {cap}% of its top speed, which usually stops turbo boost.",
             "balanced": " This is Windows' standard plan.",
             "performance": " The CPU will run faster and hotter, and the battery will drain quicker."}[mode]
    return ModeResult(True, f"Switched to {label}.{extra}", output)


def undo(runner: Optional[Callable] = None, elevated: bool = False,
         elevated_runner: Optional[Callable] = None) -> ModeResult:
    """Put back the plan you had before, and remove PC Tidy's own plans."""
    if not IS_WINDOWS:
        return ModeResult(False, "Performance modes only work on Windows.")
    state = get_state(runner)
    saved = load_saved()
    ours = [p for p in state.plans if p.name.startswith(PLAN_PREFIX)]
    if not saved and not ours:
        return ModeResult(False, "Nothing to undo: PC Tidy hasn't changed your power plan.")
    guids = {p.guid for p in state.plans}
    target = saved.get("original_guid") if saved.get("original_guid") in guids else (BALANCED if BALANCED in guids else None)
    if target is None:
        return ModeResult(False, "Couldn't find your original power plan to go back to. Nothing was changed.")
    steps = [["powercfg", "/setactive", target]] + [["powercfg", "/delete", p.guid] for p in ours if p.guid != target]
    ok, output, failed, note = _execute(steps, runner, elevated, elevated_runner)
    if note == "cancelled":
        return ModeResult(False, "You cancelled the administrator prompt, so nothing was changed.")
    if note == "timeout":
        return ModeResult(False, "Timed out waiting for the administrator prompt.")
    if not ok:
        if not elevated and not is_admin():
            return ModeResult(False, "Windows wouldn't allow this change without administrator rights.", output,
                              needs_admin=True)
        return ModeResult(False, "That step failed. Details are below.", output)
    try:
        _state_file().unlink()
    except OSError:
        pass
    safety.log_event("power_undo", target)
    return ModeResult(True, "Back to your original power plan. PC Tidy's own plans were removed.", output)
