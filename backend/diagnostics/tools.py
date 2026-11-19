"""
System Tools Module
Kill processes, system boost, speed test, duplicate finder,
and startup item toggle.
"""

import hashlib
import os
import socket
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import psutil

from diagnostics import startup, storage, system
from diagnostics.utils import format_bytes
from diagnostics.winutil import IS_WINDOWS, run_hidden

_format_bytes = format_bytes


# ─── KILL PROCESS ────────────────────────────────────────────

_PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "memcompression", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsm.exe", "svchost.exe",
    "dwm.exe", "explorer.exe", "fontdrvhost.exe", "securityhealthservice.exe", "msmpeng.exe",
    "sihost.exe", "ctfmon.exe", "taskhostw.exe", "runtimebroker.exe",
}


def _own_process_tree():
    """PIDs of this app, its parents (PyInstaller launcher) and children (WebView2)."""
    pids = {os.getpid()}
    try:
        me = psutil.Process()
        pids.update(p.pid for p in me.parents())
        pids.update(p.pid for p in me.children(recursive=True))
    except psutil.Error:
        pass
    return pids


def kill_process(pid: int):
    """Kill a process by PID."""
    if pid <= 4:
        return {"success": False, "error": "Cannot kill core system processes."}
    if pid in _own_process_tree():
        return {"success": False, "error": "Cannot kill Computer Diagnoser itself."}

    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if name.lower() in _PROTECTED_PROCESSES:
            return {"success": False, "error": f"Cannot kill protected system process: {name}"}

        proc.terminate()
        try:
            proc.wait(timeout=5)
            message = f"Terminated {name} (PID: {pid})"
        except psutil.TimeoutExpired:
            proc.kill()
            message = f"Force-killed {name} (PID: {pid})"
        system.invalidate_process_cache()
        return {"success": True, "message": message}
    except psutil.NoSuchProcess:
        return {"success": False, "error": f"Process {pid} not found."}
    except psutil.AccessDenied:
        return {"success": False, "error": "Access denied. Try running as Administrator."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─── SYSTEM BOOST ────────────────────────────────────────────

def _trim_working_sets() -> int:
    """Ask Windows to page out idle memory of processes we are allowed to touch.

    Returns how many processes were trimmed. Windows pages the data back in on
    demand, so this only helps while memory is actually scarce.
    """
    if not IS_WINDOWS:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.restype = wintypes.BOOL

        access = 0x0100 | 0x1000  # PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION
        skip = _own_process_tree() | {0, 4}
        trimmed = 0
        for pid in psutil.pids():
            if pid in skip:
                continue
            handle = kernel32.OpenProcess(access, False, pid)
            if not handle:
                continue
            try:
                if psapi.EmptyWorkingSet(handle):
                    trimmed += 1
            finally:
                kernel32.CloseHandle(handle)
        return trimmed
    except Exception:
        return 0


def system_boost():
    """
    1-Click System Boost: clears user temp/cache folders, flushes the DNS cache and
    thumbnail cache, and (only when memory is actually tight) trims idle memory.
    Only things that really happened are reported.
    """
    actions = []
    skipped = []
    freed_bytes = 0

    # 1. Temp / cache folders that are always safe to empty.
    for target in storage.cleanup_targets():
        if not target["boost"]:
            continue
        result = storage.clean_directory_contents(target["path"])
        freed_bytes += result["freed_bytes"]
        if result["deleted"]:
            note = f" ({result['errors']} in use, skipped)" if result["errors"] else ""
            actions.append(f"Cleaned {target['name']}: {result['freed']}{note}")
        elif result["errors"]:
            skipped.append(f"{target['name']}: all files are in use")

    # 2. Flush DNS cache.
    if IS_WINDOWS:
        proc = run_hidden(["ipconfig", "/flushdns"], timeout=10)
        if proc is not None and proc.returncode == 0:
            actions.append("Flushed DNS cache")
        else:
            skipped.append("DNS flush failed")

    # 3. Thumbnail cache (Explorer keeps some of these files locked; those are skipped).
    thumb_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Explorer")
    if os.environ.get("LOCALAPPDATA") and os.path.isdir(thumb_dir):
        count = 0
        for name in os.listdir(thumb_dir):
            if name.lower().startswith("thumbcache"):
                path = os.path.join(thumb_dir, name)
                try:
                    size = os.path.getsize(path)
                    os.remove(path)
                    freed_bytes += size
                    count += 1
                except OSError:
                    continue
        if count:
            actions.append(f"Cleared {count} thumbnail cache files")

    # 4. Memory: only worthwhile when RAM is under pressure.
    before = psutil.virtual_memory()
    if before.percent >= 75:
        trimmed = _trim_working_sets()
        if trimmed:
            time.sleep(0.8)
            gained = max(0, psutil.virtual_memory().available - before.available)
            actions.append(f"Trimmed idle memory of {trimmed} processes (+{format_bytes(gained)} available RAM)")
    else:
        skipped.append(f"Memory trim skipped: RAM is healthy ({before.percent:.0f}% used)")

    storage.invalidate_caches()
    return {
        "success": True,
        "actions": actions,
        "skipped": skipped,
        "freed_bytes": freed_bytes,
        "freed_display": format_bytes(freed_bytes),
        "message": f"System boost complete! Freed {format_bytes(freed_bytes)}. "
                   f"Performed {len(actions)} optimizations.",
    }


# ─── INTERNET SPEED TEST ────────────────────────────────────

def _tcp_latency_ms(host: str, port: int, tries: int = 3):
    samples = []
    for _ in range(tries):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            samples.append((time.perf_counter() - started) * 1000)
        except OSError:
            continue
    return round(statistics.median(samples), 1) if samples else None


def speed_test():
    """Download speed + latency test using public speed-test endpoints (no extra libraries)."""
    TEST_URLS = [
        "https://speed.cloudflare.com/__down?bytes=10000000",
        "http://speedtest.tele2.net/10MB.zip",
    ]
    MAX_SECONDS = 8

    result = {
        "download_mbps": None,
        "latency_ms": None,
        "success": False,
        "message": "Speed test failed. Check your internet connection.",
    }

    result["latency_ms"] = _tcp_latency_ms("1.1.1.1", 443) or _tcp_latency_ms("8.8.8.8", 53)

    for url in TEST_URLS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                first = response.read(1024)           # exclude connection setup from the timing
                started = time.perf_counter()
                received = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk or time.perf_counter() - started > MAX_SECONDS:
                        break
                    received += len(chunk)
            elapsed = time.perf_counter() - started
            received += len(first)
            if elapsed > 0.05 and received > 100_000:
                mbps = round((received * 8 / elapsed) / 1_000_000, 2)
                result.update({
                    "download_mbps": mbps,
                    "download_size": format_bytes(received),
                    "download_time": round(elapsed, 2),
                    "success": True,
                    "message": f"Download: {mbps} Mbps | Latency: {result['latency_ms']}ms",
                })
                break
        except Exception:
            continue

    return result


# ─── DUPLICATE FILE FINDER ───────────────────────────────────

_QUICK_BYTES = 64 * 1024


def _quick_hash(path: str, size: int) -> str:
    """Hash of the first and last 64 KB (+ size): cheap filter before a full hash."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(size).encode())
    with open(path, "rb") as handle:
        digest.update(handle.read(_QUICK_BYTES))
        if size > _QUICK_BYTES * 2:
            handle.seek(-_QUICK_BYTES, os.SEEK_END)
            digest.update(handle.read(_QUICK_BYTES))
    return digest.hexdigest()


def _full_hash(path: str) -> str:
    digest = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_candidates(path: str, min_bytes: int, deadline: float):
    """Parallel scan: {size: [(path, mtime), ...]} for files >= min_bytes (system folders skipped)."""
    top_entries, tasks = storage._plan_tasks(path)
    groups = {}
    count = 0
    partial = False

    def run(task):
        _, directory, large_ok, recurse = task
        found = []
        if not large_ok:
            return found, False
        stack = [(directory, True)]
        was_partial = False
        while stack:
            if time.monotonic() > deadline:
                was_partial = True
                break
            current, _ = stack.pop()
            try:
                iterator = os.scandir(current)
            except OSError:
                continue
            with iterator:
                for entry in iterator:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            lowered = entry.name.lower()
                            if (recurse and lowered not in storage.SKIP_ALWAYS
                                    and lowered not in storage.LARGE_SKIP_DIRS
                                    and not storage._is_dir_link(entry)):
                                stack.append((entry.path, True))
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if st.st_size >= min_bytes and not storage._is_cloud_only(st):
                        found.append((st.st_size, entry.path, st.st_mtime))
        return found, was_partial

    with ThreadPoolExecutor(max_workers=8) as pool:
        for found, was_partial in pool.map(run, tasks):
            partial = partial or was_partial
            for size, file_path, mtime in found:
                groups.setdefault(size, []).append((file_path, mtime))
                count += 1
    return groups, count, partial


def find_duplicates(path: str, min_size_mb: float = 10, time_limit: float = 45):
    """
    Find truly identical files: same size -> same first/last 64 KB -> same full-file hash.
    Only fully verified groups are reported, because the UI offers to delete them.
    """
    started = time.monotonic()
    deadline = started + time_limit
    min_bytes = max(1, int(min_size_mb * 1024 * 1024))

    groups, file_count, partial = _collect_candidates(path, min_bytes, started + time_limit * 0.45)

    # Biggest candidates first, so the most valuable groups get verified within the time limit.
    candidates = sorted(((size, files) for size, files in groups.items() if len(files) >= 2),
                        key=lambda item: item[0] * len(item[1]), reverse=True)

    def verify(item):
        size, files = item
        if time.monotonic() > deadline:
            return size, None
        # Hard links share the same data: deleting one frees nothing, so count them once.
        unique = {}
        for file_path, mtime in files:
            try:
                st = os.stat(file_path)
                identity = (st.st_dev, st.st_ino) if st.st_ino else file_path
            except OSError:
                continue
            unique.setdefault(identity, (file_path, mtime))
        files = list(unique.values())
        if len(files) < 2:
            return size, []

        quick = {}
        for file_path, mtime in files:
            try:
                quick.setdefault(_quick_hash(file_path, size), []).append((file_path, mtime))
            except OSError:
                continue

        verified = []
        for same in quick.values():
            if len(same) < 2:
                continue
            full = {}
            for file_path, mtime in same:
                if time.monotonic() > deadline:
                    return size, None
                try:
                    full.setdefault(_full_hash(file_path), []).append((file_path, mtime))
                except OSError:
                    continue
            verified.extend(g for g in full.values() if len(g) >= 2)
        return size, verified

    duplicates = []
    unverified = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for size, verified in pool.map(verify, candidates):
            if verified is None:
                unverified += 1
                continue
            for same in verified:
                same.sort(key=lambda f: f[1])  # oldest first: that one is the one to keep
                wasted = size * (len(same) - 1)
                duplicates.append({
                    "size_bytes": size,
                    "size_display": format_bytes(size),
                    "count": len(same),
                    "wasted_bytes": wasted,
                    "wasted_display": format_bytes(wasted),
                    "files": [{"path": p, "name": os.path.basename(p), "keep": i == 0}
                              for i, (p, _) in enumerate(same)],
                })

    duplicates.sort(key=lambda d: d["wasted_bytes"], reverse=True)
    total_wasted = sum(d["wasted_bytes"] for d in duplicates)

    message = (f"Found {len(duplicates)} duplicate groups wasting {format_bytes(total_wasted)}."
               if duplicates else "No significant duplicates found.")
    if partial or unverified:
        message += " (Time limit reached - results may be incomplete.)"

    return {
        "scanned_files": file_count,
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates[:20],
        "total_wasted_bytes": total_wasted,
        "total_wasted_display": format_bytes(total_wasted),
        "partial": bool(partial or unverified),
        "message": message,
    }


# ─── STARTUP TOGGLE ──────────────────────────────────────────

def toggle_startup_item(name: str, action: str, scope: str, location: str,
                        source: str = "Registry", key: str = ""):
    """Enable or disable a startup item (see diagnostics.startup)."""
    if action not in ("enable", "disable"):
        return {"success": False, "error": "Action must be 'enable' or 'disable'."}
    return startup.set_startup_enabled(name, action == "enable", scope, location, source, key)
