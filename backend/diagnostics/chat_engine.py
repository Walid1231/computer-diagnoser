"""
Smart Diagnostic Chat Engine
Rule-based system intelligence that analyzes your PC and answers questions.
No AI/LLM needed — pure diagnostic logic.
"""

import re
from diagnostics import storage, system, network, startup, health, security, tools


# ─── INTENT DETECTION ────────────────────────────────────────

INTENTS = [
    {
        "id": "storage_low",
        "keywords": ["storage", "space", "disk", "full", "low storage", "no space", "storage low",
                      "drive full", "why is my c", "why is my d", "why is my w", "running out",
                      "free up", "clean up", "clear space", "not enough space", "storage problem"],
        "handler": "handle_storage_low",
    },
    {
        "id": "slow_pc",
        "keywords": ["slow", "lag", "lagging", "hanging", "freeze", "freezing", "sluggish",
                      "not responding", "performance", "speed", "why is my pc slow",
                      "what is slowing", "affecting", "heavy", "taking too long"],
        "handler": "handle_slow_pc",
    },
    {
        "id": "large_files",
        "keywords": ["large files", "big files", "biggest files", "largest files", "huge files",
                      "what is taking space", "show large", "find large", "find big"],
        "handler": "handle_large_files",
    },
    {
        "id": "clean_temp",
        "keywords": ["temp", "temporary", "cache", "clean", "cleanup", "junk", "delete temp",
                      "clear cache", "remove temp", "clean temp", "clean cache", "junk files"],
        "handler": "handle_temp_files",
    },
    {
        "id": "startup_check",
        "keywords": ["startup", "boot", "start up", "boot time", "slow boot", "starting slow",
                      "too many programs", "autostart", "startup programs"],
        "handler": "handle_startup",
    },
    {
        "id": "memory_check",
        "keywords": ["ram", "memory", "out of memory", "memory full", "memory usage",
                      "memory leak", "using too much memory", "high memory"],
        "handler": "handle_memory",
    },
    {
        "id": "cpu_check",
        "keywords": ["cpu", "processor", "cpu usage", "cpu high", "high cpu", "100% cpu",
                      "cpu full", "overheating", "fan loud", "hot"],
        "handler": "handle_cpu",
    },
    {
        "id": "network_check",
        "keywords": ["internet", "network", "wifi", "connection", "no internet", "disconnected",
                      "can't connect", "ping", "online", "offline", "speed test"],
        "handler": "handle_network",
    },
    {
        "id": "full_diagnosis",
        "keywords": ["diagnose", "full scan", "check everything", "full check", "health check",
                      "overall", "scan my pc", "what's wrong", "whats wrong", "problems",
                      "issues", "analyze", "analysis", "doctor", "help me"],
        "handler": "handle_full_diagnosis",
    },
    {
        "id": "security_check",
        "keywords": ["security", "defender", "antivirus", "firewall", "virus", "malware",
                      "protection", "windows update", "update", "secure", "vulnerable"],
        "handler": "handle_security",
    },
    {
        "id": "temperature_check",
        "keywords": ["temperature", "temp", "thermal", "heat", "hot", "cooling", "fan",
                      "throttle", "throttling", "overheat"],
        "handler": "handle_temperature",
    },
    {
        "id": "battery_health",
        "keywords": ["battery health", "battery degradation", "battery wear", "battery life",
                      "battery capacity", "replace battery", "battery old", "battery report"],
        "handler": "handle_battery_health",
    },
    {
        "id": "speed_test",
        "keywords": ["speed test", "internet speed", "download speed", "bandwidth",
                      "how fast", "connection speed", "mbps"],
        "handler": "handle_speed_test",
    },
    {
        "id": "find_duplicates",
        "keywords": ["duplicate", "duplicates", "same file", "identical", "copy of",
                      "duplicate files", "find duplicates"],
        "handler": "handle_duplicates",
    },
    {
        "id": "system_boost",
        "keywords": ["boost", "optimize", "speed up", "make faster", "quick clean",
                      "one click", "1 click", "flush", "clear everything"],
        "handler": "handle_system_boost",
    },
    {
        "id": "crash_logs",
        "keywords": ["crash", "bsod", "blue screen", "error log", "event log",
                      "why did my pc crash", "crash report", "critical error"],
        "handler": "handle_crash_logs",
    },
]


