"""
Needed / unneeded classifier.

Every folder or file gets one of three verdicts:

  safe    Regenerable junk (temp files, caches, crash dumps...). Deleting it frees space and
          nothing is lost: Windows or the app recreates what it needs.
  review  Might be junk, might be yours. Re-downloadable data, old installers, leftovers.
          The app explains it and the user decides.
  keep    Needed. Windows itself, installed programs, personal documents, repositories...
          Never offered for deletion.

The rules are matched against the *normalised* path (lower case, "/" separators), most
specific first, so "C:\\Windows\\Temp" is safe even though "C:\\Windows" is keep.

Safety notes
------------
* This module only *describes*. Anything that deletes re-checks the verdict on the server and
  also applies storage.is_protected_path(), so a crafted request cannot bypass either.
* `cleanable` means "emptying this folder is a supported one-click action". A folder can be
  safe but not cleanable (e.g. it needs Windows Disk Cleanup, or it is a system location).
* "safe" is deliberately narrow. When in doubt a rule says "review".
"""

import os
import re
from functools import lru_cache

SAFE = "safe"
REVIEW = "review"
KEEP = "keep"

_USER = r"^[a-z]:/users/[^/]+"
_LOCAL = _USER + r"/appdata/local"
_ROAMING = _USER + r"/appdata/roaming"
_END = r"(?:/|$)"

