"""
System Resource Monitor Module
Tracks CPU, RAM, and top processes.

CPU numbers come from a small background sampler instead of blocking the request
for hundreds of milliseconds, and process CPU % is measured between calls (psutil
always reports 0 for the very first reading, which made every process look idle).
"""

import platform
import threading
import time
from datetime import datetime, timedelta

import psutil

from diagnostics.utils import format_bytes

_format_bytes = format_bytes
_LOGICAL_CPUS = psutil.cpu_count(logical=True) or 1


# ─── CPU SAMPLER ─────────────────────────────────────────────

class _CpuSampler:
    """Samples per-core CPU usage once a second, only while somebody is asking."""

    IDLE_STOP_SECONDS = 30

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._ready = threading.Event()
        self._per_cpu = []
        self._last_request = 0.0

    def get(self):
        self._last_request = time.monotonic()
        self._ensure_running()
        self._ready.wait(3.0)
        return list(self._per_cpu)

    def _ensure_running(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._thread = threading.Thread(target=self._run, name="cpu-sampler", daemon=True)
                self._thread.start()

    def _run(self):
        psutil.cpu_percent(interval=None, percpu=True)  # establish a baseline
        while time.monotonic() - self._last_request < self.IDLE_STOP_SECONDS:
            sample = psutil.cpu_percent(interval=1.0, percpu=True)
            self._per_cpu = sample
            self._ready.set()


_cpu_sampler = _CpuSampler()


# ─── STATIC SYSTEM INFO ──────────────────────────────────────

_static_info = {}


def _cpu_name() -> str:
    """Marketing name of the CPU (platform.processor() gives an unreadable family string)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        uname = platform.uname()
        return uname.processor or platform.processor() or "Unknown"


def _os_name() -> str:
    uname = platform.uname()
    if uname.system == "Windows":
        release = uname.release
        try:
            # Python on Windows 11 can still report "10"; build 22000+ is Windows 11.
            if int(uname.version.split(".")[-1]) >= 22000:
                release = "11"
        except (ValueError, IndexError):
            pass
        return f"Windows {release}"
    return f"{uname.system} {uname.release}"


def get_system_info():
    """Get general system information."""
    if not _static_info:
        uname = platform.uname()
        _static_info.update({
            "hostname": uname.node,
            "os": _os_name(),
            "os_version": uname.version,
            "architecture": uname.machine,
            "processor": _cpu_name(),
            "python_version": platform.python_version(),
        })

    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot_time
    return {
        **_static_info,
        "boot_time": boot_time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime": str(uptime).split('.')[0],
    }


def get_cpu_info():
    """Get CPU usage information."""
    try:
        cpu_freq = psutil.cpu_freq()
    except Exception:
        cpu_freq = None
    per_cpu = _cpu_sampler.get()
    overall = round(sum(per_cpu) / len(per_cpu), 1) if per_cpu else 0.0

    return {
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": _LOGICAL_CPUS,
        "current_freq_mhz": round(cpu_freq.current, 2) if cpu_freq else 0,
        "max_freq_mhz": round(cpu_freq.max, 2) if cpu_freq else 0,
        "overall_percent": overall,
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
            "total_display": format_bytes(mem.total),
            "used_display": format_bytes(mem.used),
            "available_display": format_bytes(mem.available),
        },
        "swap": {
            "total_bytes": swap.total,
            "used_bytes": swap.used,
            "free_bytes": swap.free,
            "percent_used": swap.percent,
            "total_display": format_bytes(swap.total),
            "used_display": format_bytes(swap.used),
        },
    }


# ─── PROCESSES ───────────────────────────────────────────────

_PROC_TTL = 1.5  # seconds; stops rapid repeated calls from producing noisy CPU numbers
_proc_lock = threading.Lock()
_proc_snapshot = {"time": 0.0, "rows": []}
_proc_primed = False


def _collect_processes():
    global _proc_primed

    if not _proc_primed:
        # The first cpu_percent() call only sets a baseline; measure over a short window.
        for proc in psutil.process_iter():
            try:
                proc.cpu_percent(None)
            except (psutil.Error, OSError):
                continue
        time.sleep(0.4)
        _proc_primed = True

    rows = []
    for proc in psutil.process_iter():
        try:
            with proc.oneshot():
                pid = proc.pid
                if pid == 0:  # "System Idle Process" would always top the CPU list
                    continue
                name = proc.name() or "Unknown"
                try:
                    rss = proc.memory_info().rss
                except (psutil.AccessDenied, OSError):
                    rss = 0
                try:
                    # psutil sums across cores (can exceed 100); Task Manager normalises.
                    cpu = min(round(proc.cpu_percent(None) / _LOGICAL_CPUS, 1), 100.0)
                except (psutil.Error, OSError):
                    cpu = 0.0
                try:
                    status = proc.status()
                except (psutil.Error, OSError):
                    status = "unknown"
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue

        rows.append({
            "pid": pid,
            "name": name,
            "cpu_percent": cpu,
            "memory_bytes": rss,
            "memory_display": format_bytes(rss),
            "status": status,
        })
    return rows


def invalidate_process_cache():
    """Force the next get_top_processes() call to re-read (used after a kill)."""
    _proc_snapshot["time"] = 0.0


def get_top_processes(top_n: int = 15, sort_by: str = "memory"):
    """Get the top processes by memory (default) or CPU usage."""
    with _proc_lock:
        now = time.monotonic()
        if not _proc_snapshot["rows"] or now - _proc_snapshot["time"] > _PROC_TTL:
            _proc_snapshot["rows"] = _collect_processes()
            _proc_snapshot["time"] = time.monotonic()
        rows = list(_proc_snapshot["rows"])

    if sort_by == "cpu":
        rows.sort(key=lambda r: (r["cpu_percent"], r["memory_bytes"]), reverse=True)
    else:
        rows.sort(key=lambda r: r["memory_bytes"], reverse=True)
    return rows[:top_n]


# ─── BATTERY ─────────────────────────────────────────────────

def get_battery_info():
    """Get battery information if available."""
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        return None

    secs = battery.secsleft
    known = secs is not None and secs > 0 and secs != psutil.POWER_TIME_UNLIMITED
    return {
        "percent": round(battery.percent, 1),
        "power_plugged": battery.power_plugged,
        "seconds_left": secs if known else -1,
        "time_left": str(timedelta(seconds=secs)) if known
                     else ("Charging" if battery.power_plugged else "Unknown"),
    }
