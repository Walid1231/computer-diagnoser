"""
Storage Analyzer Module
Scans drives and directories to find what is consuming disk space, finds
cleanable temp/cache folders, and performs (guarded) deletions.

Performance notes
-----------------
* One parallel pass (os.scandir + DirEntry.stat, which is free on Windows) now
  produces the file-type breakdown, folder sizes and large-file list together.
  The old code walked the drive three separate times with a stat() per file.
* Junctions / symlinks are never followed (no loops, no double counting), and
  cloud-only placeholder files (OneDrive "files on demand") are ignored so a scan
  can never trigger a download.
"""

import glob
import heapq
import os
import stat
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import psutil

from diagnostics.utils import format_bytes, is_within, normalize_path, ttl_cache

_format_bytes = format_bytes

# ─── CONSTANTS ───────────────────────────────────────────────

CATEGORY_MAP = {
    ".mp4": "Video", ".mkv": "Video", ".avi": "Video", ".mov": "Video", ".wmv": "Video", ".flv": "Video",
    ".webm": "Video", ".m4v": "Video",
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio", ".ogg": "Audio", ".wma": "Audio",
    ".m4a": "Audio",
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image", ".bmp": "Image", ".svg": "Image",
    ".webp": "Image", ".ico": "Image", ".tiff": "Image", ".heic": "Image", ".raw": "Image", ".psd": "Image",
    ".zip": "Archive", ".rar": "Archive", ".7z": "Archive", ".tar": "Archive", ".gz": "Archive",
    ".iso": "Disk Image", ".img": "Disk Image", ".vhd": "Disk Image", ".vhdx": "Disk Image",
    ".vmdk": "Disk Image",
    ".exe": "Executable", ".msi": "Executable", ".dll": "Library",
    ".pdf": "Document", ".doc": "Document", ".docx": "Document", ".xls": "Document",
    ".xlsx": "Document", ".ppt": "Document", ".pptx": "Document", ".txt": "Document",
    ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code", ".css": "Code",
    ".java": "Code", ".cpp": "Code", ".c": "Code", ".cs": "Code", ".go": "Code",
    ".log": "Log", ".tmp": "Temporary", ".bak": "Backup", ".cache": "Cache",
}

# Never descended into at all (recycle bin, system restore data...).
SKIP_ALWAYS = {"$recycle.bin", "system volume information", "$winreagent", "$sysreset", "config.msi"}

# Still counted for sizes, but never offered as "large files you could delete".
LARGE_SKIP_TOP = {"windows", "programdata", "program files", "program files (x86)",
                  "recovery", "$windows.~bt", "$windows.~ws"}
LARGE_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache"}
LARGE_SKIP_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys"}

_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # directory junction
_IO_REPARSE_TAG_SYMLINK = 0xA000000C
# OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS: file content lives in the cloud
_CLOUD_ONLY_ATTRIBUTES = 0x1000 | 0x40000 | 0x400000


# ─── LOW-LEVEL HELPERS ───────────────────────────────────────

def _is_dir_link(entry) -> bool:
    """True for symlinks and junctions (which must not be followed)."""
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
        return getattr(st, "st_reparse_tag", 0) in (_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK)
    except OSError:
        return True


def _is_cloud_only(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY_ATTRIBUTES)


def _remove_file(path: str) -> bool:
    """Delete a file, clearing the read-only flag if needed. True on success."""
    try:
        os.remove(path)
        return True
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return True
        except OSError:
            return False
    except OSError:
        return False


# ─── DRIVES ──────────────────────────────────────────────────

def get_drive_usage():
    """Get usage statistics for all mounted disk partitions."""
    drives = []
    for partition in psutil.disk_partitions(all=False):
        opts = (partition.opts or "").lower()
        if "cdrom" in opts:  # empty optical drives raise errors / can block
            continue
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            continue
        drives.append({
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "fstype": partition.fstype,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent_used": usage.percent,
            "total_display": format_bytes(usage.total),
            "used_display": format_bytes(usage.used),
            "free_display": format_bytes(usage.free),
        })
    return drives


# ─── SCANNING ────────────────────────────────────────────────