def detect_intent(question: str) -> dict:
    """Detect the user's intent from their question."""
    q = question.lower().strip()

    best_match = None
    best_score = 0

    for intent in INTENTS:
        score = 0
        for kw in intent["keywords"]:
            if kw in q:
                # Longer keyword matches get higher score
                score += len(kw)

        if score > best_score:
            best_score = score
            best_match = intent

    if best_match and best_score > 0:
        return best_match

    return {"id": "unknown", "handler": "handle_unknown"}


# ─── INTENT HANDLERS ─────────────────────────────────────────

def handle_storage_low():
    """Analyze storage and tell user why space is low."""
    drives = storage.get_drive_usage()
    temp_files = storage.get_temp_files()

    messages = []
    actions = []
    critical_drives = []

    for d in drives:
        if d["percent_used"] >= 90:
            critical_drives.append(d)
            messages.append(
                f"🔴 **{d['mountpoint']}** is critically full at **{d['percent_used']}%** — "
                f"only **{d['free_display']}** free out of {d['total_display']}."
            )
        elif d["percent_used"] >= 75:
            messages.append(
                f"🟡 **{d['mountpoint']}** is at **{d['percent_used']}%** — "
                f"**{d['free_display']}** free out of {d['total_display']}."
            )
        else:
            messages.append(
                f"🟢 **{d['mountpoint']}** is healthy at **{d['percent_used']}%** — "
                f"**{d['free_display']}** free."
            )

    # Check temp files
    total_temp = sum(t["size_bytes"] for t in temp_files)
    if total_temp > 100 * 1024 * 1024:  # > 100MB
        messages.append(
            f"\n🗑️ I found **{storage._format_bytes(total_temp)}** of temporary/cache files that can be cleaned:"
        )
        for t in temp_files:
            if t["size_bytes"] > 10 * 1024 * 1024:  # > 10MB
                messages.append(f"   • **{t['name']}** — {t['size_display']}")
                actions.append({
                    "type": "delete_folder_contents",
                    "path": t["path"],
                    "name": t["name"],
                    "size": t["size_display"],
                    "label": f"Clean {t['name']} ({t['size_display']})",
                })

    if critical_drives:
        messages.append("\n💡 **Tip:** Ask me **\"show large files\"** to find the biggest space wasters on your drives.")
    else:
        messages.append("\n✅ Your drives look healthy! No critical space issues detected.")

    return {
        "title": "Storage Analysis",
        "messages": messages,
        "actions": actions,
    }


