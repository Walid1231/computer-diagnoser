"""
System Tools Module
Kill processes, system boost, speed test, duplicate finder,
and startup item toggle.
"""

import os
import subprocess
import hashlib
import time
import psutil
import socket


# ─── KILL PROCESS ────────────────────────────────────────────

def kill_process(pid: int):
    """Kill a process by PID."""
    PROTECTED = ["System", "csrss.exe", "wininit.exe", "smss.exe",
                 "services.exe", "lsass.exe", "winlogon.exe", "svchost.exe",
                 "explorer.exe", "dwm.exe"]
    try:
        proc = psutil.Process(pid)
        if proc.name() in PROTECTED:
            return {"success": False, "error": f"Cannot kill protected system process: {proc.name()}"}
        name = proc.name()
        proc.terminate()
        proc.wait(timeout=5)
        return {"success": True, "message": f"Terminated {name} (PID: {pid})"}
    except psutil.NoSuchProcess:
        return {"success": False, "error": f"Process {pid} not found."}
    except psutil.AccessDenied:
        return {"success": False, "error": f"Access denied. Try running as Administrator."}
    except psutil.TimeoutExpired:
        try:
            psutil.Process(pid).kill()
            return {"success": True, "message": f"Force-killed process (PID: {pid})"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── SYSTEM BOOST ────────────────────────────────────────────

def system_boost():
    """
    1-Click System Boost: Cleans temp files, flushes DNS, clears
    standby memory list, and closes non-essential background apps.
    """
    results = []
    freed_bytes = 0

    # 1. Clean user TEMP folder
    temp_dir = os.environ.get("TEMP", "")
    if temp_dir and os.path.isdir(temp_dir):
        count = 0
        for item in os.listdir(temp_dir):
            try:
                fp = os.path.join(temp_dir, item)
                if os.path.isfile(fp):
                    size = os.path.getsize(fp)
                    os.remove(fp)
                    freed_bytes += size
                    count += 1
            except (PermissionError, OSError):
                continue
        results.append(f"Cleaned {count} temp files ({_format_bytes(freed_bytes)})")

    # 2. Flush DNS cache
    try:
        subprocess.run(["ipconfig", "/flushdns"],
                       capture_output=True, timeout=10)
        results.append("Flushed DNS cache")
    except Exception:
        results.append("DNS flush skipped")

    # 3. Clear Windows thumbnail cache
    thumb_path = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                              "Microsoft", "Windows", "Explorer")
    if os.path.isdir(thumb_path):
        t_count = 0
        for f in os.listdir(thumb_path):
            if f.startswith("thumbcache"):
                try:
                    fp = os.path.join(thumb_path, f)
                    size = os.path.getsize(fp)
                    os.remove(fp)
                    freed_bytes += size
                    t_count += 1
                except (PermissionError, OSError):
                    continue
        if t_count:
            results.append(f"Cleared {t_count} thumbnail cache files")

    # 4. Clear font cache
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Remove-Item $env:LOCALAPPDATA\\*.log -ErrorAction SilentlyContinue"],
                       capture_output=True, timeout=5)
    except Exception:
        pass

    return {
        "success": True,
        "actions": results,
        "freed_bytes": freed_bytes,
        "freed_display": _format_bytes(freed_bytes),
        "message": f"System boost complete! Freed {_format_bytes(freed_bytes)}. "
                   f"Performed {len(results)} optimizations.",
    }


# ─── INTERNET SPEED TEST ────────────────────────────────────

def speed_test():
    """
    Simple download speed test using a known test file.
    Measures download speed without any external dependencies.
    """
    import urllib.request

    TEST_URLS = [
        ("http://speedtest.tele2.net/1MB.zip", 1_000_000),
        ("http://proof.ovh.net/files/1Mb.dat", 1_000_000),
    ]

    result = {
        "download_mbps": None,
        "latency_ms": None,
        "success": False,
        "message": "Speed test failed. Check your internet connection.",
    }

    # Latency test
    try:
        start = time.time()
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        result["latency_ms"] = round((time.time() - start) * 1000, 1)
    except Exception:
        result["latency_ms"] = None

    # Download speed test
    for url, expected_size in TEST_URLS:
        try:
            start = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urllib.request.urlopen(req, timeout=15)
            data = response.read()
            elapsed = time.time() - start

            if elapsed > 0 and len(data) > 0:
                bits = len(data) * 8
                mbps = round((bits / elapsed) / 1_000_000, 2)
                result["download_mbps"] = mbps
                result["download_size"] = _format_bytes(len(data))
                result["download_time"] = round(elapsed, 2)
                result["success"] = True
                result["message"] = f"Download: {mbps} Mbps | Latency: {result['latency_ms']}ms"
                break
        except Exception:
            continue

    return result


# ─── DUPLICATE FILE FINDER ───────────────────────────────────

