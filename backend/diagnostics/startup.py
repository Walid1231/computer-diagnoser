"""
Startup Optimizer Module
Lists, enables and disables the programs that run when Windows starts.

Items are switched on/off the same way Task Manager does it (the
StartupApproved registry keys), so changes are fully reversible and show up as
"Disabled" in Task Manager and Windows Settings. Items disabled by older versions
of this app (moved to an "AutorunsDisabled" key) are still listed and can be restored.
"""

import os

try:
    import winreg
except ImportError:  # not Windows (development / tests)
    winreg = None

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_ONCE = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
_RUN_32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
_LEGACY_SUFFIX = "\\AutorunsDisabled"

PROTECTED_NAMES = {"securityhealth", "windowsdefender", "rtkauduservice"}

_ENABLED_BYTES = bytes([2]) + bytes(11)
_DISABLED_BYTES = bytes([3]) + bytes(11)


def _hive(name):
    return winreg.HKEY_CURRENT_USER if name == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def _registry_sources():
    """(scope label, hive name, Run key path, StartupApproved sub key)."""
    return [
        ("Current User", "HKCU", _RUN, "Run"),
        ("All Users", "HKLM", _RUN, "Run"),
        ("All Users (32-bit)", "HKLM", _RUN_32, "Run32"),
    ]


def _folder_sources():
    """(scope label, hive name, folder path)."""
    return [
        ("Current User", "HKCU",
         os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")),
        ("All Users", "HKLM",
         os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                      r"Microsoft\Windows\Start Menu\Programs\Startup")),
    ]


def _approved_state(hive_name, approved_sub, value_name):
    """True if enabled (or never touched), False if disabled in StartupApproved."""
    try:
        with winreg.OpenKey(_hive(hive_name), f"{_APPROVED}\\{approved_sub}", 0, winreg.KEY_READ) as key:
            data, _ = winreg.QueryValueEx(key, value_name)
        return not (isinstance(data, (bytes, bytearray)) and data and data[0] % 2 == 1)
    except OSError:
        return True


def _enum_values(hive_name, path):
    values = []
    try:
        with winreg.OpenKey(_hive(hive_name), path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                values.append((name, value))
                index += 1
    except OSError:
        pass
    return values


def get_startup_programs():
    """Get all programs configured to run at startup (enabled and disabled)."""
    programs = []
    if winreg is None:
        return programs

    for scope, hive_name, path, approved_sub in _registry_sources():
        for name, value in _enum_values(hive_name, path):
            programs.append({
                "name": name,
                "key": name,
                "command": str(value),
                "scope": scope,
                "source": "Registry",
                "location": path,
                "enabled": _approved_state(hive_name, approved_sub, name),
                "toggleable": True,
            })
        # Items disabled by an older version of this app.
        for name, value in _enum_values(hive_name, path + _LEGACY_SUFFIX):
            programs.append({
                "name": name,
                "key": name,
                "command": str(value),
                "scope": scope,
                "source": "Registry",
                "location": path,
                "enabled": False,
                "legacy": True,
                "toggleable": True,
            })

    # RunOnce entries run a single time; shown for information only.
    for scope, hive_name in (("Current User (Once)", "HKCU"), ("All Users (Once)", "HKLM")):
        for name, value in _enum_values(hive_name, _RUN_ONCE):
            programs.append({
                "name": name, "key": name, "command": str(value), "scope": scope,
                "source": "Registry", "location": _RUN_ONCE, "enabled": True, "toggleable": False,
            })

    for scope, hive_name, folder in _folder_sources():
        if not folder or not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for item in names:
            if item.lower() == "desktop.ini":
                continue
            programs.append({
                "name": os.path.splitext(item)[0],
                "key": item,
                "command": os.path.join(folder, item),
                "scope": scope,
                "source": "Folder",
                "location": folder,
                "enabled": _approved_state(hive_name, "StartupFolder", item),
                "toggleable": True,
            })

    return programs


# ─── TOGGLE ──────────────────────────────────────────────────

def _set_approved(hive_name, approved_sub, value_name, enabled):
    path = f"{_APPROVED}\\{approved_sub}"
    with winreg.CreateKeyEx(_hive(hive_name), path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY,
                          _ENABLED_BYTES if enabled else _DISABLED_BYTES)


def _restore_legacy(hive_name, run_path, name):
    """Move an item disabled by an older version back into the Run key."""
    disabled_path = run_path + _LEGACY_SUFFIX
    with winreg.OpenKey(_hive(hive_name), disabled_path, 0, winreg.KEY_READ) as key:
        value, regtype = winreg.QueryValueEx(key, name)
    with winreg.OpenKey(_hive(hive_name), run_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, regtype, value)
    with winreg.OpenKey(_hive(hive_name), disabled_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, name)


def set_startup_enabled(name: str, enabled: bool, scope: str, location: str,
                        source: str = "Registry", key_name: str = "") -> dict:
    """Enable or disable one startup item. Only locations this module lists are accepted."""
    if winreg is None:
        return {"success": False, "error": "Startup management is only available on Windows."}

    key_name = key_name or name
    if name.lower() in PROTECTED_NAMES or key_name.lower() in PROTECTED_NAMES:
        return {"success": False, "error": f"Cannot modify protected startup item: {name}"}

    verb = "Enabled" if enabled else "Disabled"
    try:
        if source == "Registry":
            match = next((s for s in _registry_sources() if s[0] == scope and s[2] == location), None)
            if match is None:
                return {"success": False, "error": "Unknown startup location."}
            _, hive_name, run_path, approved_sub = match

            if enabled and _is_legacy_disabled(hive_name, run_path, key_name):
                _restore_legacy(hive_name, run_path, key_name)
            _set_approved(hive_name, approved_sub, key_name, enabled)

        elif source == "Folder":
            match = next((s for s in _folder_sources()
                          if s[0] == scope and os.path.normcase(s[2]) == os.path.normcase(location)), None)
            if match is None:
                return {"success": False, "error": "Unknown startup location."}
            _, hive_name, folder = match
            if os.path.basename(key_name) != key_name or not os.path.exists(os.path.join(folder, key_name)):
                return {"success": False, "error": "Startup item not found."}
            _set_approved(hive_name, "StartupFolder", key_name, enabled)

        else:
            return {"success": False, "error": "Unsupported startup item type."}

        return {"success": True, "message": f"{verb} startup item: {name}"}

    except PermissionError:
        return {"success": False, "error": "Access denied. Changing 'All Users' items needs Administrator rights."}
    except OSError as exc:
        return {"success": False, "error": f"Registry error: {exc}"}


def _is_legacy_disabled(hive_name, run_path, name) -> bool:
    return any(n == name for n, _ in _enum_values(hive_name, run_path + _LEGACY_SUFFIX))
