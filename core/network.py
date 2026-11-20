"""
network.py - a ladder of network fixes, safest first.

Each fix just runs a standard Windows command (ipconfig / netsh) - nothing custom. They all
need administrator rights, so if the app isn't running as administrator they will fail with a
clear message instead of doing anything.
"""
import base64
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional

from .safety import log_event
from .startup import is_admin

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


@dataclass
class Fix:
    id: str
    name: str
    what: str            # what it does, in plain words
    when: str            # when to use it
    risk: str            # shown as a badge
    steps: List[List[str]]
    after: str = ""      # what to do once it finished
    timeout: int = 30


FIXES = [
    Fix("flush_dns", "Flush DNS cache",
        "Clears your PC's saved list of website addresses. Fixes sites that won't load or load the wrong thing.",
        "Try this first. Pages not loading while other apps work.",
        "Safe", [["ipconfig", "/flushdns"]],
        after="Done. Your PC will quietly rebuild the list as you browse."),
    Fix("renew_ip", "Renew IP address",
        "Gives up your current network address and asks the router for a fresh one.",
        "Connected but nothing works, or after the Wi-Fi was acting up.",
        "Drops connection for a few seconds", [["ipconfig", "/release"], ["ipconfig", "/renew"]],
        after="Your connection should come back by itself. A VPN may need reconnecting.", timeout=90),
    Fix("reset_winsock", "Reset Winsock",
        "Resets the part of Windows that apps use to talk to the network. Clears out corrupted network settings.",
        "Only if the first two didn't help. Weird, persistent connection errors.",
        "Needs a restart", [["netsh", "winsock", "reset"]],
        after="Restart your laptop for this to take effect. Some VPN or security programs may need reconnecting."),
]


@dataclass
class FixResult:
    ok: bool
    output: str
    message: str


def _run(command: List[str], timeout: int, runner: Callable):
    kwargs = {"capture_output": True, "text": True, "errors": "replace", "timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return runner(command, **kwargs)


def run_fix(fix: Fix, runner: Optional[Callable] = None) -> FixResult:
    """Run every step of a fix in order. Stops at the first step that fails."""
    runner = runner or subprocess.run
    if not IS_WINDOWS:
        return FixResult(False, "", "Network fixes only work on Windows.")
    log_lines = []
    for index, command in enumerate(fix.steps):
        try:
            done = _run(command, fix.timeout, runner)
        except subprocess.TimeoutExpired:
            return FixResult(False, "\n".join(log_lines),
                             f"'{' '.join(command)}' took too long and was stopped. Check your connection and try again.")
        except OSError as error:
            return FixResult(False, "\n".join(log_lines), f"Couldn't run {command[0]}: {error}")
        text = ((done.stdout or "") + (done.stderr or "")).strip()
        log_lines.append(f"> {' '.join(command)}\n{text}")
        if done.returncode != 0:
            hint = "" if is_admin() else " This usually means the app needs to run as administrator."
            partial = ""
            if index > 0:
                partial = " The first step worked but this one didn't, so reconnect your Wi-Fi to get an address back."
            log_event("network_fix_failed", f"{fix.id}: {' '.join(command)}")
            return FixResult(False, "\n\n".join(log_lines), f"That step failed.{hint}{partial}")
    log_event("network_fix_ok", fix.id)
    return FixResult(True, "\n\n".join(log_lines), fix.after)


# ---------------------------------------------------------------- running with administrator rights
CANCELLED = 1223          # Windows' own number for "the user said No to the admin prompt"


def build_elevated_script(fix: Fix, out_path: str) -> str:
    """
    PowerShell that asks Windows for administrator rights (the shield prompt), runs this fix's
    fixed commands in one elevated window, waits, and passes back the exit code.
    The commands come only from the FIXES list above - never from user input.
    """
    inner = " && ".join(" ".join(step) for step in fix.steps)     # '&&' = stop at the first failure
    cmdline = f'/c ({inner}) > "{out_path}" 2>&1'
    ps_cmdline = cmdline.replace("'", "''")                       # escape for a PowerShell string
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '{ps_cmdline}' -Verb RunAs -Wait -PassThru -WindowStyle Hidden\n"
        "  $code = $p.ExitCode\n"
        "  if ($null -eq $code) { exit 1 }\n"
        "  exit $code\n"
        f"}} catch {{\n"
        f"  if ($_.Exception.Message -match 'cancel') {{ exit {CANCELLED} }}\n"
        "  [Console]::Error.WriteLine($_.Exception.Message); exit 1\n"
        "}\n"
    )