# (id, regex, verdict, label, reason, cleanable) — first match wins.
_RULES = [
    # ── SAFE: regenerable junk ───────────────────────────────
    ("user_temp", _LOCAL + r"/temp" + _END, SAFE, "User temp files",
     "Leftovers from installers and apps. Windows recreates what it needs; files in use are skipped.", True),
    ("windows_temp", r"^[a-z]:/windows/temp" + _END, SAFE, "Windows temp files",
     "Temporary files from Windows and installers. Needs Administrator rights to clean.", True),
    ("update_downloads", r"^[a-z]:/windows/softwaredistribution/download" + _END, SAFE, "Windows Update downloads",
     "Already-installed update packages. Windows re-downloads anything it still needs.", True),
    ("delivery_optimization", r"/deliveryoptimization/cache" + _END, SAFE, "Delivery Optimization cache",
     "Windows Update sharing cache. Remove it with Settings > System > Storage > Temporary files.", False),
    ("error_reports", r"^[a-z]:/programdata/microsoft/windows/wer" + _END, SAFE, "Windows error reports",
     "Old crash reports. Remove with Windows Disk Cleanup.", False),
    ("minidump", r"^[a-z]:/windows/minidump" + _END, SAFE, "Crash dumps",
     "Memory dumps from past blue-screens. Only useful for debugging. Remove with Disk Cleanup.", False),
    ("crash_dumps", _LOCAL + r"/crashdumps" + _END, SAFE, "App crash dumps",
     "Memory dumps from crashed apps. Only useful for debugging.", True),
    ("browser_cache", r"/user data/[^/]+/(?:cache|code cache|gpucache|dawncache|dawngraphitecache|grshadercache|"
                      r"service worker/cachestorage)" + _END, SAFE, "Browser cache",
     "Saved copies of web pages and scripts. The browser rebuilds it as you browse (pages load slower once).", True),
    ("firefox_cache", r"/mozilla/firefox/profiles/[^/]+/(?:cache2|startupcache)" + _END, SAFE, "Firefox cache",
     "Saved copies of web pages. Firefox rebuilds it as you browse.", True),
    ("inet_cache", r"/microsoft/windows/inetcache" + _END, SAFE, "Internet cache",
     "Temporary internet files used by Windows and Edge.", True),
    ("shader_cache", _LOCAL + r"/d3dscache" + _END, SAFE, "DirectX shader cache",
     "Compiled graphics shaders. Games rebuild them (first launch may stutter once).", True),
    ("thumbnails", _LOCAL + r"/microsoft/windows/explorer" + _END, SAFE, "Thumbnail & icon cache",
     "Explorer previews. Rebuilt automatically when you open a folder.", True),
    ("pip_cache", r"/pip/cache" + _END, SAFE, "pip download cache",
     "Downloaded Python packages. pip re-downloads them if needed.", True),
    ("npm_cache", r"/(?:npm-cache|\.npm/_cacache)" + _END, SAFE, "npm download cache",
     "Downloaded JavaScript packages. npm re-downloads them if needed.", True),
    ("pycache", r"/__pycache__" + _END, SAFE, "Python bytecode cache",
     "Compiled .pyc files. Python regenerates them on the next run.", True),
    ("tool_caches", r"/\.(?:pytest_cache|mypy_cache|ruff_cache)" + _END, SAFE, "Tool cache",
     "Cache of a developer tool. Rebuilt on the next run.", True),

    # ── KEEP: needed system / program / personal data ────────
    ("winsxs", r"^[a-z]:/windows/winsxs" + _END, KEEP, "Windows component store (WinSxS)",
     "Required for Windows updates and repairs. Never delete by hand. Sizes include hard links, so the "
     "real usage is smaller than shown. Shrink it only with Disk Cleanup or DISM.", False),
    ("windows_installer", r"^[a-z]:/windows/installer" + _END, KEEP, "Windows Installer cache",
     "Needed to repair or uninstall your programs. Deleting it breaks uninstallers.", False),
    ("windows", r"^[a-z]:/windows" + _END, KEEP, "Windows system files",
     "Part of Windows. Deleting anything here can stop the PC from booting.", False),
    ("program_files", r"^[a-z]:/program files(?: \(x86\))?" + _END, KEEP, "Installed programs",
     "Apps you installed. Uninstall them from Settings > Apps instead of deleting the folder.", False),
    ("boot_recovery", r"^[a-z]:/(?:recovery|boot|efi|\$winreagent|system volume information)" + _END, KEEP,
     "Boot & recovery data", "Needed to start and repair Windows.", False),
    ("user_programs", _LOCAL + r"/programs" + _END, KEEP, "Installed programs (per user)",
     "Apps installed for your account. Uninstall them from Settings > Apps.", False),
    ("store_apps", _LOCAL + r"/packages" + _END, KEEP, "Microsoft Store app data",
     "Data of Store apps. Reset an app from Settings > Apps instead.", False),
    ("browser_profile", r"/appdata/(?:local|roaming)/(?:google/chrome|microsoft/edge|mozilla/firefox|"
                        r"brave-software|opera software)" + _END, KEEP, "Browser profile",
     "Your bookmarks, passwords, history and extensions. (The cache inside it is listed separately.)", False),
    ("git_repo", r"/\.git" + _END, KEEP, "Git repository history",
     "The full history of a project. Deleting it loses your version history.", False),
    ("conda_pkgs", r"/(?:ana|mini)conda3?/pkgs" + _END, REVIEW, "Conda package cache",
     "Downloaded packages. Run `conda clean --all` to remove the unused ones safely.", False),
    ("conda_env", r"/(?:ana|mini)conda3?" + _END, KEEP, "Python environment (Anaconda/Miniconda)",
     "Your Python installation and environments. Deleting it breaks projects that use it.", False),
    ("site_packages", r"/site-packages" + _END, KEEP, "Python packages",
     "Libraries installed for Python. Your projects need them.", False),
    ("programdata_package_cache", r"^[a-z]:/programdata/package cache" + _END, REVIEW, "Installer cache",
     "Used to repair or uninstall programs (Visual Studio, drivers...). Removing it can break repair/uninstall.",
     False),
    ("programdata", r"^[a-z]:/programdata" + _END, KEEP, "Shared app data",
     "Settings and data shared by installed programs.", False),
    ("personal", _USER + r"/(?:documents|desktop|pictures|videos|music|onedrive[^/]*)" + _END, KEEP,
     "Your personal files", "These are yours, so the app never marks them as junk. Review them yourself.", False),

    # ── REVIEW: could be junk, but you decide ────────────────
    ("windows_old", r"^[a-z]:/windows\.old" + _END, REVIEW, "Previous Windows installation",
     "Lets you roll back an upgrade. Safe to remove once you are happy with Windows — use Disk Cleanup.", False),
    ("upgrade_leftovers", r"^[a-z]:/\$windows\.~(?:bt|ws)" + _END, REVIEW, "Windows upgrade leftovers",
     "Temporary upgrade files. Remove with Disk Cleanup > 'Windows Update Cleanup'.", False),
    ("hf_cache", r"/\.cache/(?:huggingface|torch|whisper)" + _END, REVIEW, "AI model cache",
     "Downloaded AI models and datasets. Re-downloadable, but large and slow to fetch again.", False),
    ("ollama", r"/\.ollama" + _END, REVIEW, "Ollama models",
     "Local AI models you downloaded. Delete the ones you no longer use.", False),
    ("dot_cache", r"/\.cache" + _END, REVIEW, "Tool cache",
     "A cache folder. Usually re-creatable, but check what created it.", False),
    ("package_cache", r"/(?:\.gradle/caches|\.nuget/packages|nuget/packages|\.m2/repository)" + _END, REVIEW,
     "Package download cache", "Re-downloaded when a project needs it, which can take a while.", False),
    ("node_modules", r"/node_modules" + _END, REVIEW, "JavaScript dependencies",
     "Rebuilt by `npm install`. Safe to delete for projects you are not working on.", False),
    ("downloads", _USER + r"/downloads" + _END, REVIEW, "Downloads folder",
     "Installers and files you downloaded. Delete what you already installed or extracted.", False),
    ("app_data", r"/appdata/(?:local|roaming)" + _END, REVIEW, "App data",
     "Settings and data of your apps. Removing it can reset the app, so check which app owns it.", False),
]