def handle_slow_pc():
    """Analyze what's slowing the PC down."""
    cpu = system.get_cpu_info()
    mem = system.get_memory_info()
    procs = system.get_top_processes(top_n=10)
    startup_items = startup.get_startup_programs()

    messages = []
    actions = []

    # CPU check
    if cpu["overall_percent"] > 80:
        messages.append(f"🔴 **CPU is at {cpu['overall_percent']}%** — your processor is overloaded!")
    elif cpu["overall_percent"] > 50:
        messages.append(f"🟡 **CPU is at {cpu['overall_percent']}%** — moderately loaded.")
    else:
        messages.append(f"🟢 **CPU is at {cpu['overall_percent']}%** — looking good.")

    # RAM check
    ram_pct = mem["ram"]["percent_used"]
    if ram_pct > 85:
        messages.append(
            f"🔴 **RAM is at {ram_pct}%** — using {mem['ram']['used_display']} of "
            f"{mem['ram']['total_display']}. This is causing slowdowns!"
        )
    elif ram_pct > 65:
        messages.append(
            f"🟡 **RAM is at {ram_pct}%** — {mem['ram']['available_display']} available."
        )
    else:
        messages.append(f"🟢 **RAM is at {ram_pct}%** — plenty of memory available.")

    # Top resource hogs
    hogs = [p for p in procs if p["memory_bytes"] > 200 * 1024 * 1024]  # > 200MB
    if hogs:
        messages.append("\n🐷 **Top memory hogs:**")
        for p in hogs[:6]:
            messages.append(f"   • **{p['name']}** (PID: {p['pid']}) — using {p['memory_display']}")

    # Startup check
    if len(startup_items) > 8:
        messages.append(
            f"\n⚡ You have **{len(startup_items)} startup programs** — this may slow your boot time."
        )

    # Recommendations
    messages.append("\n💡 **Recommendations:**")
    if ram_pct > 85:
        messages.append("   • Close unused browser tabs and applications")
        messages.append("   • Check if any large programs can be closed")
    if len(startup_items) > 8:
        messages.append("   • Reduce startup programs to speed up boot time")
    if cpu["overall_percent"] < 30 and ram_pct < 60:
        messages.append("   • Your PC looks fine! If it feels slow, try restarting.")

    return {
        "title": "Performance Analysis",
        "messages": messages,
        "actions": actions,
    }


def handle_large_files():
    """Find and list large files across all drives."""
    drives = storage.get_drive_usage()
    messages = []
    actions = []

    for d in drives:
        large = storage.get_large_files(d["mountpoint"], top_n=10, min_size_mb=50)
        if large:
            messages.append(f"\n📦 **Large files on {d['mountpoint']}:**")
            for f in large:
                messages.append(f"   • **{f['name']}** — {f['size_display']}  `{f['extension']}`")
                actions.append({
                    "type": "delete_file",
                    "path": f["path"],
                    "name": f["name"],
                    "size": f["size_display"],
                    "label": f"Delete {f['name']} ({f['size_display']})",
                })

    if not actions:
        messages.append("✅ No files larger than 50 MB found.")

    return {
        "title": "Large Files Found",
        "messages": messages,
        "actions": actions,
    }


def handle_temp_files():
    """Find and offer to clean temp/cache files."""
    temp = storage.get_temp_files()
    messages = []
    actions = []

    total = sum(t["size_bytes"] for t in temp)

    if total > 0:
        messages.append(f"🗑️ Found **{storage._format_bytes(total)}** of cleanable temp/cache files:\n")
        for t in temp:
            messages.append(f"   • **{t['name']}** — {t['size_display']}")
            messages.append(f"     `{t['path']}`")
            actions.append({
                "type": "delete_folder_contents",
                "path": t["path"],
                "name": t["name"],
                "size": t["size_display"],
                "label": f"Clean {t['name']} ({t['size_display']})",
            })
        messages.append(f"\n💡 Cleaning these could free up **{storage._format_bytes(total)}**!")
    else:
        messages.append("✅ No significant temp or cache files found. Your system is clean!")

    return {
        "title": "Temporary Files Scan",
        "messages": messages,
        "actions": actions,
    }


def handle_startup():
    """Analyze startup programs."""
    items = startup.get_startup_programs()
    messages = []
    actions = []

    messages.append(f"⚡ You have **{len(items)} programs** set to run at startup:\n")

    for item in items:
        messages.append(f"   • **{item['name']}** — {item['scope']}")
        messages.append(f"     `{item['command'][:80]}`")

    if len(items) > 8:
        messages.append(f"\n🟡 **{len(items)} startup items is quite high.** Consider removing programs "
                        "you don't need starting automatically to speed up boot time.")
    elif len(items) > 4:
        messages.append(f"\n🟢 Startup count is moderate. Your boot time should be reasonable.")
    else:
        messages.append(f"\n🟢 Very few startup items — your boot should be fast!")

    return {
        "title": "Startup Analysis",
        "messages": messages,
        "actions": actions,
    }