def _scan_tree(top: str, deadline: float, min_bytes: int, top_n: int, large_ok: bool, recurse: bool):
    """Scan one directory. Returns (bytes, files, {category: [count, bytes]}, [large], partial)."""
    total = 0
    files = 0
    types = defaultdict(lambda: [0, 0])
    heap = []  # min-heap of (size, path, mtime): keeps only the top_n largest
    partial = False
    stack = [(top, large_ok)]

    while stack:
        if time.monotonic() > deadline:
            partial = True
            break
        directory, may_list_large = stack.pop()
        try:
            iterator = os.scandir(directory)
        except OSError:
            continue
        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        lowered = entry.name.lower()
                        if recurse and lowered not in SKIP_ALWAYS and not _is_dir_link(entry):
                            stack.append((entry.path, may_list_large and lowered not in LARGE_SKIP_DIRS))
                        continue
                    if entry.is_symlink():
                        continue  # a link holds no data of its own; counting it would skew totals
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_cloud_only(st):
                    continue

                size = st.st_size
                total += size
                files += 1
                category = CATEGORY_MAP.get(os.path.splitext(entry.name)[1].lower(), "Other")
                bucket = types[category]
                bucket[0] += 1
                bucket[1] += size

                if may_list_large and size >= min_bytes and entry.name.lower() not in LARGE_SKIP_FILES:
                    if len(heap) < top_n:
                        heapq.heappush(heap, (size, entry.path, st.st_mtime))
                    elif size > heap[0][0]:
                        heapq.heapreplace(heap, (size, entry.path, st.st_mtime))

    return total, files, types, heap, partial


def _plan_tasks(path: str):
    """Split a scan into independent pieces so threads can work in parallel.

    Returns (top_entries, tasks, root_task) where each task is
    (top_index_or_None, directory, may_list_large, recurse).
    """
    # A drive root counts as "protected" for deletion, but scanning it must still work.
    path_is_protected = not _is_drive_root(path) and is_protected_path(path)
    top_entries = []
    tasks = []
    try:
        with os.scandir(path) as iterator:
            children = list(iterator)
    except OSError:
        return top_entries, tasks

    # Files sitting directly in the scanned folder.
    tasks.append((None, path, not path_is_protected, False))

    for child in children:
        try:
            if not child.is_dir(follow_symlinks=False):
                continue
            lowered = child.name.lower()
            if lowered in SKIP_ALWAYS or _is_dir_link(child):
                continue
        except OSError:
            continue

        index = len(top_entries)
        top_entries.append(child)
        large_ok = not path_is_protected and lowered not in LARGE_SKIP_TOP and lowered not in LARGE_SKIP_DIRS
        # Files directly inside the top-level folder, then each sub-folder separately.
        tasks.append((index, child.path, large_ok, False))
        try:
            with os.scandir(child.path) as sub_iterator:
                for sub in sub_iterator:
                    try:
                        if not sub.is_dir(follow_symlinks=False):
                            continue
                        sub_name = sub.name.lower()
                        if sub_name in SKIP_ALWAYS or _is_dir_link(sub):
                            continue
                        tasks.append((index, sub.path, large_ok and sub_name not in LARGE_SKIP_DIRS, True))
                    except OSError:
                        continue
        except OSError:
            pass
    return top_entries, tasks


