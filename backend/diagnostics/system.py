"""
System Resource Monitor Module
Tracks CPU, RAM, and top processes.
"""

import psutil
import platform
import time
from datetime import datetime, timedelta


def get_system_info():
    """Get general system information."""
    uname = platform.uname()
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time

    return {
        "hostname": uname.node,
        "os": f"{uname.system} {uname.release}",
        "os_version": uname.version,
        "architecture": uname.machine,
        "processor": uname.processor or platform.processor(),
        "boot_time": boot_time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime": str(uptime).split('.')[0],
        "python_version": platform.python_version(),
    }


def get_cpu_info():
    """Get CPU usage information."""
    cpu_freq = psutil.cpu_freq()
    per_cpu = psutil.cpu_percent(interval=0.5, percpu=True)

    return {
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "current_freq_mhz": round(cpu_freq.current, 2) if cpu_freq else 0,
        "max_freq_mhz": round(cpu_freq.max, 2) if cpu_freq else 0,
        "overall_percent": psutil.cpu_percent(interval=0.1),
        "per_cpu_percent": per_cpu,
    }


def get_memory_info():
    """Get RAM usage information."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    return {
        "ram": {
            "total_bytes": mem.total,
            "used_bytes": mem.used,
            "available_bytes": mem.available,
            "percent_used": mem.percent,
            "total_display": _format_bytes(mem.total),
            "used_display": _format_bytes(mem.used),
            "available_display": _format_bytes(mem.available),
        },
        "swap": {
            "total_bytes": swap.total,
            "used_bytes": swap.used,
            "free_bytes": swap.free,
            "percent_used": swap.percent,
            "total_display": _format_bytes(swap.total),
            "used_display": _format_bytes(swap.used),
        },
    }


def get_top_processes(top_n: int = 15):
    """Get the top processes by memory and CPU usage."""
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']):
        try:
            info = proc.info
            mem = info.get('memory_info')
            procs.append({
                "pid": info['pid'],
                "name": info['name'],
                "cpu_percent": info.get('cpu_percent', 0) or 0,
                "memory_bytes": mem.rss if mem else 0,
                "memory_display": _format_bytes(mem.rss) if mem else "0 B",
                "status": info.get('status', 'unknown'),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Sort by memory usage
    procs.sort(key=lambda x: x["memory_bytes"], reverse=True)
    return procs[:top_n]


def get_battery_info():
    """Get battery information if available."""
    battery = psutil.sensors_battery()
    if battery is None:
        return None
    return {
        "percent": battery.percent,
        "power_plugged": battery.power_plugged,
        "seconds_left": battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else -1,
        "time_left": str(timedelta(seconds=battery.secsleft)) if battery.secsleft > 0 and battery.secsleft != psutil.POWER_TIME_UNLIMITED else ("Charging" if battery.power_plugged else "Unknown"),
    }


def _format_bytes(size: int) -> str:
    """Convert bytes to a human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
