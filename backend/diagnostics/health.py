"""
Hardware Health & System Diagnostics Module
Monitors temperatures, battery degradation, SMART disk health,
and BSOD/crash log analysis.
"""

import os
import re

import psutil

from diagnostics.utils import format_bytes, ttl_cache
from diagnostics.winutil import powershell_json, run_hidden

_format_bytes = format_bytes


# ─── TEMPERATURE MONITORING ──────────────────────────────────

@ttl_cache(10)
def get_temperatures():
    """
    Get CPU/GPU temperatures using Windows WMI via PowerShell.
    Returns available thermal data.
    """
    temps = {
        "cpu_temp": None,
        "gpu_temp": None,
        "sensors": [],
        "available": False,
    }

    # MSAcpi_ThermalZoneTemperature (usually needs admin, but worth trying)
    data = powershell_json(
        "Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature | "
        "Select-Object InstanceName, CurrentTemperature | ConvertTo-Json -Compress",
        timeout=10,
    ) or []
    zone_temps = []
    for sensor in data:
        try:
            celsius = round((float(sensor.get("CurrentTemperature") or 0) / 10.0) - 273.15, 1)  # tenths of Kelvin
        except (TypeError, ValueError):
            continue
        if 0 < celsius < 120:  # sanity check
            zone_temps.append(celsius)
            temps["sensors"].append({
                "name": sensor.get("InstanceName", "Thermal Zone"),
                "temperature_c": celsius,
                "temperature_f": round(celsius * 9 / 5 + 32, 1),
            })
            temps["available"] = True
    if zone_temps:
        temps["cpu_temp"] = max(zone_temps)  # the hottest zone is the one that matters

    # psutil sensors (not available on Windows, works on some other systems)
    try:
        for name, entries in (psutil.sensors_temperatures() or {}).items():
            for entry in entries:
                temps["sensors"].append({
                    "name": f"{name}: {entry.label or 'sensor'}",
                    "temperature_c": entry.current,
                    "temperature_f": round(entry.current * 9 / 5 + 32, 1),
                    "high": entry.high,
                    "critical": entry.critical,
                })
                temps["available"] = True
                if temps["cpu_temp"] is None:
                    temps["cpu_temp"] = entry.current
    except Exception:
        pass

    if temps["cpu_temp"] is not None:
        t = temps["cpu_temp"]
        if t > 90:
            temps["status"] = "critical"
            temps["message"] = f"CPU at {t}°C — DANGEROUSLY HOT! Possible thermal throttling."
        elif t > 75:
            temps["status"] = "warning"
            temps["message"] = f"CPU at {t}°C — Running warm. Check airflow and cooling."
        elif t > 60:
            temps["status"] = "moderate"
            temps["message"] = f"CPU at {t}°C — Normal under load."
        else:
            temps["status"] = "good"
            temps["message"] = f"CPU at {t}°C — Cool and healthy."
    else:
        temps["status"] = "unknown"
        temps["message"] = "Temperature data unavailable. Try running as Administrator."

    return temps


# ─── BATTERY HEALTH / DEGRADATION ────────────────────────────

