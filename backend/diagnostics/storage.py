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

from diagnostics import classifier
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


# ─── EXPLORER: "what is inside Other?" ───────────────────────
# Opening a category (or any folder) shows where its bytes live and whether each part is
# needed. Every folder is classified once (cached), and each file reuses its folder's verdict.

CATEGORIES = set(CATEGORY_MAP.values()) | {"Other"}
_GROUP_DEPTH = 3     # locations are grouped to 3 folder levels below the scanned root
_USERS_GROUP_DEPTH = 5
_VERDICTS = (classifier.SAFE, classifier.REVIEW, classifier.KEEP)
_VERDICT_SLOT = {classifier.SAFE: 2, classifier.REVIEW: 3, classifier.KEEP: 4}


def _group_key(root: str, directory: str) -> str:
    """The location a directory is reported under (root + up to _GROUP_DEPTH levels)."""
    root = root.rstrip("\\/")
    rest = directory[len(root):].strip("\\/")
    if not rest:
        return root + os.sep
    parts = rest.split(os.sep)
    # Inside C:\Users the interesting level is deeper (Users\<name>\AppData\Local\<app>).
    depth = _USERS_GROUP_DEPTH if parts[0].lower() == "users" else _GROUP_DEPTH
    return os.path.join(root, *parts[:depth])


def _scan_category(top: str, root: str, deadline: float, category: str, recurse: bool, top_n: int):
    """Scan one directory for files of `category`. Slots: [count, bytes, safe, review, keep]."""
    totals = [0, 0, 0, 0, 0]
    extensions = defaultdict(lambda: [0, 0, 0, 0, 0])
    locations = defaultdict(lambda: [0, 0, 0, 0, 0])
    heap = []
    partial = False
    stack = [top]

    while stack:
        if time.monotonic() > deadline:
            partial = True
            break
        directory = stack.pop()
        try:
            iterator = os.scandir(directory)
        except OSError:
            continue
        dir_verdict = classifier.classify_dir(directory)[0]
        location = locations[_group_key(root, directory)]
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
                        if recurse and entry.name.lower() not in SKIP_ALWAYS and not _is_dir_link(entry):
                            stack.append(entry.path)
                        continue
                    if entry.is_symlink():
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    if CATEGORY_MAP.get(ext, "Other") != category:
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_cloud_only(st):
                    continue

                size = st.st_size
                slot = _VERDICT_SLOT[classifier.file_verdict(dir_verdict, entry.name, ext)]
                for bucket in (totals, extensions[ext], location):
                    bucket[0] += 1
                    bucket[1] += size
                    bucket[slot] += size

                if len(heap) < top_n:
                    heapq.heappush(heap, (size, entry.path, st.st_mtime))
                elif size > heap[0][0]:
                    heapq.heapreplace(heap, (size, entry.path, st.st_mtime))

    return totals, extensions, locations, heap, partial


def _dominant(bucket) -> str:
    """The verdict that covers the most bytes of a [count, bytes, safe, review, keep] bucket."""
    return max(_VERDICTS, key=lambda v: bucket[_VERDICT_SLOT[v]])


def _can_clean_folder(path: str, info: dict = None) -> bool:
    """True when emptying this folder is a supported, safe one-click action."""
    key = normalize_path(path)
    if any(normalize_path(t["path"]) == key for t in cleanup_targets()):
        return True
    info = info or classifier.classify(path, True)
    return bool(info["verdict"] == classifier.SAFE and info["cleanable"] and not is_protected_path(path))


def _bytes_info(bucket) -> dict:
    return {
        "count": bucket[0],
        "size_bytes": bucket[1],
        "size_display": format_bytes(bucket[1]),
        "safe_bytes": bucket[2],
        "review_bytes": bucket[3],
        "keep_bytes": bucket[4],
    }


