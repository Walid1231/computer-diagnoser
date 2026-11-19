"""
Auto-Updater Module
Checks GitHub Releases for new versions and handles the update flow.
"""

import hashlib
import os
import sys
import json
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from diagnostics.version import __version__

GITHUB_OWNER = "Walid1231"
GITHUB_REPO = "computer-diagnoser"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
EXE_NAME = "ComputerDiagnoser.exe"


def _compare_versions(current: str, latest: str) -> int:
    """
    Compare two semver strings.
    Returns: 1 if latest > current, 0 if equal, -1 if current > latest
    """
    def parse(v):
        core = v.strip().lstrip("vV").split("-")[0].split("+")[0]
        return [int(x) for x in core.split(".")]
    try:
        c = parse(current)
        l = parse(latest)
        if l > c:
            return 1
        elif l == c:
            return 0
        return -1
    except (ValueError, AttributeError):
        return 0


def check_for_update() -> dict:
    """
    Check GitHub Releases API for a newer version.
    Returns update info dict.
    """
    result = {
        "available": False,
        "current_version": __version__,
        "latest_version": __version__,
        "download_url": None,
        "sha256": None,
        "release_notes": "",
        "release_url": "",
        "error": None,
    }

    try:
        req = urllib.request.Request(
            RELEASES_URL,
            headers={
                "User-Agent": f"ComputerDiagnoser/{__version__}",
                "Accept": "application/vnd.github.v3+json",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        latest_tag = (data.get("tag_name") or "").lstrip("vV")
        result["latest_version"] = latest_tag
        result["release_notes"] = (data.get("body") or "")[:500]
        result["release_url"] = data.get("html_url") or ""

        # Find the .exe asset
        for asset in data.get("assets", []):
            if asset.get("name", "").lower().endswith(".exe"):
                result["download_url"] = asset.get("browser_download_url")
                digest = asset.get("digest") or ""
                if digest.startswith("sha256:"):
                    result["sha256"] = digest.split(":", 1)[1].lower()
                break

        if _compare_versions(__version__, latest_tag) > 0:
            result["available"] = True

    except urllib.error.HTTPError as e:
        if e.code == 404:
            result["error"] = "No releases found yet."
        elif e.code == 403:
            result["error"] = "GitHub API rate limit. Try again later."
        else:
            result["error"] = f"GitHub API error: {e.code}"
    except urllib.error.URLError:
        result["error"] = "Cannot reach GitHub. Check your internet."
    except Exception as e:
        result["error"] = str(e)

    return result


_TRUSTED_DOWNLOAD_HOSTS = ("github.com", "githubusercontent.com")


def _is_trusted_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in _TRUSTED_DOWNLOAD_HOSTS)


def download_update(download_url: str, expected_sha256: str = None) -> dict:
    """
    Download the new .exe and prepare the update script.
    Returns the path to the updater batch script.
    """
    if not download_url:
        return {"success": False, "error": "No download URL provided."}
    if not _is_trusted_url(download_url):
        return {"success": False, "error": "Refusing to download from an untrusted address."}
    if not getattr(sys, 'frozen', False):
        # Running from source: replacing "the exe" would drop a stray file into the source folder.
        return {"success": False,
                "error": "Auto-update only works in the packaged .exe. When running from source, use git pull."}

    try:
        current_exe = sys.executable
        app_dir = os.path.dirname(current_exe)

        # Download to temp
        temp_dir = tempfile.mkdtemp(prefix="diagnoser_update_")
        new_exe_path = os.path.join(temp_dir, EXE_NAME)

        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": f"ComputerDiagnoser/{__version__}"}
        )

        digest = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(new_exe_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)

        if not os.path.exists(new_exe_path) or os.path.getsize(new_exe_path) < 1_000_000:
            return {"success": False, "error": "Downloaded file appears corrupt."}
        if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
            return {"success": False, "error": "Download failed its integrity check (SHA-256 mismatch)."}

        # Create updater batch script
        updater_bat = os.path.join(temp_dir, "updater.bat")
        target_exe = os.path.join(app_dir, EXE_NAME)

        bat_content = f"""@echo off
title Updating Computer Diagnoser...
echo.
echo  ============================================
echo    Updating Computer Diagnoser...
echo  ============================================
echo.
echo  Waiting for app to close...
ping -n 4 127.0.0.1 >nul

set RETRIES=15
:retry
copy /y "{new_exe_path}" "{target_exe}" >nul 2>&1
if not errorlevel 1 goto done
set /a RETRIES=%RETRIES%-1
if %RETRIES% LEQ 0 goto failed
echo  File locked, retrying...
ping -n 3 127.0.0.1 >nul
goto retry

:failed
echo.
echo  UPDATE FAILED. The file may be locked.
echo  Please close Computer Diagnoser and try again.
pause
exit /b 1

:done
echo.
echo  Update complete! Relaunching...
ping -n 2 127.0.0.1 >nul
start "" "{target_exe}"
rmdir /s /q "{temp_dir}" 2>nul
exit
"""
        with open(updater_bat, "w", encoding="mbcs") as f:
            f.write(bat_content)

        return {
            "success": True,
            "updater_path": updater_bat,
            "new_exe_path": new_exe_path,
            "message": "Update downloaded. Ready to install.",
        }

    except Exception as e:
        return {"success": False, "error": f"Download failed: {str(e)}"}


def install_update(updater_path: str) -> dict:
    """
    Launch the updater batch script and exit the app.
    The batch script will wait, replace the exe, and relaunch.
    """
    if not os.path.exists(updater_path):
        return {"success": False, "error": "Updater script not found."}

    try:
        import subprocess
        # Launch the updater in a new detached process
        # A one-file PyInstaller exe that is relaunched from its own child inherits the old
        # temp-extraction folder (which is deleted when we exit) and fails to start.
        # PYINSTALLER_RESET_ENVIRONMENT makes the relaunched copy unpack itself fresh.
        env = dict(os.environ)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        env.pop("_MEIPASS2", None)
        subprocess.Popen(
            ["cmd", "/c", "start", "/min", "", updater_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            env=env,
        )
        return {"success": True, "message": "Update installing... App will restart."}
    except Exception as e:
        return {"success": False, "error": str(e)}
