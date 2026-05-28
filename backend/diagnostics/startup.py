"""
Startup Optimizer Module
Lists applications that run at startup on Windows.
"""

import os
import winreg
import subprocess


def get_startup_programs():
    """Get all programs configured to run at startup."""
    programs = []

    # Registry locations for startup items
    registry_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Current User"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "Current User (Once)"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "All Users"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "All Users (Once)"),
    ]

    for hive, path, scope in registry_paths:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    programs.append({
                        "name": name,
                        "command": value,
                        "scope": scope,
                        "source": "Registry",
                        "location": path,
                    })
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except (OSError, WindowsError):
            continue

    # Startup folder items
    startup_folders = [
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup",
    ]

    for folder in startup_folders:
        if os.path.isdir(folder):
            for item in os.listdir(folder):
                programs.append({
                    "name": os.path.splitext(item)[0],
                    "command": os.path.join(folder, item),
                    "scope": "Startup Folder",
                    "source": "Folder",
                    "location": folder,
                })

    return programs


def get_scheduled_tasks():
    """Get a summary of scheduled tasks (lightweight)."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "CSV", "/nh"],
            capture_output=True, text=True, timeout=10
        )
        tasks = []
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n")[:20]:
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 3:
                    tasks.append({
                        "name": parts[0].strip('"'),
                        "next_run": parts[1].strip('"') if len(parts) > 1 else "",
                        "status": parts[2].strip('"') if len(parts) > 2 else "",
                    })
        return tasks
    except Exception:
        return []
