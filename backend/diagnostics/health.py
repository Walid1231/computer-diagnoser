"""
Hardware Health & System Diagnostics Module
Monitors temperatures, battery degradation, SMART disk health,
and BSOD/crash log analysis.
"""

import os
import subprocess
import json
import platform
import psutil
from datetime import datetime, timedelta


# ─── TEMPERATURE MONITORING ──────────────────────────────────

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

    # Try WMI via PowerShell for thermal zone info
    try:
        # MSAcpi_ThermalZoneTemperature (requires admin, but worth trying)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
             "2>$null | Select-Object InstanceName, CurrentTemperature | ConvertTo-Json"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]
            for sensor in data:
                # Temperature is in tenths of Kelvin
                kelvin_tenths = sensor.get("CurrentTemperature", 0)
                celsius = round((kelvin_tenths / 10.0) - 273.15, 1)
                if 0 < celsius < 120:  # sanity check
                    temps["sensors"].append({
                        "name": sensor.get("InstanceName", "Thermal Zone"),
                        "temperature_c": celsius,
                        "temperature_f": round(celsius * 9/5 + 32, 1),
                    })
                    if temps["cpu_temp"] is None:
                        temps["cpu_temp"] = celsius
                    temps["available"] = True
    except Exception:
        pass

    # Try psutil sensors (works on some systems)
    try:
        sensor_temps = psutil.sensors_temperatures()
        if sensor_temps:
            for name, entries in sensor_temps.items():
                for entry in entries:
                    temps["sensors"].append({
                        "name": f"{name}: {entry.label or 'sensor'}",
                        "temperature_c": entry.current,
                        "temperature_f": round(entry.current * 9/5 + 32, 1),
                        "high": entry.high,
                        "critical": entry.critical,
                    })
                    temps["available"] = True
    except (AttributeError, Exception):
        pass

    # Determine thermal status
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

    battery = psutil.sensors_battery()
    if battery is None:
        result["message"] = "No battery detected — this may be a desktop PC."
        return result

    result["available"] = True
    result["current_percent"] = battery.percent
    result["power_plugged"] = battery.power_plugged

    # Generate battery report
    report_path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "battery-report.xml")
    try:
        subprocess.run(
            ["powercfg", "/batteryreport", "/xml", "/output", report_path],
            capture_output=True, text=True, timeout=10
        )

        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Parse design capacity
            import re
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

            # Cleanup
            try:
                os.remove(report_path)
            except OSError:
                pass

    except Exception:
        result["message"] = "Could not generate battery report. Try running as Administrator."

    return result


# ─── DISK SMART HEALTH ───────────────────────────────────────

def get_disk_health():
    """
    Check disk health status using PowerShell's Get-PhysicalDisk.
    Works on Windows 10/11 without admin rights.
    """
    disks = []

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus, "
             "OperationalStatus, Size, BusType | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]

            for disk in data:
                size_bytes = disk.get("Size", 0) or 0
                health = disk.get("HealthStatus", "Unknown")
                status = disk.get("OperationalStatus", "Unknown")

                health_level = "good"
                if health in ("Warning", "Degraded"):
                    health_level = "warning"
                elif health in ("Unhealthy", "Unknown"):
                    health_level = "danger"

                disks.append({
                    "name": disk.get("FriendlyName", "Unknown Disk"),
                    "media_type": _media_type_str(disk.get("MediaType", 0)),
                    "health_status": health,
                    "operational_status": status,
                    "size_bytes": size_bytes,
                    "size_display": _format_bytes(size_bytes),
                    "bus_type": _bus_type_str(disk.get("BusType", 0)),
                    "health_level": health_level,
                })

    except Exception:
        pass

    return disks


# ─── CRASH / BSOD LOG ANALYSIS ───────────────────────────────

def get_crash_logs():
    """
    Read Windows Event Viewer for recent crashes, BSODs, and critical errors.
    """
    crashes = []

    try:
        # Query for BugCheck (BSOD) events — Event ID 1001 from BugCheck
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; "
             "StartTime=(Get-Date).AddDays(-30)} -MaxEvents 20 2>$null | "
             "Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message | "
             "ConvertTo-Json"],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]

            for event in data:
                time_raw = event.get("TimeCreated", "")
                # Parse the /Date(...)/ format
                time_str = _parse_win_date(time_raw)
                message = event.get("Message", "")
                if message and len(message) > 300:
                    message = message[:300] + "..."

                crashes.append({
                    "time": time_str,
                    "event_id": event.get("Id", 0),
                    "level": event.get("LevelDisplayName", "Unknown"),
                    "source": event.get("ProviderName", "Unknown"),
                    "message": message,
                })
    except Exception:
        pass

    return {
        "crash_count": len(crashes),
        "events": crashes,
        "message": f"Found {len(crashes)} critical/error events in the last 30 days." if crashes
                   else "No critical errors or BSODs found in the last 30 days. Your system is stable!",
    }


# ─── INSTALLED PROGRAMS ──────────────────────────────────────

def get_installed_programs():
    """
    List installed programs sorted by estimated size.
    """
    programs = []

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* , "
             "HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* 2>$null | "
             "Where-Object { $_.DisplayName } | "
             "Select-Object DisplayName, DisplayVersion, Publisher, EstimatedSize, InstallDate | "
             "Sort-Object -Property EstimatedSize -Descending | "
             "Select-Object -First 50 | ConvertTo-Json"],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]

            for prog in data:
                est_size_kb = prog.get("EstimatedSize", 0) or 0
                size_bytes = est_size_kb * 1024

                programs.append({
                    "name": prog.get("DisplayName", "Unknown"),
                    "version": prog.get("DisplayVersion", ""),
                    "publisher": prog.get("Publisher", ""),
                    "size_bytes": size_bytes,
                    "size_display": _format_bytes(size_bytes) if size_bytes > 0 else "Unknown",
                    "install_date": prog.get("InstallDate", ""),
                })
    except Exception:
        pass

    return programs


# ─── HELPERS ─────────────────────────────────────────────────

def _parse_win_date(date_val):
    """Parse Windows JSON date format /Date(milliseconds)/."""
    if date_val is None:
        return "Unknown"
    if isinstance(date_val, dict):
        if 'DateTime' in date_val:
            dt_str = date_val['DateTime']
            try:
                dt = datetime.strptime(dt_str, "%A, %B %d, %Y %I:%M:%S %p")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return str(dt_str)
        if 'value' in date_val:
            return _parse_win_date(date_val['value'])
        return str(date_val)
    if isinstance(date_val, str) and "Date(" in date_val:
        try:
            import re
            ms = int(re.search(r"Date\((\d+)\)", date_val).group(1))
            dt = datetime.fromtimestamp(ms / 1000)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(date_val)
    return str(date_val) if date_val else "Unknown"


def _media_type_str(val):
    """Convert MediaType integer to readable string."""
    mapping = {0: "Unspecified", 3: "HDD", 4: "SSD", 5: "SCM"}
    return mapping.get(val, str(val))


def _bus_type_str(val):
    """Convert BusType integer to readable string."""
    mapping = {
        0: "Unknown", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "IEEE 1394",
        5: "SSA", 6: "Fibre Channel", 7: "USB", 8: "RAID",
        9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD", 13: "MMC",
        14: "MAX", 15: "File Backed Virtual", 16: "Storage Spaces",
        17: "NVMe", 18: "Microsoft Reserved",
    }
    return mapping.get(val, str(val))


def _format_bytes(size: int) -> str:
    """Convert bytes to a human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