_COMPILED = [(rid, re.compile(rx), verdict, label, reason, cleanable)
             for rid, rx, verdict, label, reason, cleanable in _RULES]

_DEFAULT_USER = (REVIEW, "Your files", "Not recognised as junk. Check it yourself before deleting.")
_DEFAULT_OTHER = (REVIEW, "Unrecognised", "Not recognised as junk or as a system file. Check before deleting.")

# Files that are junk wherever they are (unless they sit in a KEEP location).
SAFE_EXTENSIONS = {".tmp", ".temp", ".dmp", ".etl", ".chk", ".gid"}
_SYSTEM_FILES = {
    "pagefile.sys": "Windows virtual memory file. Resize it in System settings; never delete it.",
    "swapfile.sys": "Windows swap file used by Store apps. Never delete it.",
    "hiberfil.sys": "Hibernation file. To reclaim its space, turn hibernation off with `powercfg /hibernate off`.",
}
_FILE_REVIEW_HINTS = {
    ".bak": ("Backup file", "A backup copy. Delete it if you no longer need that backup."),
    ".old": ("Old copy", "A previous version of a file. Usually safe to delete once you checked."),
    ".orig": ("Original copy", "Left behind by a merge or edit tool."),
    ".log": ("Log file", "Diagnostic log. Usually safe to delete; the app recreates it."),
    ".iso": ("Disk image", "An installer or disc image. Delete it if you already used it."),
    ".img": ("Disk image", "A disk image. Delete it if you no longer need it."),
    ".vhd": ("Virtual disk", "A virtual machine disk. Make sure no VM uses it before deleting."),
    ".vhdx": ("Virtual disk", "A virtual machine disk. Make sure no VM uses it before deleting."),
    ".vmdk": ("Virtual disk", "A virtual machine disk. Make sure no VM uses it before deleting."),
}


def normalize(path: str) -> str:
    """Lower-case, forward-slash form used for rule matching."""
    text = str(path).replace("\\", "/").lower()
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/") if len(text) > 3 else text


@lru_cache(maxsize=100_000)
def _classify_dir(norm: str):
    """(verdict, label, reason, cleanable, rule_id) for a normalised directory path."""
    for rid, regex, verdict, label, reason, cleanable in _COMPILED:
        if regex.search(norm):
            return verdict, label, reason, cleanable, rid
    if re.match(_USER + _END, norm):
        return (*_DEFAULT_USER, False, "default_user")
    return (*_DEFAULT_OTHER, False, "default")


def classify_dir(path: str):
    """Cached directory verdict as a tuple (verdict, label, reason, cleanable, rule_id)."""
    return _classify_dir(normalize(path))


def file_verdict(dir_verdict: str, name: str, ext: str) -> str:
    """Cheap per-file verdict used while scanning (given its folder's verdict)."""
    if dir_verdict == KEEP:
        return KEEP
    lowered = name.lower()
    if ext in SAFE_EXTENSIONS or lowered.startswith("~$"):
        return SAFE
    if lowered in _SYSTEM_FILES:
        return KEEP
    return dir_verdict


def classify(path: str, is_dir: bool = False) -> dict:
    """Full description of one folder or file."""
    norm = normalize(path)
    if is_dir:
        verdict, label, reason, cleanable, rule = _classify_dir(norm)
        return {"verdict": verdict, "label": label, "reason": reason, "cleanable": cleanable, "rule": rule}

    parent = norm.rsplit("/", 1)[0] if "/" in norm else norm
    name = norm.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1]
    verdict, label, reason, _cleanable, rule = _classify_dir(parent)

    if name in _SYSTEM_FILES:
        return {"verdict": KEEP, "label": "Windows system file", "reason": _SYSTEM_FILES[name],
                "cleanable": False, "rule": "system_file"}
    if verdict == KEEP:
        return {"verdict": KEEP, "label": label, "reason": reason, "cleanable": False, "rule": rule}
    if ext in SAFE_EXTENSIONS or name.startswith("~$"):
        return {"verdict": SAFE, "label": "Temporary file",
                "reason": "A temporary or debug file. Safe to delete unless the app that made it is running.",
                "cleanable": False, "rule": "temp_extension"}
    if verdict == SAFE:
        return {"verdict": SAFE, "label": label, "reason": reason, "cleanable": False, "rule": rule}
    if ext in _FILE_REVIEW_HINTS:
        hint_label, hint_reason = _FILE_REVIEW_HINTS[ext]
        return {"verdict": REVIEW, "label": hint_label, "reason": hint_reason, "cleanable": False,
                "rule": "review_extension"}
    return {"verdict": REVIEW, "label": label, "reason": reason, "cleanable": False, "rule": rule}