def handle_memory():
    """Detailed memory analysis."""
    mem = system.get_memory_info()
    procs = system.get_top_processes(top_n=10)
    messages = []
    actions = []

    ram = mem["ram"]
    messages.append(f"🧠 **RAM Usage: {ram['percent_used']}%**")
    messages.append(f"   • Total: {ram['total_display']}")
    messages.append(f"   • Used: {ram['used_display']}")
    messages.append(f"   • Available: {ram['available_display']}")

    swap = mem["swap"]
    if swap["percent_used"] > 50:
        messages.append(f"\n🟡 **Swap is at {swap['percent_used']}%** ({swap['used_display']} / {swap['total_display']})")
        messages.append("   This means your RAM is overflowing to disk — causing slowdowns.")

    # Top memory consumers
    messages.append("\n🐷 **Top memory consumers:**")
    for p in procs[:8]:
        bar_len = min(int(p["memory_bytes"] / (500 * 1024 * 1024) * 10), 20)
        bar = "█" * bar_len
        messages.append(f"   {bar} **{p['name']}** — {p['memory_display']}")

    if ram["percent_used"] > 85:
        messages.append("\n💡 **Tip:** Close unused applications and browser tabs to free up RAM.")

    return {
        "title": "Memory Analysis",
        "messages": messages,
        "actions": actions,
    }


def handle_cpu():
    """Detailed CPU analysis."""
    cpu = system.get_cpu_info()
    procs = system.get_top_processes(top_n=10)
    messages = []

    messages.append(f"⚙️ **CPU Usage: {cpu['overall_percent']}%**")
    messages.append(f"   • Cores: {cpu['physical_cores']} physical / {cpu['logical_cores']} logical")
    messages.append(f"   • Frequency: {cpu['current_freq_mhz']} MHz")

    messages.append("\n📊 **Per-core usage:**")
    for i, pct in enumerate(cpu["per_cpu_percent"]):
        level = "🟢" if pct < 50 else "🟡" if pct < 80 else "🔴"
        bar_len = min(int(pct / 5), 20)
        bar = "█" * bar_len
        messages.append(f"   Core {i}: {level} {bar} {pct}%")

    # CPU-heavy processes
    cpu_hogs = sorted(procs, key=lambda p: p["cpu_percent"], reverse=True)[:5]
    if cpu_hogs[0]["cpu_percent"] > 10:
        messages.append("\n🔥 **Top CPU consumers:**")
        for p in cpu_hogs:
            if p["cpu_percent"] > 0:
                messages.append(f"   • **{p['name']}** — {p['cpu_percent']}% CPU")

    if cpu["overall_percent"] > 80:
        messages.append("\n💡 **Tip:** Your CPU is under heavy load. Close resource-heavy apps or check for background processes.")

    return {
        "title": "CPU Analysis",
        "messages": messages,
        "actions": [],
    }


def handle_network():
    """Network connectivity check."""
    interfaces = network.get_network_info()
    ping_result = network.ping_test("8.8.8.8", count=2)
    messages = []

    if ping_result["success"]:
        messages.append("🟢 **Internet connection is working!** Ping to Google DNS successful.")
    else:
        messages.append("🔴 **No internet connection!** Ping to 8.8.8.8 failed.")

    active_ifaces = [i for i in interfaces if i["is_up"]]
    messages.append(f"\n📡 **{len(active_ifaces)} active** network interfaces out of {len(interfaces)} total:")

    for iface in interfaces:
        status = "🟢 UP" if iface["is_up"] else "🔴 DOWN"
        ipv4 = next((a["address"] for a in iface["addresses"] if a["type"] == "IPv4"), "No IP")
        messages.append(f"   • **{iface['name']}** — {status}")
        messages.append(f"     IP: {ipv4} | Speed: {iface['speed_mbps']} Mbps")

    return {
        "title": "Network Diagnostics",
        "messages": messages,
        "actions": [],
    }