def _read_output(path: str) -> str:
    for encoding in ("oem", "utf-8"):          # command-prompt text uses Windows' 'oem' code page
        try:
            with open(path, encoding=encoding, errors="replace") as f:
                return f.read().strip()
        except LookupError:
            continue
        except OSError:
            break
    return ""


def run_fix_elevated(fix: Fix, runner: Optional[Callable] = None, temp_dir: Optional[str] = None) -> FixResult:
    """Run a fix with administrator rights. Windows shows its normal prompt; if you decline, nothing changes."""
    runner = runner or subprocess.run
    if not IS_WINDOWS:
        return FixResult(False, "", "Network fixes only work on Windows.")
    out_path = os.path.join(temp_dir or tempfile.gettempdir(), f"pctidy_{uuid.uuid4().hex[:8]}.txt")
    script = build_elevated_script(fix, out_path)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-EncodedCommand", encoded]
    try:
        # Generous timeout: it includes however long you take to click Yes on the Windows prompt.
        done = _run(command, fix.timeout + 180, runner)
    except subprocess.TimeoutExpired:
        return FixResult(False, "", "Timed out waiting for the administrator prompt or the command to finish.")
    except OSError as error:
        return FixResult(False, "", f"Couldn't start PowerShell: {error}")
    try:
        output = _read_output(out_path)
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
    if done.returncode == CANCELLED:
        return FixResult(False, "", "You cancelled the administrator prompt, so nothing was changed.")
    if done.returncode != 0:
        detail = (done.stderr or "").strip()
        log_event("network_fix_failed", f"{fix.id} (administrator)")
        return FixResult(False, output or detail, "That step failed even with administrator rights. See the details below.")
    log_event("network_fix_ok", f"{fix.id} (administrator)")
    return FixResult(True, output, fix.after)


# ---------------------------------------------------------------- how much is in the DNS cache?
APPROX_BYTES_PER_ENTRY = 300      # a rough guess: Windows doesn't report the real memory size


@dataclass
class DnsCacheInfo:
    entries: Optional[int]        # None = couldn't find out
    approx_bytes: Optional[int]


def dns_cache_info(runner: Optional[Callable] = None) -> DnsCacheInfo:
    """Count the addresses currently saved in the DNS cache. Read-only, no administrator rights needed."""
    runner = runner or subprocess.run
    if not IS_WINDOWS:
        return DnsCacheInfo(None, None)
    command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "(Get-DnsClientCache | Measure-Object).Count"]
    try:
        done = _run(command, 20, runner)
        count = int((done.stdout or "").strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return DnsCacheInfo(None, None)
    if done.returncode != 0 or count < 0:
        return DnsCacheInfo(None, None)
    return DnsCacheInfo(count, count * APPROX_BYTES_PER_ENTRY)


def describe_dns_cache(info: DnsCacheInfo) -> str:
    from .scanner import format_size
    if info.entries is None:
        return "Couldn't read the size right now."
    if info.entries == 0:
        return "Empty right now."
    noun = "address" if info.entries == 1 else "addresses"
    return f"{info.entries:,} saved {noun}, roughly {format_size(max(info.approx_bytes, 1024))} (a rough estimate, kept in memory)."