@ttl_cache(60)
def get_battery_health():
    """
    Generate and parse a Windows battery report to determine
    battery degradation level.
    """
    result = {
        "available": False,
        "design_capacity_mwh": None,
        "full_charge_capacity_mwh": None,
        "degradation_percent": None,
        "cycle_count": None,
        "health_status": "unknown",
        "message": "Battery health data unavailable.",
    }

    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        result["message"] = "No battery detected — this may be a desktop PC."
        return result

    result["available"] = True
    result["current_percent"] = battery.percent
    result["power_plugged"] = battery.power_plugged

    report_path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), f"diagnoser-battery-{os.getpid()}.xml")
    try:
        run_hidden(["powercfg", "/batteryreport", "/xml", "/output", report_path], timeout=15)

        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            design_match = re.search(r"<DesignCapacity>(\d+)</DesignCapacity>", content)
            full_match = re.search(r"<FullChargeCapacity>(\d+)</FullChargeCapacity>", content)
            cycle_match = re.search(r"<CycleCount>(\d+)</CycleCount>", content)

            if design_match and full_match:
                design = int(design_match.group(1))
                full = int(full_match.group(1))
                result["design_capacity_mwh"] = design
                result["full_charge_capacity_mwh"] = full

                if design > 0:
                    health_pct = round((full / design) * 100, 1)
                    degradation = round(100 - health_pct, 1)
                    result["degradation_percent"] = max(0, degradation)
                    result["health_percent"] = min(100, health_pct)

                    if degradation < 10:
                        result["health_status"] = "excellent"
                        result["message"] = f"Battery health is excellent — {health_pct}% of original capacity remains."
                    elif degradation < 25:
                        result["health_status"] = "good"
                        result["message"] = f"Battery health is good — {health_pct}% capacity. Normal wear."
                    elif degradation < 50:
                        result["health_status"] = "fair"
                        result["message"] = f"Battery has degraded to {health_pct}% — consider replacement soon."
                    else:
                        result["health_status"] = "poor"
                        result["message"] = f"Battery critically degraded — only {health_pct}% capacity. Replace battery."

            if cycle_match:
                result["cycle_count"] = int(cycle_match.group(1))
        else:
            result["message"] = "Could not generate battery report. Try running as Administrator."
    except Exception:
        result["message"] = "Could not generate battery report. Try running as Administrator."
    finally:
        try:
            os.remove(report_path)
        except OSError:
            pass

    return result


# ─── DISK SMART HEALTH ───────────────────────────────────────

# Windows PowerShell 5.1 serialises these enums as numbers; the script below asks for
# the names, and these maps cover the numeric case as a fallback.
_HEALTH_NAMES = {0: "Healthy", 1: "Warning", 2: "Unhealthy", 5: "Unknown"}
_MEDIA_NAMES = {0: "Unspecified", 3: "HDD", 4: "SSD", 5: "SCM"}
_BUS_NAMES = {
    0: "Unknown", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "IEEE 1394", 5: "SSA", 6: "Fibre Channel",
    7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD", 13: "MMC", 14: "MAX",
    15: "File Backed Virtual", 16: "Storage Spaces", 17: "NVMe", 18: "Microsoft Reserved",
}


def _name_of(value, mapping):
    if isinstance(value, int):
        return mapping.get(value, str(value))
    return str(value) if value not in (None, "") else "Unknown"


@ttl_cache(120)
def get_disk_health():
    """Check disk health status using PowerShell's Get-PhysicalDisk (no admin needed)."""
    data = powershell_json(
        "Get-PhysicalDisk | Select-Object FriendlyName, Size, "
        "@{n='Media';e={\"$($_.MediaType)\"}}, "
        "@{n='Health';e={\"$($_.HealthStatus)\"}}, "
        "@{n='Op';e={($_.OperationalStatus | ForEach-Object { \"$_\" }) -join ', '}}, "
        "@{n='Bus';e={\"$($_.BusType)\"}} | ConvertTo-Json -Compress",
        timeout=15,
    ) or []

    disks = []
    for disk in data:
        size_bytes = disk.get("Size", 0) or 0
        health = _name_of(disk.get("Health"), _HEALTH_NAMES)

        if health == "Healthy":
            level = "good"
        elif health in ("Unhealthy",):
            level = "danger"
        else:  # Warning, Degraded, Unknown...
            level = "warning"

        disks.append({
            "name": disk.get("FriendlyName") or "Unknown Disk",
            "media_type": _name_of(disk.get("Media"), _MEDIA_NAMES),
            "health_status": health,
            "operational_status": disk.get("Op") or "Unknown",
            "size_bytes": size_bytes,
            "size_display": format_bytes(size_bytes),
            "bus_type": _name_of(disk.get("Bus"), _BUS_NAMES),
            "health_level": level,
        })
    return disks


# ─── CRASH / BSOD LOG ANALYSIS ───────────────────────────────

