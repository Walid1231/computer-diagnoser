"""
Security & System Protection Module
Checks Windows Defender / antivirus, Firewall and Windows Update status.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from diagnostics.utils import ttl_cache
from diagnostics.winutil import powershell_json

_STALE_UPDATE_DAYS = 60


def get_security_status():
    """Get comprehensive security status report (the three checks run in parallel)."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        defender = pool.submit(get_defender_status)
        firewall = pool.submit(get_firewall_status)
        updates = pool.submit(get_update_status)
        return {
            "defender": defender.result(),
            "firewall": firewall.result(),
            "updates": updates.result(),
        }


# ─── ANTIVIRUS ───────────────────────────────────────────────

_DEFENDER_SCRIPT = r"""
$mp = Get-MpComputerStatus
$av = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object {
  [pscustomobject]@{ name = $_.displayName; state = [int]$_.productState } })
$out = [ordered]@{ av = $av; mp = $null }
if ($mp) {
  $out.mp = [pscustomobject]@{
    enabled = [bool]$mp.AntivirusEnabled
    rtp = [bool]$mp.RealTimeProtectionEnabled
    spy = [bool]$mp.AntispywareEnabled
    sig = $(if ($mp.AntivirusSignatureLastUpdated) { $mp.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd HH:mm') } else { '' })
    scan = $(if ($mp.QuickScanEndTime) { $mp.QuickScanEndTime.ToString('yyyy-MM-dd HH:mm') } else { '' })
  }
}
$out | ConvertTo-Json -Depth 4 -Compress
"""


@ttl_cache(60)
def get_defender_status():
    """Check Windows Defender / any other installed antivirus."""
    status = {
        "enabled": None,
        "real_time_protection": None,
        "status_level": "unknown",
        "message": "Could not determine antivirus status.",
    }
    data = powershell_json(_DEFENDER_SCRIPT, timeout=20)
    if not data:
        return status

    info = data[0]
    mp = info.get("mp")
    products = info.get("av") or []
    if isinstance(products, dict):
        products = [products]
    # productState bit 0x1000 = the product is switched on.
    # Match Microsoft's own product names exactly: a substring test on "defender" would also
    # swallow third-party products such as Bitdefender.
    microsoft_av = {"windows defender", "microsoft defender antivirus", "microsoft defender"}
    other_av = [p.get("name") for p in products
                if p.get("name") and p["name"].strip().lower() not in microsoft_av
                and (int(p.get("state") or 0) & 0x1000)]

    if mp:
        status["antispyware"] = bool(mp.get("spy"))
        status["last_signature_update"] = mp.get("sig") or "Unknown"
        status["last_scan"] = mp.get("scan") or "Unknown"

    if mp and mp.get("enabled") and mp.get("rtp"):
        status.update(enabled=True, real_time_protection=True, status_level="good",
                      message="Windows Defender is active with real-time protection ON.")
    elif other_av:
        # Defender switches itself off when another antivirus takes over - that is normal.
        status.update(enabled=True, real_time_protection=True, status_level="good",
                      message=f"Protected by {', '.join(other_av)}. Windows Defender stands by.")
    elif mp and mp.get("enabled"):
        status.update(enabled=True, real_time_protection=False, status_level="warning",
                      message="Defender enabled but real-time protection is OFF.")
    elif mp:
        status.update(enabled=False, real_time_protection=False, status_level="danger",
                      message="Windows Defender is DISABLED and no other antivirus was found — your PC may be vulnerable.")
    return status


# ─── FIREWALL ────────────────────────────────────────────────

@ttl_cache(60)
def get_firewall_status():
    """Check Windows Firewall status for all profiles."""
    status = {
        "profiles": [],
        "all_enabled": False,
        "status_level": "unknown",
        "message": "Could not determine firewall status.",
    }
    # Enabled is a GpoBoolean enum (True/False/NotConfigured); ask for its name.
    data = powershell_json(
        "Get-NetFirewallProfile | Select-Object Name, @{n='Enabled';e={\"$($_.Enabled)\"}} | "
        "ConvertTo-Json -Compress",
        timeout=15,
    )
    if not data:
        return status

    for profile in data:
        raw = profile.get("Enabled")
        enabled = raw == 1 if isinstance(raw, int) and not isinstance(raw, bool) else str(raw).lower() == "true"
        status["profiles"].append({"name": profile.get("Name") or "Unknown", "enabled": enabled})

    status["all_enabled"] = all(p["enabled"] for p in status["profiles"])
    if status["all_enabled"]:
        status["status_level"] = "good"
        status["message"] = "Windows Firewall is ON for all profiles."
    else:
        disabled = [p["name"] for p in status["profiles"] if not p["enabled"]]
        status["status_level"] = "danger"
        status["message"] = f"Firewall OFF for: {', '.join(disabled)}."
    return status


# ─── WINDOWS UPDATE ──────────────────────────────────────────

# Get-HotFix returns updates in no particular order, so the old "first 10" were not
# the latest ones. Sort by install date first.
_UPDATES_SCRIPT = r"""
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID, Description,
  @{n='On';e={ if ($_.InstalledOn) { $_.InstalledOn.ToString('yyyy-MM-dd') } else { '' } }} |
  ConvertTo-Json -Compress
"""


@ttl_cache(120)
def get_update_status():
    """Check recent Windows Updates and how old the latest one is."""
    status = {
        "recent_updates": [],
        "status_level": "unknown",
        "message": "Could not check Windows Update status.",
    }
    data = powershell_json(_UPDATES_SCRIPT, timeout=40)
    if not data:
        return status

    for update in data:
        status["recent_updates"].append({
            "id": update.get("HotFixID") or "",
            "description": update.get("Description") or "",
            "installed_on": update.get("On") or "Unknown",
        })

    latest = status["recent_updates"][0]
    age_days = None
    try:
        age_days = (datetime.now() - datetime.strptime(latest["installed_on"], "%Y-%m-%d")).days
    except ValueError:
        pass

    if age_days is None:
        status["status_level"] = "good"
        status["message"] = f"Last update: {latest['id']}."
    elif age_days <= _STALE_UPDATE_DAYS:
        status["status_level"] = "good"
        status["message"] = f"Up to date. Last: {latest['id']} on {latest['installed_on']} ({age_days} days ago)."
    else:
        status["status_level"] = "warning"
        status["message"] = (f"Last update {latest['id']} was installed {age_days} days ago "
                             f"({latest['installed_on']}). Check Windows Update.")
    return status