@ttl_cache(60)
def explore_category(path: str, category: str, top_n: int = 40, time_limit: float = 60):
    """Break one file category (e.g. "Other") down by location, extension and biggest files,
    marking each part safe / review / keep."""
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError("Folder not found.")
    if category not in CATEGORIES:
        raise ValueError("Unknown category.")

    started = time.monotonic()
    deadline = started + time_limit
    _, tasks = _plan_tasks(path)

    totals = [0, 0, 0, 0, 0]
    extensions = defaultdict(lambda: [0, 0, 0, 0, 0])
    locations = defaultdict(lambda: [0, 0, 0, 0, 0])
    heap = []
    partial = False

    def run(task):
        _, directory, _large_ok, recurse = task
        return _scan_category(directory, path, deadline, category, recurse, top_n)

    def merge(target, source):
        for i in range(5):
            target[i] += source[i]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for t_totals, t_exts, t_locs, t_heap, was_partial in pool.map(run, tasks):
            merge(totals, t_totals)
            for key, bucket in t_exts.items():
                merge(extensions[key], bucket)
            for key, bucket in t_locs.items():
                if bucket[0]:
                    merge(locations[key], bucket)
            heap.extend(t_heap)
            partial = partial or was_partial

    location_rows = []
    for key, bucket in locations.items():
        if not bucket[0]:
            continue
        info = classifier.classify(key, True)
        location_rows.append({
            "path": key,
            "name": os.path.basename(key.rstrip("\\/")) or key,
            **_bytes_info(bucket),
            "verdict": _dominant(bucket),
            "label": info["label"],
            "reason": info["reason"],
            "can_clean": _can_clean_folder(key, info),
        })
    location_rows.sort(key=lambda r: r["size_bytes"], reverse=True)

    extension_rows = [{
        "extension": ext or "(no extension)",
        **_bytes_info(bucket),
        "verdict": _dominant(bucket),
    } for ext, bucket in extensions.items()]
    extension_rows.sort(key=lambda r: r["size_bytes"], reverse=True)

    file_rows = []
    for size, file_path, mtime in heapq.nlargest(top_n, heap):
        info = classifier.classify(file_path, False)
        file_rows.append({
            "path": file_path,
            "name": os.path.basename(file_path),
            "is_dir": False,
            "size_bytes": size,
            "size_display": format_bytes(size),
            "extension": os.path.splitext(file_path)[1].lower(),
            "modified": mtime,
            "verdict": info["verdict"],
            "label": info["label"],
            "reason": info["reason"],
            "can_delete": info["verdict"] != classifier.KEEP and not is_protected_path(file_path),
        })

    summary = {verdict: {"size_bytes": totals[_VERDICT_SLOT[verdict]],
                         "size_display": format_bytes(totals[_VERDICT_SLOT[verdict]])}
               for verdict in _VERDICTS}
    return {
        "path": path,
        "category": category,
        "files": totals[0],
        "total_bytes": totals[1],
        "total_display": format_bytes(totals[1]),
        "summary": summary,
        "locations": location_rows[:30],
        "extensions": extension_rows[:30],
        "largest_files": file_rows,
        "partial": partial,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


@ttl_cache(30)
def browse_folder(path: str, time_limit: float = 30):
    """List one folder's contents (folders sized recursively) with a verdict for each item."""
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError("Folder not found.")

    started = time.monotonic()
    deadline = started + time_limit
    folders, files = [], []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() in SKIP_ALWAYS or _is_dir_link(entry):
                            continue
                        folders.append(entry)
                    elif not entry.is_symlink():
                        st = entry.stat(follow_symlinks=False)
                        if not _is_cloud_only(st):
                            files.append((entry, st))
                except OSError:
                    continue
    except OSError as exc:
        raise ValueError("Cannot open this folder (access denied?).") from exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(lambda e: _dir_size(e.path, deadline), folders))

    items = []
    for entry, size in zip(folders, sizes):
        info = classifier.classify(entry.path, True)
        items.append({
            "path": entry.path, "name": entry.name, "is_dir": True,
            "size_bytes": size, "size_display": format_bytes(size),
            "verdict": info["verdict"], "label": info["label"], "reason": info["reason"],
            "can_clean": _can_clean_folder(entry.path, info),
            "can_delete": False,
        })
    for entry, st in files:
        info = classifier.classify(entry.path, False)
        items.append({
            "path": entry.path, "name": entry.name, "is_dir": False,
            "size_bytes": st.st_size, "size_display": format_bytes(st.st_size),
            "verdict": info["verdict"], "label": info["label"], "reason": info["reason"],
            "can_clean": False,
            "can_delete": info["verdict"] != classifier.KEEP and not is_protected_path(entry.path),
        })
    items.sort(key=lambda i: i["size_bytes"], reverse=True)

    total = sum(i["size_bytes"] for i in items)
    here = classifier.classify(path, True)
    parent = os.path.dirname(path.rstrip("\\/"))
    return {
        "path": path,
        "parent": parent if parent and parent != path and not _is_drive_root(path) else None,
        "verdict": here["verdict"], "label": here["label"], "reason": here["reason"],
        "total_bytes": total, "total_display": format_bytes(total),
        "items": items[:300],
        "hidden_items": max(0, len(items) - 300),
        "partial": time.monotonic() > deadline,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


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
    explore_category.cache_clear()
    browse_folder.cache_clear()


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
    """Empty one folder that is safe to clean.

    Allowed: folders from cleanup_targets(), or folders the classifier rates *safe* and
    *cleanable* (browser caches, __pycache__, crash dumps...). The verdict is recomputed here
    from the real path, so the client cannot talk the server into cleaning anything else.
    """
    if os.path.islink(path):
        return {"success": False, "error": "Links cannot be cleaned."}
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        return {"success": False, "error": "Folder not found."}

    allowed = {normalize_path(t["path"]): t for t in cleanup_targets()}
    target = allowed.get(normalize_path(real))
    if target is not None:
        name = target["name"]
    else:
        info = classifier.classify(real, True)
        if not _can_clean_folder(real, info):
            return {"success": False, "error": "This folder is not on the list of safe-to-clean locations."}
        name = info["label"]

    result = clean_directory_contents(real)
    return {"success": True, "message": f"Cleaned: {name}", **result}


def reveal_in_explorer(path: str) -> dict:
    """Open Windows Explorer with the item selected (so the user can delete it by hand)."""
    import subprocess
    target = os.path.abspath(path)
    if not os.path.exists(target):
        return {"success": False, "error": "Path not found."}
    if os.name != "nt":
        return {"success": False, "error": "Only available on Windows."}
    try:
        flag = "/select," if os.path.isfile(target) else ""
        subprocess.Popen(["explorer", f"{flag}{target}"])  # explorer returns non-zero normally; do not wait
        return {"success": True}
    except OSError as exc:
        return {"success": False, "error": f"Could not open Explorer: {exc}"}