# The previous query returned *any* Error/Critical System event, which is never empty on a
# healthy PC (service timeouts, DCOM...) and was reported as "crashes". This one only
# looks at events that mean something actually went down.
_CRASH_SCRIPT = r"""
$start = (Get-Date).AddDays(-30)
$items = @()
$items += Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008; StartTime=$start} -MaxEvents 30
$items += Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; Id=1001; StartTime=$start} -MaxEvents 30
$items += Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$start} -MaxEvents 10
$items += Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error','Application Hang'; StartTime=$start} -MaxEvents 30
@($items | Where-Object { $_ } | Sort-Object TimeCreated -Descending | Select-Object -First 30 `
  @{n='Time';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}}, Id, LevelDisplayName, ProviderName, `
  @{n='Msg';e={ ("$($_.Message)" -split "`n")[0] }}) | ConvertTo-Json -Compress
"""


def _classify_event(event_id, provider):
    provider = provider or ""
    if "WHEA" in provider:
        return "Hardware error", "Critical"
    if event_id == 41:
        return "Unexpected restart", "Critical"
    if event_id == 1001 and "SystemErrorReporting" in provider:
        return "Blue screen (BSOD)", "Critical"
    if event_id == 6008:
        return "Unexpected shutdown", "Error"
    if provider == "Application Hang":
        return "App hang", "Warning"
    return "App crash", "Warning"


@ttl_cache(120)
def get_crash_logs():
    """Recent real crashes: BSODs, unexpected restarts, hardware errors, app crashes/hangs."""
    data = powershell_json(_CRASH_SCRIPT, timeout=30)

    crashes = []
    system_level = 0
    for event in data or []:
        kind, level = _classify_event(event.get("Id"), event.get("ProviderName"))
        if not kind.startswith("App"):
            system_level += 1
        text = (event.get("Msg") or "").strip()
        message = f"{kind}: {text}" if text else kind
        if len(message) > 300:
            message = message[:300] + "..."
        crashes.append({
            "time": event.get("Time") or "Unknown",
            "event_id": event.get("Id", 0),
            "level": level,
            "source": event.get("ProviderName") or "Unknown",
            "message": message,
            "kind": kind,
        })

    if data is None:
        message = "Could not read the Windows Event Log. Try running as Administrator."
    elif not crashes:
        message = "No crashes, blue screens or unexpected restarts in the last 30 days. Your system is stable!"
    else:
        message = (f"{system_level} system-level crash event(s) and {len(crashes) - system_level} "
                   f"application crash/hang event(s) in the last 30 days.")

    return {
        "crash_count": len(crashes),
        "system_crash_count": system_level,
        "events": crashes,
        "message": message,
    }


# ─── INSTALLED PROGRAMS ──────────────────────────────────────

_PROGRAMS_SCRIPT = r"""
$paths = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
         'HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
         'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
Get-ItemProperty $paths | Where-Object { $_.DisplayName -and -not $_.SystemComponent -and -not $_.ParentKeyName } |
  Select-Object DisplayName, DisplayVersion, Publisher, EstimatedSize, InstallDate | ConvertTo-Json -Compress
"""


@ttl_cache(300)
def get_installed_programs():
    """List installed programs (machine-wide and per-user) sorted by estimated size."""
    data = powershell_json(_PROGRAMS_SCRIPT, timeout=20) or []

    programs = {}
    for prog in data:
        name = prog.get("DisplayName")
        if not name:
            continue
        try:
            size_bytes = int(prog.get("EstimatedSize") or 0) * 1024  # registry value is in KB
        except (TypeError, ValueError):
            size_bytes = 0
        version = prog.get("DisplayVersion") or ""
        entry = {
            "name": name,
            "version": version,
            "publisher": prog.get("Publisher") or "",
            "size_bytes": size_bytes,
            "size_display": format_bytes(size_bytes) if size_bytes > 0 else "Unknown",
            "install_date": prog.get("InstallDate") or "",
        }
        key = (name.lower(), version)  # the same app often appears in both 32- and 64-bit keys
        if key not in programs or size_bytes > programs[key]["size_bytes"]:
            programs[key] = entry

    ordered = sorted(programs.values(), key=lambda p: (-p["size_bytes"], p["name"].lower()))
    return ordered[:50]
