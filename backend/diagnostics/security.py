"""
Security & System Protection Module
Checks Windows Defender, Firewall, Windows Update status.
"""

import subprocess
import json
import re
from datetime import datetime


def get_security_status():
    """Get comprehensive security status report."""
    return {
        "defender": get_defender_status(),
        "firewall": get_firewall_status(),
        "updates": get_update_status(),
    }


def get_defender_status():
    """Check Windows Defender/Antivirus status."""
    status = {
        "enabled": None,
        "real_time_protection": None,
        "status_level": "unknown",
        "message": "Could not determine antivirus status.",
    }
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-MpComputerStatus 2>$null | Select-Object "
             "AntivirusEnabled, RealTimeProtectionEnabled, "
             "AntivirusSignatureLastUpdated, QuickScanEndTime, "
             "AntispywareEnabled | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            status["enabled"] = data.get("AntivirusEnabled", False)
            status["real_time_protection"] = data.get("RealTimeProtectionEnabled", False)
            status["antispyware"] = data.get("AntispywareEnabled", False)
            sig_update = data.get("AntivirusSignatureLastUpdated", "")
            status["last_signature_update"] = _parse_win_date(sig_update)
            last_scan = data.get("QuickScanEndTime", "")
            status["last_scan"] = _parse_win_date(last_scan)
            if status["enabled"] and status["real_time_protection"]:
                status["status_level"] = "good"
                status["message"] = "Windows Defender is active with real-time protection ON."
            elif status["enabled"]:
                status["status_level"] = "warning"
                status["message"] = "Defender enabled but real-time protection is OFF."
            else:
                status["status_level"] = "danger"
                status["message"] = "Windows Defender is DISABLED — your PC may be vulnerable."
    except Exception:
        pass
    return status


def get_firewall_status():
    """Check Windows Firewall status for all profiles."""
    status = {
        "profiles": [],
        "all_enabled": False,
        "status_level": "unknown",
        "message": "Could not determine firewall status.",
    }
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetFirewallProfile 2>$null | Select-Object Name, Enabled | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]
            all_on = True
            for profile in data:
                enabled = profile.get("Enabled", False)
                if isinstance(enabled, int):
                    enabled = enabled == 1
                status["profiles"].append({
                    "name": profile.get("Name", "Unknown"),
                    "enabled": bool(enabled),
                })
                if not enabled:
                    all_on = False
            status["all_enabled"] = all_on
            if all_on:
                status["status_level"] = "good"
                status["message"] = "Windows Firewall is ON for all profiles."
            else:
                disabled = [p["name"] for p in status["profiles"] if not p["enabled"]]
                status["status_level"] = "danger"
                status["message"] = f"Firewall OFF for: {', '.join(disabled)}."
    except Exception:
        pass
    return status


def get_update_status():
    """Check recent Windows Updates."""
    status = {
        "recent_updates": [],
        "status_level": "unknown",
        "message": "Could not check Windows Update status.",
    }
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-HotFix -ErrorAction SilentlyContinue | "
             "Select-Object -First 10 HotFixID, Description, InstalledOn | ConvertTo-Json"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]
            for update in data:
                status["recent_updates"].append({
                    "id": update.get("HotFixID", ""),
                    "description": update.get("Description", ""),
                    "installed_on": _parse_win_date(update.get("InstalledOn", "")),
                })
            if status["recent_updates"]:
                status["status_level"] = "good"
                u = status["recent_updates"][0]
                status["message"] = f"Up to date. Last: {u['id']} on {u['installed_on']}."
    except Exception:
        pass
    return status


def _parse_win_date(date_val):
    """Parse Windows JSON date format."""
    if date_val is None:
        return "Unknown"
    # Handle dict format: {'value': '/Date(..)/', 'DateTime': '...'}
    if isinstance(date_val, dict):
        if 'DateTime' in date_val:
            dt_str = date_val['DateTime']
            # Extract just the date part from "Friday, April 17, 2026 12:00:00 AM"
            try:
                from datetime import datetime
                dt = datetime.strptime(dt_str, "%A, %B %d, %Y %I:%M:%S %p")
                return dt.strftime("%Y-%m-%d")
            except Exception:
                return str(dt_str)
        if 'value' in date_val:
            return _parse_win_date(date_val['value'])
        return str(date_val)
    if isinstance(date_val, str) and "Date(" in date_val:
        try:
            ms = int(re.search(r"Date\((\d+)\)", date_val).group(1))
            return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
        except Exception:
            return str(date_val)
    return str(date_val) if date_val else "Unknown"
