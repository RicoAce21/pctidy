"""
elevate.py - run a fixed list of commands with administrator rights (Windows shows its shield prompt).

Only ever used with commands this app builds itself. Every piece is checked so nothing unexpected
can slip into the command line.
"""
import base64
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional

CANCELLED = 1223                      # Windows' number for "the user said No to the prompt"
FORBIDDEN = set('"&|<>^%\r\n\'`$;')   # characters that could change what a command line means


@dataclass
class ElevatedResult:
    returncode: int
    output: str
    cancelled: bool = False
    timed_out: bool = False
    error: str = ""


def _quote(token: str) -> str:
    if any(c in FORBIDDEN for c in token):
        raise ValueError(f"Unsafe character in command part: {token!r}")
    return f'"{token}"' if " " in token else token


def build_script(steps: List[List[str]], out_path: str) -> str:
    """PowerShell that asks for admin rights, runs the steps (stopping at the first failure), returns the exit code."""
    inner = " && ".join(" ".join(_quote(t) for t in step) for step in steps)
    cmdline = f'/c ({inner}) > "{out_path}" 2>&1'
    ps_cmdline = cmdline.replace("'", "''")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '{ps_cmdline}' -Verb RunAs -Wait -PassThru -WindowStyle Hidden\n"
        "  $code = $p.ExitCode\n"
        "  if ($null -eq $code) { exit 1 }\n"
        "  exit $code\n"
        "} catch {\n"
        f"  if ($_.Exception.Message -match 'cancel') {{ exit {CANCELLED} }}\n"
        "  [Console]::Error.WriteLine($_.Exception.Message); exit 1\n"
        "}\n"
    )


def _read_output(path: str) -> str:
    for encoding in ("oem", "utf-8"):
        try:
            with open(path, encoding=encoding, errors="replace") as f:
                return f.read().strip()
        except LookupError:
            continue
        except OSError:
            break
    return ""


def run_elevated(steps: List[List[str]], runner: Optional[Callable] = None, temp_dir: Optional[str] = None,
                 timeout: int = 240, is_windows: Optional[bool] = None) -> ElevatedResult:
    runner = runner or subprocess.run
    if not (sys.platform == "win32" if is_windows is None else is_windows):
        return ElevatedResult(1, "", error="Only works on Windows.")
    out_path = os.path.join(temp_dir or tempfile.gettempdir(), f"pctidy_{uuid.uuid4().hex[:8]}.txt")
    script = build_script(steps, out_path)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    kwargs = {"capture_output": True, "text": True, "errors": "replace", "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    try:
        done = runner(command, **kwargs)
    except subprocess.TimeoutExpired:
        return ElevatedResult(1, "", timed_out=True, error="Timed out waiting for the administrator prompt or the command.")
    except OSError as error:
        return ElevatedResult(1, "", error=f"Couldn't start PowerShell: {error}")
    try:
        output = _read_output(out_path)
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
    return ElevatedResult(done.returncode, output, cancelled=done.returncode == CANCELLED,
                          error=(done.stderr or "").strip())