@ttl_cache(60)
def scan_path(path: str, top_n: int = 30, min_size_mb: float = 50, time_limit: float = 60):
    """One parallel pass: file types, top-level folder sizes and the largest files."""
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError("Folder not found.")

    started = time.monotonic()
    deadline = started + time_limit
    min_bytes = int(min_size_mb * 1024 * 1024)
    top_entries, tasks = _plan_tasks(path)

    folder_totals = defaultdict(int)
    type_totals = defaultdict(lambda: [0, 0])
    large = []
    files_scanned = 0
    total_bytes = 0
    partial = False

    def run(task):
        index, directory, large_ok, recurse = task
        return index, _scan_tree(directory, deadline, min_bytes, top_n, large_ok, recurse)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, (size, files, types, heap, was_partial) in pool.map(run, tasks):
            total_bytes += size
            files_scanned += files
            partial = partial or was_partial
            if index is not None:
                folder_totals[index] += size
            for category, (count, nbytes) in types.items():
                type_totals[category][0] += count
                type_totals[category][1] += nbytes
            large.extend(heap)

    folders = [{
        "path": top_entries[i].path,
        "name": top_entries[i].name,
        "size_bytes": size,
        "size_display": format_bytes(size),
    } for i, size in folder_totals.items()]
    folders.sort(key=lambda f: f["size_bytes"], reverse=True)

    file_types = [{
        "category": category,
        "count": count,
        "size_bytes": nbytes,
        "size_display": format_bytes(nbytes),
    } for category, (count, nbytes) in type_totals.items()]
    file_types.sort(key=lambda t: t["size_bytes"], reverse=True)

    large_files = [{
        "path": file_path,
        "name": os.path.basename(file_path),
        "size_bytes": size,
        "size_display": format_bytes(size),
        "extension": os.path.splitext(file_path)[1].lower(),
        "modified": mtime,
    } for size, file_path, mtime in heapq.nlargest(top_n, large)]

    return {
        "path": path,
        "file_types": file_types,
        "folders": folders[:25],
        "large_files": large_files,
        "files_scanned": files_scanned,
        "total_bytes": total_bytes,
        "total_display": format_bytes(total_bytes),
        "partial": partial,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


def get_large_files(path: str, top_n: int = 30, min_size_mb: float = 50, time_limit: float = 25):
    """Find the largest files under a path (system folders excluded)."""
    return scan_path(path, top_n, min_size_mb, time_limit)["large_files"]


def get_folder_sizes(path: str, depth: int = 1):
    """Size of each top-level folder under `path`."""
    return scan_path(path)["folders"]


def get_file_type_breakdown(path: str):
    """Break down disk usage by file category."""
    return scan_path(path)["file_types"]


# ─── TEMP / CACHE TARGETS ────────────────────────────────────

def cleanup_targets():
    """All folders the app is allowed to empty.

    This list is also the *whitelist* for the clean-folder API: nothing outside it
    can be cleaned, however the request is crafted.
    `boost` marks the ones that are safe to clear during a one-click boost.
    """
    env = os.environ.get
    local = env("LOCALAPPDATA", "")
    windir = env("WINDIR") or env("SystemRoot") or ""

    candidates = []  # (name, path, boost, needs_admin)

    def add(name, path, boost=False, admin=False):
        if path:
            candidates.append((name, path, boost, admin))

    add("User Temp", env("TEMP", ""), boost=True)
    add("User Temp (TMP)", env("TMP", ""), boost=True)
    if local:
        add("Local Temp", os.path.join(local, "Temp"), boost=True)
        add("Internet Cache", os.path.join(local, "Microsoft", "Windows", "INetCache"), boost=True)
        add("Crash Dumps", os.path.join(local, "CrashDumps"), boost=True)
        add("DirectX Shader Cache", os.path.join(local, "D3DSCache"))
        add("pip Cache", os.path.join(local, "pip", "Cache"))
        add("npm Cache", os.path.join(local, "npm-cache"))
        for browser, rel in (("Chrome", ("Google", "Chrome", "User Data")),
                             ("Edge", ("Microsoft", "Edge", "User Data"))):
            base = os.path.join(local, *rel)
            for profile in sorted(glob.glob(os.path.join(base, "Default")) +
                                  glob.glob(os.path.join(base, "Profile *"))):
                label = os.path.basename(profile)
                add(f"{browser} Cache ({label})", os.path.join(profile, "Cache"))
                add(f"{browser} Code Cache ({label})", os.path.join(profile, "Code Cache"))
        for profile in glob.glob(os.path.join(local, "Mozilla", "Firefox", "Profiles", "*")):
            add("Firefox Cache", os.path.join(profile, "cache2"))
    if windir:
        add("Windows Temp", os.path.join(windir, "Temp"), admin=True)
        add("Windows Update Downloads", os.path.join(windir, "SoftwareDistribution", "Download"), admin=True)

    targets = []
    seen = set()
    for name, path, boost, admin in candidates:
        if not os.path.isdir(path):
            continue
        key = normalize_path(path)  # TEMP and LOCALAPPDATA\Temp are usually the same folder
        if key in seen:
            continue
        seen.add(key)
        targets.append({"name": name, "path": path, "boost": boost, "needs_admin": admin})
    return targets


def _dir_size(path: str, deadline: float = None) -> int:
    """Total size of a directory tree (links and cloud placeholders excluded)."""
    return _scan_tree(path, deadline or (time.monotonic() + 3600), 1 << 62, 1, False, True)[0]


@ttl_cache(30)
def get_temp_files():
    """Find temporary and cache folders that can be cleaned, with their sizes."""
    targets = cleanup_targets()
    deadline = time.monotonic() + 20
    with ThreadPoolExecutor(max_workers=6) as pool:
        sizes = list(pool.map(lambda t: _dir_size(t["path"], deadline), targets))

    results = []
    for target, size in zip(targets, sizes):
        if size <= 0:
            continue
        results.append({
            "path": target["path"],
            "name": target["name"],
            "size_bytes": size,
            "size_display": format_bytes(size),
            "can_clean": True,
            "needs_admin": target["needs_admin"],
        })
    results.sort(key=lambda t: t["size_bytes"], reverse=True)
    return results


def invalidate_caches():
    """Drop cached scan results (call after anything was deleted)."""
    scan_path.cache_clear()
    get_temp_files.cache_clear()


# ─── SAFE DELETION ───────────────────────────────────────────

def _protected_roots():
    roots = []
    for var in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        value = os.environ.get(var)
        if value:
            roots.append(value)
    if os.name == "nt":
        drive = os.environ.get("SystemDrive", "C:")
        roots.extend([
            drive + "\\Windows", drive + "\\Program Files", drive + "\\Program Files (x86)",
            drive + "\\ProgramData", drive + "\\Recovery", drive + "\\Users\\Default",
            drive + "\\Boot", drive + "\\EFI",
        ])
    return {normalize_path(r) for r in roots}


_PROTECTED_DIR_NAMES = {"$recycle.bin", "system volume information", "$winreagent"}


def _is_drive_root(path: str) -> bool:
    _, rest = os.path.splitdrive(normalize_path(path))
    return rest in ("", os.sep, "/")


def is_protected_path(path: str) -> bool:
    """True for system locations the app must never delete from."""
    normalized = normalize_path(path)
    if _is_drive_root(normalized):
        return True
    parts = {part.lower() for part in normalized.replace("/", "\\").split("\\")}
    if parts & _PROTECTED_DIR_NAMES:
        return True
    return any(is_within(normalized, root) for root in _protected_roots())


def delete_file(path: str) -> dict:
    """Permanently delete one file, refusing protected system locations."""
    target = os.path.abspath(path)
    if is_protected_path(target):
        return {"success": False, "error": "Cannot delete files in a protected system location."}
    if not os.path.isfile(target):
        return {"success": False, "error": "File not found."}
    try:
        size = os.path.getsize(target)
    except OSError:
        size = 0
    if not _remove_file(target):
        return {"success": False, "error": "Permission denied. The file may be in use."}
    invalidate_caches()
    return {
        "success": True,
        "message": f"Deleted: {os.path.basename(target)}",
        "freed": format_bytes(size),
        "freed_bytes": size,
    }


def clean_directory_contents(path: str) -> dict:
    """Delete everything inside `path`, keeping the folder itself.

    Counts only what was really removed (files in use are skipped and reported),
    never follows junctions, and clears read-only flags.
    """
    freed = 0
    deleted = 0
    errors = 0
    directories = []

    stack = [path]
    first = True
    while stack:
        directory = stack.pop()
        if not first:
            directories.append(directory)
        first = False
        try:
            iterator = os.scandir(directory)
        except OSError:
            errors += 1
            continue
        with iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if _is_dir_link(entry):
                            try:
                                os.rmdir(entry.path)  # removes the link, never the target
                                deleted += 1
                            except OSError:
                                errors += 1
                        else:
                            stack.append(entry.path)
                        continue
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    if _remove_file(entry.path):
                        freed += size
                        deleted += 1
                    else:
                        errors += 1
                except OSError:
                    errors += 1

    for directory in reversed(directories):  # children before parents
        try:
            os.rmdir(directory)
        except OSError:
            pass  # not empty because something inside was locked

    invalidate_caches()
    return {"freed_bytes": freed, "freed": format_bytes(freed), "deleted": deleted, "errors": errors}


def clean_target(path: str) -> dict:
    """Clean one whitelisted cache/temp folder (see cleanup_targets)."""
    requested = normalize_path(path)
    allowed = {normalize_path(t["path"]): t for t in cleanup_targets()}
    target = allowed.get(requested)
    if target is None:
        return {"success": False, "error": "This folder is not on the list of safe-to-clean locations."}

    result = clean_directory_contents(target["path"])
    return {
        "success": True,
        "message": f"Cleaned: {target['name']}",
        **result,
    }