def handle_full_diagnosis():
    """Run a complete system diagnosis."""
    cpu = system.get_cpu_info()
    mem = system.get_memory_info()
    drives = storage.get_drive_usage()
    temp = storage.get_temp_files()
    startup_items = startup.get_startup_programs()
    battery = system.get_battery_info()

    messages = []
    actions = []
    issues = 0

    messages.append("🔍 **Full System Diagnosis Report**\n")
    messages.append("─" * 40)

    # CPU
    if cpu["overall_percent"] > 80:
        messages.append(f"🔴 CPU: {cpu['overall_percent']}% — OVERLOADED")
        issues += 1
    elif cpu["overall_percent"] > 50:
        messages.append(f"🟡 CPU: {cpu['overall_percent']}% — Moderate")
    else:
        messages.append(f"🟢 CPU: {cpu['overall_percent']}% — Healthy")

    # RAM
    ram_pct = mem["ram"]["percent_used"]
    if ram_pct > 85:
        messages.append(f"🔴 RAM: {ram_pct}% — CRITICAL ({mem['ram']['available_display']} free)")
        issues += 1
    elif ram_pct > 65:
        messages.append(f"🟡 RAM: {ram_pct}% — Moderate ({mem['ram']['available_display']} free)")
    else:
        messages.append(f"🟢 RAM: {ram_pct}% — Healthy ({mem['ram']['available_display']} free)")

    # Drives
    for d in drives:
        if d["percent_used"] > 90:
            messages.append(f"🔴 {d['mountpoint']} — {d['percent_used']}% full ({d['free_display']} free) — CRITICAL")
            issues += 1
        elif d["percent_used"] > 75:
            messages.append(f"🟡 {d['mountpoint']} — {d['percent_used']}% ({d['free_display']} free)")
        else:
            messages.append(f"🟢 {d['mountpoint']} — {d['percent_used']}% ({d['free_display']} free)")

    # Temp files
    total_temp = sum(t["size_bytes"] for t in temp)
    if total_temp > 500 * 1024 * 1024:
        messages.append(f"🟡 Temp/Cache: {storage._format_bytes(total_temp)} of cleanable files")
        for t in temp:
            if t["size_bytes"] > 50 * 1024 * 1024:
                actions.append({
                    "type": "delete_folder_contents",
                    "path": t["path"],
                    "name": t["name"],
                    "size": t["size_display"],
                    "label": f"Clean {t['name']} ({t['size_display']})",
                })

    # Startup
    if len(startup_items) > 10:
        messages.append(f"🟡 Startup: {len(startup_items)} programs — consider reducing")
        issues += 1
    else:
        messages.append(f"🟢 Startup: {len(startup_items)} programs")

    # Battery
    if battery:
        if battery["percent"] < 20 and not battery["power_plugged"]:
            messages.append(f"🔴 Battery: {battery['percent']}% — LOW! Plug in your charger!")
            issues += 1
        else:
            status = "Charging" if battery["power_plugged"] else f"{battery['time_left']} remaining"
            messages.append(f"🟢 Battery: {battery['percent']}% — {status}")

    messages.append("\n" + "─" * 40)
    if issues == 0:
        messages.append("✅ **Your PC is healthy!** No critical issues found.")
    elif issues <= 2:
        messages.append(f"⚠️ **{issues} issue(s) detected.** Check the items marked with 🔴 above.")
    else:
        messages.append(f"🚨 **{issues} issues detected!** Your PC needs attention.")

    return {
        "title": "Full System Diagnosis",
        "messages": messages,
        "actions": actions,
    }