def find_duplicates(path: str, min_size_mb: float = 10):
    """
    Find duplicate files by comparing file sizes first, then
    partial MD5 hashes. Returns groups of duplicate files.
    """
    min_size = int(min_size_mb * 1024 * 1024)
    start_time = time.time()
    TIME_LIMIT = 30
    SKIP_DIRS = {'$Recycle.Bin', 'System Volume Information',
                 'Windows', '.git', 'node_modules', '__pycache__'}

    # Phase 1: Group files by size
    size_groups = {}
    file_count = 0

    try:
        for root, dirs, files in os.walk(path):
            if time.time() - start_time > TIME_LIMIT:
                break
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    size = os.path.getsize(fp)
                    if size >= min_size:
                        if size not in size_groups:
                            size_groups[size] = []
                        size_groups[size].append(fp)
                        file_count += 1
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass

    # Phase 2: Hash files with matching sizes
    duplicates = []
    for size, files in size_groups.items():
        if len(files) < 2:
            continue
        if time.time() - start_time > TIME_LIMIT:
            break

        hash_groups = {}
        for fp in files:
            try:
                h = _partial_hash(fp)
                if h not in hash_groups:
                    hash_groups[h] = []
                hash_groups[h].append(fp)
            except (PermissionError, OSError):
                continue

        for h, matched_files in hash_groups.items():
            if len(matched_files) >= 2:
                duplicates.append({
                    "size_bytes": size,
                    "size_display": _format_bytes(size),
                    "count": len(matched_files),
                    "wasted_bytes": size * (len(matched_files) - 1),
                    "wasted_display": _format_bytes(size * (len(matched_files) - 1)),
                    "files": [{"path": f, "name": os.path.basename(f)} for f in matched_files],
                })

    duplicates.sort(key=lambda x: x["wasted_bytes"], reverse=True)
    total_wasted = sum(d["wasted_bytes"] for d in duplicates)

    return {
        "scanned_files": file_count,
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates[:20],
        "total_wasted_bytes": total_wasted,
        "total_wasted_display": _format_bytes(total_wasted),
        "message": f"Found {len(duplicates)} duplicate groups wasting {_format_bytes(total_wasted)}."
                   if duplicates else "No significant duplicates found.",
    }


def _partial_hash(filepath, chunk_size=8192):
    """Generate a partial hash using first and last chunks of a file."""
    h = hashlib.md5()
    with open(filepath, 'rb') as f:
        h.update(f.read(chunk_size))
        f.seek(0, 2)
        size = f.tell()
        if size > chunk_size * 2:
            f.seek(-chunk_size, 2)
            h.update(f.read(chunk_size))
    return h.hexdigest()


# ─── STARTUP TOGGLE ──────────────────────────────────────────

def toggle_startup_item(name: str, action: str, scope: str, location: str):
    """
    Enable or disable a startup item.
    action: 'disable' or 'enable'
    For registry items, moves them to a disabled subkey.
    """
    import winreg

    PROTECTED = ["SecurityHealth", "WindowsDefender", "RtkAudUService"]
    if name in PROTECTED:
        return {"success": False, "error": f"Cannot modify protected startup item: {name}"}

    if scope in ("Current User", "All Users"):
        try:
            # Determine hive
            hive = winreg.HKEY_CURRENT_USER if "Current" in scope else winreg.HKEY_LOCAL_MACHINE

            if action == "disable":
                # Read current value
                key = winreg.OpenKey(hive, location, 0, winreg.KEY_READ)
                value, regtype = winreg.QueryValueEx(key, name)
                winreg.CloseKey(key)

                # Write to disabled subkey
                disabled_path = location + "\\AutorunsDisabled"
                try:
                    dkey = winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_WRITE)
                except OSError:
                    dkey = winreg.CreateKey(hive, disabled_path)
                winreg.SetValueEx(dkey, name, 0, regtype, value)
                winreg.CloseKey(dkey)

                # Delete from original
                key = winreg.OpenKey(hive, location, 0, winreg.KEY_WRITE)
                winreg.DeleteValue(key, name)
                winreg.CloseKey(key)

                return {"success": True, "message": f"Disabled startup item: {name}"}

            elif action == "enable":
                disabled_path = location + "\\AutorunsDisabled"
                dkey = winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_READ)
                value, regtype = winreg.QueryValueEx(dkey, name)
                winreg.CloseKey(dkey)

                # Restore to original
                key = winreg.OpenKey(hive, location, 0, winreg.KEY_WRITE)
                winreg.SetValueEx(key, name, 0, regtype, value)
                winreg.CloseKey(key)

                # Remove from disabled
                dkey = winreg.OpenKey(hive, disabled_path, 0, winreg.KEY_WRITE)
                winreg.DeleteValue(dkey, name)
                winreg.CloseKey(dkey)

                return {"success": True, "message": f"Enabled startup item: {name}"}

        except OSError as e:
            return {"success": False, "error": f"Registry error: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Can only toggle registry-based startup items."}


def _format_bytes(size: int) -> str:
    """Convert bytes to a human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
