"""
Storage Analyzer Module
Scans drives and directories to find what is consuming disk space.
"""

import os
import psutil
import time
from collections import defaultdict
from pathlib import Path


def get_drive_usage():
    """Get usage statistics for all mounted disk partitions."""
    drives = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            drives.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "fstype": partition.fstype,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent_used": usage.percent,
                "total_display": _format_bytes(usage.total),
                "used_display": _format_bytes(usage.used),
                "free_display": _format_bytes(usage.free),
            })
        except (PermissionError, OSError):
            continue
    return drives


def get_large_files(path: str, top_n: int = 30, min_size_mb: float = 50):
    """Find the largest files under a given path. Stops after 25s to avoid timeouts."""
    large_files = []
    min_size_bytes = min_size_mb * 1024 * 1024
    start_time = time.time()
    TIME_LIMIT = 25  # seconds

    try:
        for root, dirs, files in os.walk(path):
            if time.time() - start_time > TIME_LIMIT:
                break
            # Skip system and protected directories
            dirs[:] = [d for d in dirs if d not in (
                '$Recycle.Bin', 'System Volume Information',
                'Windows', 'ProgramData', '$WinREAgent',
                'Recovery', 'pagefile.sys', '.git',
                'node_modules', '__pycache__', '.cache'
            )]
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    size = os.path.getsize(fp)
                    if size >= min_size_bytes:
                        large_files.append({
                            "path": fp,
                            "name": f,
                            "size_bytes": size,
                            "size_display": _format_bytes(size),
                            "extension": os.path.splitext(f)[1].lower(),
                            "modified": os.path.getmtime(fp),
                        })
                except (PermissionError, OSError, FileNotFoundError):
                    continue
    except (PermissionError, OSError):
        pass

    large_files.sort(key=lambda x: x["size_bytes"], reverse=True)
    return large_files[:top_n]


def get_folder_sizes(path: str, depth: int = 1):
    """Get the size of top-level folders. Uses fast estimation with time limit."""
    folder_sizes = []
    start_time = time.time()
    TIME_LIMIT = 25  # seconds

    try:
        root_path = Path(path)
        folders = []
        for item in root_path.iterdir():
            if item.is_dir():
                folders.append(item)

        for item in folders:
            if time.time() - start_time > TIME_LIMIT:
                break
            try:
                total = _dir_size_fast(str(item), start_time, TIME_LIMIT)
                folder_sizes.append({
                    "path": str(item),
                    "name": item.name,
                    "size_bytes": total,
                    "size_display": _format_bytes(total),
                })
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    folder_sizes.sort(key=lambda x: x["size_bytes"], reverse=True)
    return folder_sizes[:25]


def get_file_type_breakdown(path: str):
    """Break down disk usage by file extension/type."""
    type_map = defaultdict(lambda: {"count": 0, "size_bytes": 0})
    category_map = {
        ".mp4": "Video", ".mkv": "Video", ".avi": "Video", ".mov": "Video", ".wmv": "Video", ".flv": "Video",
        ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio", ".ogg": "Audio", ".wma": "Audio",
        ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image", ".bmp": "Image", ".svg": "Image",
        ".webp": "Image", ".ico": "Image", ".tiff": "Image",
        ".zip": "Archive", ".rar": "Archive", ".7z": "Archive", ".tar": "Archive", ".gz": "Archive",
        ".iso": "Disk Image", ".img": "Disk Image", ".vhd": "Disk Image", ".vmdk": "Disk Image",
        ".exe": "Executable", ".msi": "Executable", ".dll": "Library",
        ".pdf": "Document", ".doc": "Document", ".docx": "Document", ".xls": "Document",
        ".xlsx": "Document", ".ppt": "Document", ".pptx": "Document", ".txt": "Document",
        ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code", ".css": "Code",
        ".java": "Code", ".cpp": "Code", ".c": "Code", ".cs": "Code", ".go": "Code",
        ".log": "Log", ".tmp": "Temporary", ".bak": "Backup", ".cache": "Cache",
    }

    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in (
                '$Recycle.Bin', 'System Volume Information',
                'Windows', '$WinREAgent', 'Recovery'
            )]
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    size = os.path.getsize(fp)
                    ext = os.path.splitext(f)[1].lower()
                    category = category_map.get(ext, "Other")
                    type_map[category]["count"] += 1
                    type_map[category]["size_bytes"] += size
                except (PermissionError, OSError, FileNotFoundError):
                    continue
    except (PermissionError, OSError):
        pass

    result = []
    for category, data in type_map.items():
        result.append({
            "category": category,
            "count": data["count"],
            "size_bytes": data["size_bytes"],
            "size_display": _format_bytes(data["size_bytes"]),
        })
    result.sort(key=lambda x: x["size_bytes"], reverse=True)
    return result


def get_temp_files():
    """Find temporary and cache files that can potentially be cleaned."""
    temp_dirs = []
    paths_to_check = [
        os.environ.get("TEMP", ""),
        os.environ.get("TMP", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Temp"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "INetCache"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data", "Default", "Cache"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "pip", "cache"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm-cache"),
    ]

    seen = set()
    for p in paths_to_check:
        if not p or p in seen or not os.path.isdir(p):
            continue
        seen.add(p)
        try:
            total = _dir_size(p)
            temp_dirs.append({
                "path": p,
                "name": os.path.basename(p) or p,
                "size_bytes": total,
                "size_display": _format_bytes(total),
                "can_clean": True,
            })
        except (PermissionError, OSError):
            continue

    temp_dirs.sort(key=lambda x: x["size_bytes"], reverse=True)
    return temp_dirs


def _dir_size(path: str) -> int:
    """Calculate total size of a directory recursively."""
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except (PermissionError, OSError, FileNotFoundError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def _dir_size_fast(path: str, global_start: float, time_limit: float) -> int:
    """Calculate directory size with a global time limit to avoid hangs."""
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            if time.time() - global_start > time_limit:
                break
            dirs[:] = [d for d in dirs if d not in (
                '$Recycle.Bin', 'System Volume Information'
            )]
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except (PermissionError, OSError, FileNotFoundError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def _format_bytes(size: int) -> str:
    """Convert bytes to a human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