def handle_security():
    """Check security status."""
    sec = security.get_security_status()
    messages = ["🛡️ **Security Status Report**\n"]

    d = sec["defender"]
    icon = "🟢" if d["status_level"] == "good" else "🔴" if d["status_level"] == "danger" else "🟡"
    messages.append(f"{icon} **Defender:** {d['message']}")
    if d.get("last_scan"):
        messages.append(f"   Last scan: {d['last_scan']}")

    f = sec["firewall"]
    icon = "🟢" if f["status_level"] == "good" else "🔴"
    messages.append(f"{icon} **Firewall:** {f['message']}")
    for p in f.get("profiles", []):
        st = "✅ ON" if p["enabled"] else "❌ OFF"
        messages.append(f"   • {p['name']}: {st}")

    u = sec["updates"]
    icon = "🟢" if u["status_level"] == "good" else "🟡"
    messages.append(f"\n{icon} **Updates:** {u['message']}")
    for upd in u.get("recent_updates", [])[:3]:
        messages.append(f"   • {upd['id']} — {upd['installed_on']}")

    return {"title": "Security Status", "messages": messages, "actions": []}


def handle_temperature():
    """Check system temperatures."""
    temps = health.get_temperatures()
    messages = ["🌡️ **Temperature Report**\n"]

    if not temps["available"]:
        messages.append("⚠️ Temperature sensors not accessible. Try running as Administrator.")
        messages.append("\n💡 **Tip:** If your laptop feels hot, check that vents are not blocked and consider cleaning the fans.")
    else:
        messages.append(f"{temps['message']}")
        for s in temps["sensors"]:
            t = s["temperature_c"]
            icon = "🟢" if t < 60 else "🟡" if t < 80 else "🔴"
            messages.append(f"   {icon} {s['name']}: {t}°C / {s['temperature_f']}°F")

    return {"title": "Temperature Check", "messages": messages, "actions": []}


def handle_battery_health():
    """Check battery degradation."""
    bh = health.get_battery_health()
    messages = ["🔋 **Battery Health Report**\n"]

    if not bh["available"]:
        messages.append(bh["message"])
    else:
        messages.append(f"{bh['message']}")
        if bh.get("design_capacity_mwh") and bh.get("full_charge_capacity_mwh"):
            messages.append(f"   • Original capacity: {bh['design_capacity_mwh']} mWh")
            messages.append(f"   • Current max capacity: {bh['full_charge_capacity_mwh']} mWh")
            if bh.get("health_percent"):
                messages.append(f"   • Health: **{bh['health_percent']}%**")
            if bh.get("degradation_percent"):
                messages.append(f"   • Degradation: **{bh['degradation_percent']}%**")
        if bh.get("cycle_count"):
            messages.append(f"   • Cycle count: {bh['cycle_count']}")
        messages.append(f"   • Current charge: {bh.get('current_percent', '?')}%")
        messages.append(f"   • Plugged in: {'Yes' if bh.get('power_plugged') else 'No'}")

    return {"title": "Battery Health", "messages": messages, "actions": []}


def handle_speed_test():
    """Run internet speed test."""
    messages = ["🌐 **Internet Speed Test**\n", "Testing download speed..."]
    result = tools.speed_test()

    messages = ["🌐 **Internet Speed Test Results**\n"]
    if result["success"]:
        mbps = result["download_mbps"]
        icon = "🟢" if mbps > 20 else "🟡" if mbps > 5 else "🔴"
        messages.append(f"{icon} **Download: {mbps} Mbps**")
        if result.get("latency_ms"):
            lat = result["latency_ms"]
            icon_l = "🟢" if lat < 50 else "🟡" if lat < 100 else "🔴"
            messages.append(f"{icon_l} **Latency: {lat} ms**")
        messages.append(f"   Test file: {result.get('download_size', 'N/A')} in {result.get('download_time', '?')}s")
    else:
        messages.append("🔴 Speed test failed. Check your internet connection.")

    return {"title": "Speed Test", "messages": messages, "actions": []}


