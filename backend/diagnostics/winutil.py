"""Helpers for running Windows commands without side effects.

* No console window flashes (the packaged app is built with --noconsole, and every
  plain subprocess call would otherwise pop a black window).
* PowerShell scripts are passed with -EncodedCommand, so quoting can never break
  them, and output is forced to UTF-8 so non-English systems do not crash.
"""

import base64
import json
import os
import subprocess

IS_WINDOWS = os.name == "nt"
_CREATE_NO_WINDOW = 0x08000000

_PS_PRELUDE = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$ProgressPreference='SilentlyContinue';"
    "[Console]::OutputEncoding=(New-Object System.Text.UTF8Encoding $false);"
)


def run_hidden(args, timeout=15, **kwargs):
    """subprocess.run() without a console window.

    Returns the CompletedProcess, or None if the command could not start or timed out.
    """
    options = {"capture_output": True, "text": True, "errors": "replace"}
    if IS_WINDOWS:
        options["creationflags"] = _CREATE_NO_WINDOW
    options.update(kwargs)
    try:
        return subprocess.run(args, timeout=timeout, **options)
    except (subprocess.TimeoutExpired, OSError):
        return None


def run_powershell(script: str, timeout=20):
    """Run a PowerShell script (UTF-8 output). Returns CompletedProcess or None."""
    encoded = base64.b64encode((_PS_PRELUDE + script).encode("utf-16-le")).decode("ascii")
    return run_hidden(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        timeout=timeout, encoding="utf-8",
    )


def powershell_json(script: str, timeout=20):
    """Run a script that ends in ConvertTo-Json and return a list of dicts.

    Returns None when PowerShell failed / timed out / printed invalid JSON, and an
    empty list when the command ran but produced no output, so callers can tell
    "could not read" apart from "nothing there".
    """
    proc = run_powershell(script, timeout)
    if proc is None:
        return None
    out = (proc.stdout or "").lstrip("﻿").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if data is None:
        return []
    return data if isinstance(data, list) else [data]
