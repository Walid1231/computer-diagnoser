"""Small helpers shared by every diagnostics module."""

import functools
import os
import threading
import time


def format_bytes(size) -> str:
    """Convert a byte count to a human readable string ("512 B", "1.50 GB")."""
    size = float(size or 0)
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024:
            return f"{size:.2f} {unit}"
    return f"{size / 1024:.2f} PB"


def ttl_cache(seconds: float):
    """Cache a function's result for `seconds`. Thread-safe; errors are not cached.

    Expensive probes (PowerShell calls, directory scans) are hit repeatedly by the
    UI and by the chat engine, so a short cache removes a lot of duplicate work.
    """

    def decorator(fn):
        lock = threading.Lock()
        store = {}

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                hit = store.get(key)
                if hit is not None and now - hit[0] < seconds:
                    return hit[1]
            value = fn(*args, **kwargs)
            with lock:
                store[key] = (time.monotonic(), value)
            return value

        def cache_clear():
            with lock:
                store.clear()

        wrapper.cache_clear = cache_clear
        return wrapper

    return decorator


def normalize_path(path: str) -> str:
    """Absolute, symlink-resolved, case-normalised path for safe comparisons."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_within(path: str, parent: str) -> bool:
    """True if `path` is `parent` or inside it. Both must already be normalised."""
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:  # different drives
        return False