def handle_duplicates():
    """Find duplicate files."""
    drives = storage.get_drive_usage()
    messages = ["🔍 **Duplicate File Scan**\n"]
    actions = []

    # Scan the largest non-system drive
    target = "C:\\"
    for d in drives:
        if d["mountpoint"] != "C:\\":
            target = d["mountpoint"]
            break

    result = tools.find_duplicates(target, min_size_mb=10)
    messages.append(f"Scanned **{result['scanned_files']}** files on {target}")
    messages.append(f"{result['message']}\n")

    for dup in result.get("duplicates", [])[:10]:
        messages.append(f"📄 **{dup['count']} copies** — {dup['size_display']} each (wasting {dup['wasted_display']})")
        for f in dup["files"]:
            messages.append(f"   • `{f['path']}`")
            actions.append({
                "type": "delete_file",
                "path": f["path"],
                "name": f["name"],
                "size": dup["size_display"],
                "label": f"Delete {f['name']} ({dup['size_display']})",
            })

    return {"title": "Duplicate Files", "messages": messages, "actions": actions}


def handle_system_boost():
    """Run 1-click system boost."""
    result = tools.system_boost()
    messages = ["⚡ **System Boost Complete!**\n"]
    messages.append(f"Freed **{result['freed_display']}** of space.")
    messages.append(f"\nActions performed:")
    for action in result["actions"]:
        messages.append(f"   ✅ {action}")

    return {"title": "System Boost", "messages": messages, "actions": []}


def handle_crash_logs():
    """Analyze crash and BSOD logs."""
    crashes = health.get_crash_logs()
    messages = ["💥 **Crash & Error Log Analysis**\n"]
    messages.append(crashes["message"])

    if crashes["events"]:
        messages.append("")
        for event in crashes["events"][:10]:
            icon = "🔴" if event["level"] == "Critical" else "🟡"
            messages.append(f"{icon} **{event['level']}** — {event['time']}")
            messages.append(f"   Source: {event['source']} (Event ID: {event['event_id']})")
            if event["message"]:
                short_msg = event["message"][:150] + "..." if len(event["message"]) > 150 else event["message"]
                messages.append(f"   {short_msg}")
            messages.append("")
    else:
        messages.append("\n✅ No crashes detected. Your system is stable!")

    return {"title": "Crash Analysis", "messages": messages, "actions": []}


def handle_unknown():
    """Handle unrecognized questions."""
    return {
        "title": "How can I help?",
        "messages": [
            "🤔 I'm not sure what you mean. Try asking me things like:\n",
            '   • **"Why is my storage low?"** — Storage analysis',
            '   • **"What is slowing my PC?"** — Performance check',
            '   • **"Show large files"** — Find space wasters',
            '   • **"Clean temp files"** — Remove junk',
            '   • **"Check my RAM"** — Memory analysis',
            '   • **"CPU usage"** — Processor check',
            '   • **"Check internet"** — Network diagnostics',
            '   • **"Startup programs"** — Boot optimizer',
            '   • **"Diagnose my PC"** — Full health check',
            '   • **"Security check"** — Defender & Firewall',
            '   • **"Check temperature"** — CPU thermals',
            '   • **"Battery health"** — Degradation report',
            '   • **"Speed test"** — Internet speed',
            '   • **"Find duplicates"** — Duplicate file scan',
            '   • **"System boost"** — 1-click optimization',
            '   • **"Crash logs"** — BSOD & error analysis',
        ],
        "actions": [],
    }


# ─── MAIN CHAT FUNCTION ──────────────────────────────────────

def process_question(question: str) -> dict:
    """Process a user question and return a diagnostic response."""
    intent = detect_intent(question)
    handler_name = intent["handler"]

    # Call the appropriate handler
    handler = globals().get(handler_name, handle_unknown)
    result = handler()

    return {
        "intent": intent.get("id", "unknown"),
        "question": question,
        **result,
    }
