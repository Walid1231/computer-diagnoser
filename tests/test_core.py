"""
Tests for the Computer Diagnoser backend (pure-Python logic; no Windows required).

Run from the repository root:
    pip install pytest
    python -m pytest tests -q
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from diagnostics import ai_chat, chat_engine, classifier, health, network, security, startup, storage, system, tools, updater
from diagnostics.utils import format_bytes, ttl_cache


def write(path, size, fill=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(fill * size)


@pytest.fixture(autouse=True)
def fresh_caches():
    storage.invalidate_caches()
    yield
    storage.invalidate_caches()


# ─── utils ───────────────────────────────────────────────────

def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(5 * 1024 ** 3) == "5.00 GB"
    assert format_bytes(3 * 1024 ** 5) == "3.00 PB"


def test_ttl_cache_expires_and_clears():
    calls = []

    @ttl_cache(0.05)
    def f(x):
        calls.append(x)
        return x * 2

    assert f(2) == 4 and f(2) == 4
    assert calls == [2]
    time.sleep(0.07)
    f(2)
    assert calls == [2, 2]
    f.cache_clear()
    f(2)
    assert calls == [2, 2, 2]


# ─── storage: scanning ───────────────────────────────────────

def build_tree(root):
    write(f"{root}/Users/a/Downloads/movie.mp4", 3_000_000)
    write(f"{root}/Users/a/Downloads/doc.pdf", 10_000)
    write(f"{root}/Games/big/data.bin", 5_000_000)
    write(f"{root}/node_modules/x/huge.bin", 4_000_000)
    write(f"{root}/Windows/sys/core.dll", 9_000_000)
    write(f"{root}/rootfile.zip", 2_000_000)
    write(f"{root}/pagefile.sys", 6_000_000)


def test_scan_path_single_pass(tmp_path):
    root = str(tmp_path)
    build_tree(root)
    result = storage.scan_path(root, 10, 1, 20)

    folders = {f["name"]: f["size_bytes"] for f in result["folders"]}
    assert folders["Windows"] == 9_000_000          # folders are all sized...
    assert folders["Games"] == 5_000_000
    assert result["files_scanned"] == 7
    assert not result["partial"]

    large = [f["name"] for f in result["large_files"]]
    assert "core.dll" not in large                   # ...but system dirs are never offered for deletion
    assert "huge.bin" not in large                   # node_modules noise excluded
    assert "pagefile.sys" not in large
    assert large == ["data.bin", "movie.mp4", "rootfile.zip"]


def test_scan_does_not_follow_symlinks(tmp_path):
    root = str(tmp_path)
    write(f"{root}/real/file.bin", 1_000_000)
    try:
        os.symlink(f"{root}/real", f"{root}/link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    result = storage.scan_path(root, 5, 0.1, 10)
    assert result["files_scanned"] == 1               # counted once, not twice
    assert [f["name"] for f in result["folders"]] == ["real"]


def test_scan_rejects_missing_folder(tmp_path):
    with pytest.raises(ValueError):
        storage.scan_path(str(tmp_path / "nope"))


def test_scan_honours_time_limit(tmp_path):
    for i in range(30):
        write(f"{tmp_path}/d{i}/f.bin", 10)
    result = storage.scan_path(str(tmp_path), 5, 1, time_limit=-1)   # deadline already passed
    assert result["partial"] is True


# ─── storage: deletion safety ────────────────────────────────

def test_protected_paths(tmp_path, monkeypatch):
    win = tmp_path / "Windows"
    win.mkdir()
    monkeypatch.setenv("WINDIR", str(win))
    assert storage.is_protected_path(str(win / "System32" / "x.dll"))
    assert storage.is_protected_path(str(win))
    assert storage.is_protected_path(str(tmp_path / "$Recycle.Bin" / "x"))
    assert not storage.is_protected_path(str(tmp_path / "Documents" / "x.txt"))
    # a sibling that merely *starts with* the same text must not count (old startswith bug)
    assert not storage.is_protected_path(str(tmp_path / "WindowsApps_mine" / "x"))
    assert storage.is_protected_path(os.path.abspath(os.sep))   # drive root


def test_delete_file_blocks_protected_and_missing(tmp_path, monkeypatch):
    win = tmp_path / "Windows"
    write(str(win / "core.dll"), 100)
    monkeypatch.setenv("WINDIR", str(win))
    blocked = storage.delete_file(str(win / "core.dll"))
    assert not blocked["success"] and (win / "core.dll").exists()
    assert not storage.delete_file(str(tmp_path / "missing.txt"))["success"]

    write(str(tmp_path / "ok.bin"), 2048)
    done = storage.delete_file(str(tmp_path / "ok.bin"))
    assert done["success"] and done["freed_bytes"] == 2048
    assert not (tmp_path / "ok.bin").exists()


def test_clean_folder_is_whitelisted(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    write(str(docs / "thesis.docx"), 100)
    temp = tmp_path / "Temp"
    write(str(temp / "a.tmp"), 500)
    write(str(temp / "sub" / "b.tmp"), 700)
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.delenv("TMP", raising=False)

    # The old API would have happily emptied any folder it was pointed at.
    refused = storage.clean_target(str(docs))
    assert not refused["success"]
    assert (docs / "thesis.docx").exists()
    assert not storage.clean_target(str(tmp_path))["success"]

    cleaned = storage.clean_target(str(temp))
    assert cleaned["success"] and cleaned["freed_bytes"] == 1200 and cleaned["errors"] == 0
    assert temp.exists() and list(temp.iterdir()) == []     # folder kept, contents gone


def test_clean_reports_only_what_was_removed(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    write(str(temp / "a.tmp"), 500)
    write(str(temp / "locked.tmp"), 900)
    monkeypatch.setenv("TEMP", str(temp))
    real = storage._remove_file
    monkeypatch.setattr(storage, "_remove_file", lambda p: False if p.endswith("locked.tmp") else real(p))
    result = storage.clean_directory_contents(str(temp))
    assert result["freed_bytes"] == 500 and result["errors"] == 1   # not 1400


def test_cleanup_targets_dedupe(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    names = [t["name"] for t in storage.cleanup_targets()]
    assert names.count("User Temp") == 1 and "User Temp (TMP)" not in names and "Local Temp" not in names


# ─── duplicates ──────────────────────────────────────────────

def test_duplicates_need_full_content_match(tmp_path):
    size = 400_000
    base = os.urandom(size)
    write_bytes = lambda p, data: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "wb").write(data))

    write_bytes(f"{tmp_path}/a/orig.bin", base)
    write_bytes(f"{tmp_path}/b/copy.bin", base)
    # Same size, same first and last 64 KB, different middle: the old partial hash called this a duplicate
    # (and the UI then offered to delete it).
    middle = bytearray(base)
    middle[200_000] ^= 0xFF
    write_bytes(f"{tmp_path}/c/almost.bin", bytes(middle))

    result = tools.find_duplicates(str(tmp_path), min_size_mb=0.1, time_limit=20)
    assert result["duplicate_groups"] == 1
    names = sorted(f["name"] for f in result["duplicates"][0]["files"])
    assert names == ["copy.bin", "orig.bin"]
    keep = [f for f in result["duplicates"][0]["files"] if f["keep"]]
    assert len(keep) == 1                                      # exactly one copy is protected


def test_duplicates_ignore_hardlinks(tmp_path):
    data = os.urandom(300_000)
    os.makedirs(f"{tmp_path}/a")
    with open(f"{tmp_path}/a/one.bin", "wb") as handle:
        handle.write(data)
    try:
        os.link(f"{tmp_path}/a/one.bin", f"{tmp_path}/a/two.bin")
    except (OSError, NotImplementedError):
        pytest.skip("hard links not available")
    result = tools.find_duplicates(str(tmp_path), min_size_mb=0.1, time_limit=20)
    assert result["duplicate_groups"] == 0                      # deleting one would free nothing


# ─── chat engine ─────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    ("why is my computer slow", "slow_pc"),
    ("Why is my storage low?", "storage_low"),
    ("check my ram", "memory_check"),
    ("show me my program list", "unknown"),
    ("I took a photo", "unknown"),
    ("check temperature", "temperature_check"),
    ("clean temp files", "clean_temp"),
    ("find duplicates", "find_duplicates"),
    ("why is my c drive full", "storage_low"),
    ("battery health", "battery_health"),
])
def test_intents(question, expected):
    assert chat_engine.detect_intent(question)["id"] == expected


# ─── startup (against a fake registry) ───────────────────────

class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = KEY_SET_VALUE = 1
    REG_BINARY = 3

    def __init__(self):
        self.keys = {}

    class _Key:
        def __init__(self, store, ident):
            self.store, self.ident = store, ident

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, hive, path, reserved=0, access=1):
        if (hive, path.lower()) not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self, (hive, path.lower()))

    def CreateKeyEx(self, hive, path, reserved=0, access=1):
        self.keys.setdefault((hive, path.lower()), {})
        return self._Key(self, (hive, path.lower()))

    def QueryValueEx(self, key, name):
        try:
            return self.keys[key.ident][name]
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, key, name, reserved, regtype, value):
        self.keys[key.ident][name] = (value, regtype)

    def EnumValue(self, key, index):
        items = list(self.keys[key.ident].items())
        if index >= len(items):
            raise OSError
        name, (value, regtype) = items[index]
        return name, value, regtype

    def DeleteValue(self, key, name):
        del self.keys[key.ident][name]


@pytest.fixture
def fake_registry(monkeypatch):
    reg = FakeWinreg()
    run = startup._RUN.lower()
    reg.keys[("HKCU", run)] = {"Spotify": ("C:\\spotify.exe", 1), "Adobe's Updater": ("C:\\adobe.exe", 1)}
    monkeypatch.setattr(startup, "winreg", reg)
    return reg


def test_startup_disable_and_enable_roundtrip(fake_registry):
    items = {i["name"]: i for i in startup.get_startup_programs()}
    assert items["Spotify"]["enabled"] is True and items["Spotify"]["toggleable"]

    # The location contains backslashes and the name an apostrophe: both used to break the
    # inline onclick handler, so disabling never worked.
    item = items["Adobe's Updater"]
    assert "\\" in item["location"]
    out = startup.set_startup_enabled(item["name"], False, item["scope"], item["location"], "Registry", item["key"])
    assert out["success"], out
    after = {i["name"]: i for i in startup.get_startup_programs()}
    assert after["Adobe's Updater"]["enabled"] is False
    assert after["Spotify"]["enabled"] is True
    # the Run value stays in place; only the StartupApproved flag changed (like Task Manager)
    assert "Adobe's Updater" in fake_registry.keys[("HKCU", startup._RUN.lower())]

    out = startup.set_startup_enabled(item["name"], True, item["scope"], item["location"], "Registry", item["key"])
    assert out["success"]
    assert {i["name"]: i for i in startup.get_startup_programs()}["Adobe's Updater"]["enabled"] is True


def test_startup_rejects_arbitrary_registry_locations(fake_registry):
    # The old code accepted any registry path from the client and could delete values anywhere under HKCU/HKLM.
    out = startup.set_startup_enabled("x", False, "Current User", r"Software\Evil\Key", "Registry", "x")
    assert not out["success"]
    out = startup.set_startup_enabled("SecurityHealth", False, "All Users", startup._RUN, "Registry", "SecurityHealth")
    assert not out["success"] and "protected" in out["error"].lower()
    assert not startup.set_startup_enabled("x", False, "Current User", startup._RUN, "Bogus", "x")["success"]


def test_startup_legacy_disabled_items_are_listed_and_restorable(fake_registry):
    run = startup._RUN.lower()
    fake_registry.keys[("HKCU", run + "\\autorunsdisabled")] = {"OldApp": ("C:\\old.exe", 1)}
    items = {i["name"]: i for i in startup.get_startup_programs()}
    assert items["OldApp"]["enabled"] is False
    out = startup.set_startup_enabled("OldApp", True, "Current User", startup._RUN, "Registry", "OldApp")
    assert out["success"]
    assert "OldApp" in fake_registry.keys[("HKCU", run)]
    assert "OldApp" not in fake_registry.keys[("HKCU", run + "\\autorunsdisabled")]


# ─── updater ─────────────────────────────────────────────────

def test_version_compare():
    assert updater._compare_versions("2.0.1", "2.0.2") == 1
    assert updater._compare_versions("2.0.1", "2.0.1") == 0
    assert updater._compare_versions("2.1.0", "2.0.9") == -1
    assert updater._compare_versions("2.0.1", "v2.1.0-beta") == 1     # used to raise and report "no update"
    assert updater._compare_versions("2.0.9", "2.0.10") == 1          # numeric, not string, compare


def test_download_urls_must_be_github():
    assert updater._is_trusted_url("https://github.com/Walid1231/computer-diagnoser/releases/download/v1/x.exe")
    assert updater._is_trusted_url("https://objects.githubusercontent.com/abc")
    assert not updater._is_trusted_url("http://github.com/x.exe")
    assert not updater._is_trusted_url("https://evil.example/github.com/x.exe")
    assert not updater._is_trusted_url("https://github.com.evil.example/x.exe")


def test_update_refused_when_not_packaged():
    out = updater.download_update("https://github.com/Walid1231/computer-diagnoser/releases/download/v1/x.exe")
    assert not out["success"] and "packaged" in out["error"]


def test_update_script_cannot_loop_forever(tmp_path, monkeypatch):
    """The old script read %RETRIES% inside a ( ) block, where it is expanded only once, so a
    locked exe made the updater retry forever. Verify the generated script's retry logic is
    expanded per line: no parenthesised block contains %RETRIES%, and there is an exit path."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "ComputerDiagnoser.exe"))

    class Resp:
        def __init__(self):
            self.data = b"x" * 1_100_000

        def read(self, n=-1):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: Resp())
    monkeypatch.setattr(updater, "open", open, raising=False)
    import codecs
    try:
        codecs.lookup("mbcs")
    except LookupError:
        codecs.register(lambda name: codecs.lookup("utf-8") if name == "mbcs" else None)

    import hashlib
    digest = hashlib.sha256(b"x" * 1_100_000).hexdigest()
    out = updater.download_update("https://github.com/x/y/releases/download/v1/a.exe", digest)
    assert out["success"], out
    script = open(out["updater_path"], encoding="utf-8").read()
    assert "goto failed" in script and "set /a RETRIES=%RETRIES%-1" in script
    assert "if %RETRIES% LEQ 0 goto failed" in script
    assert "timeout /t" not in script                              # fails when stdin is redirected

    bad = updater.download_update("https://github.com/x/y/releases/download/v1/a.exe", "0" * 64)
    assert not bad["success"] and "integrity" in bad["error"]


# ─── network ─────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["-f", "8.8.8.8 -t", "a;b", "", "x" * 300, "$(calc)"])
def test_ping_rejects_bad_hosts(host):
    assert not network.ping_test(host)["success"]


def test_connections_are_sorted_and_named():
    conns = network.get_active_connections(top_n=500)
    ranks = [0 if c["status"] == "ESTABLISHED" else 1 if c["remote_addr"] else 2 for c in conns]
    assert ranks == sorted(ranks)                                   # established first
    assert all("process" in c for c in conns)


# ─── system ──────────────────────────────────────────────────

def test_cpu_and_processes():
    cpu = system.get_cpu_info()
    assert len(cpu["per_cpu_percent"]) == cpu["logical_cores"]
    assert 0 <= cpu["overall_percent"] <= 100
    procs = system.get_top_processes(5)
    assert len(procs) == 5
    assert procs == sorted(procs, key=lambda p: p["memory_bytes"], reverse=True)
    assert all(p["pid"] != 0 and 0 <= p["cpu_percent"] <= 100 for p in procs)
    by_cpu = system.get_top_processes(5, sort_by="cpu")
    assert [p["cpu_percent"] for p in by_cpu] == sorted((p["cpu_percent"] for p in by_cpu), reverse=True)


def test_kill_process_refuses_self_and_core():
    assert not tools.kill_process(os.getpid())["success"]
    assert not tools.kill_process(4)["success"]
    assert not tools.kill_process(0)["success"]


# ─── PowerShell result handling (canned output) ──────────────

def test_disk_health_handles_names_and_numbers(monkeypatch):
    health.get_disk_health.cache_clear()
    monkeypatch.setattr(health, "powershell_json", lambda *a, **k: [
        {"FriendlyName": "Samsung SSD", "Size": 500_000_000_000, "Media": "SSD", "Health": "Healthy", "Op": "OK", "Bus": "NVMe"},
        {"FriendlyName": "Old HDD", "Size": 1_000_000_000_000, "Media": 3, "Health": 1, "Op": "OK", "Bus": 11},
        {"FriendlyName": "Dying", "Size": 1, "Media": "HDD", "Health": "Unhealthy", "Op": "Lost Communication", "Bus": "SATA"},
    ])
    disks = health.get_disk_health()
    assert [d["health_level"] for d in disks] == ["good", "warning", "danger"]
    assert disks[1]["health_status"] == "Warning" and disks[1]["media_type"] == "HDD" and disks[1]["bus_type"] == "SATA"
    health.get_disk_health.cache_clear()


def test_crash_events_are_classified(monkeypatch):
    health.get_crash_logs.cache_clear()
    monkeypatch.setattr(health, "powershell_json", lambda *a, **k: [
        {"Time": "2026-11-01 10:00:00", "Id": 41, "LevelDisplayName": "Critical", "ProviderName": "Microsoft-Windows-Kernel-Power", "Msg": "The system rebooted"},
        {"Time": "2026-11-01 09:00:00", "Id": 1000, "LevelDisplayName": "Error", "ProviderName": "Application Error", "Msg": "Faulting application chrome.exe"},
    ])
    result = health.get_crash_logs()
    assert result["crash_count"] == 2 and result["system_crash_count"] == 1
    assert result["events"][0]["kind"] == "Unexpected restart" and result["events"][0]["level"] == "Critical"
    assert result["events"][1]["kind"] == "App crash"
    monkeypatch.setattr(health, "powershell_json", lambda *a, **k: [])
    health.get_crash_logs.cache_clear()
    assert health.get_crash_logs()["crash_count"] == 0
    monkeypatch.setattr(health, "powershell_json", lambda *a, **k: None)
    health.get_crash_logs.cache_clear()
    assert "Could not read" in health.get_crash_logs()["message"]
    health.get_crash_logs.cache_clear()


def test_defender_with_third_party_antivirus(monkeypatch):
    security.get_defender_status.cache_clear()
    monkeypatch.setattr(security, "powershell_json", lambda *a, **k: [{
        "av": [{"name": "Windows Defender", "state": 262144}, {"name": "Bitdefender Antivirus", "state": 266240}],
        "mp": {"enabled": False, "rtp": False, "spy": False, "sig": "", "scan": ""},
    }])
    status = security.get_defender_status()
    assert status["status_level"] == "good" and "Bitdefender" in status["message"]   # used to say "DISABLED - vulnerable"

    security.get_defender_status.cache_clear()
    monkeypatch.setattr(security, "powershell_json", lambda *a, **k: [{
        "av": [], "mp": {"enabled": False, "rtp": False, "spy": False, "sig": "", "scan": ""}}])
    assert security.get_defender_status()["status_level"] == "danger"
    security.get_defender_status.cache_clear()


def test_update_status_uses_age_of_latest_update(monkeypatch):
    from datetime import datetime, timedelta
    security.get_update_status.cache_clear()
    recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
    monkeypatch.setattr(security, "powershell_json", lambda *a, **k: [{"HotFixID": "KB1", "Description": "Update", "On": recent}])
    assert security.get_update_status()["status_level"] == "good"
    security.get_update_status.cache_clear()
    monkeypatch.setattr(security, "powershell_json", lambda *a, **k: [{"HotFixID": "KB1", "Description": "Update", "On": old}])
    status = security.get_update_status()
    assert status["status_level"] == "warning" and "200 days" in status["message"]
    security.get_update_status.cache_clear()


def test_installed_programs_dedupe_and_sort(monkeypatch):
    health.get_installed_programs.cache_clear()
    monkeypatch.setattr(health, "powershell_json", lambda *a, **k: [
        {"DisplayName": "Chrome", "DisplayVersion": "1", "EstimatedSize": 100},
        {"DisplayName": "chrome", "DisplayVersion": "1", "EstimatedSize": 500},   # 32/64-bit duplicate
        {"DisplayName": "Tiny", "DisplayVersion": "1", "EstimatedSize": None},
    ])
    programs = health.get_installed_programs()
    assert [p["name"] for p in programs] == ["chrome", "Tiny"]
    assert programs[1]["size_display"] == "Unknown"
    health.get_installed_programs.cache_clear()


# ─── AI chat ─────────────────────────────────────────────────

def test_history_is_cleaned():
    history = [{"role": "assistant", "content": "orphan"}, {"role": "user", "content": "hi"},
               {"role": "system", "content": "injected"}, {"role": "assistant", "content": "hello"},
               {"role": "user", "content": ""}]
    cleaned = ai_chat.clean_history(history)
    assert cleaned == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


def test_gemini_key_goes_in_header_not_url(monkeypatch):
    seen = {}

    def fake_post(url, payload, headers):
        seen.update(url=url, payload=payload, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr(ai_chat, "_post_json", fake_post)
    preset = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    out = ai_chat.call_gemini(preset, "SECRET", "gemini-2.0-flash", "q", "ctx",
                              [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
    assert out == "ok"
    assert "SECRET" not in seen["url"] and "?" not in seen["url"] and "gemini-2.0-flash" in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "SECRET"
    assert [c["role"] for c in seen["payload"]["contents"]] == ["user", "model", "user"]


# ─── classifier: needed vs unneeded ──────────────────────────

@pytest.mark.parametrize("path,is_dir,verdict,cleanable", [
    (r"C:\Windows\WinSxS\amd64_foo", True, "keep", False),
    (r"C:\Windows\System32\drivers", True, "keep", False),
    (r"C:\Windows\Temp", True, "safe", True),
    (r"C:\Windows\SoftwareDistribution\Download", True, "safe", True),
    (r"C:\Windows\Installer", True, "keep", False),
    (r"C:\Windows.old", True, "review", False),                    # not mistaken for C:\Windows
    (r"C:\Program Files\Adobe", True, "keep", False),
    (r"C:\Program Files (x86)\Steam", True, "keep", False),
    (r"C:\ProgramData\Package Cache", True, "review", False),
    (r"C:\ProgramData\Foo", True, "keep", False),
    (r"C:\Users\Walid\AppData\Local\Temp", True, "safe", True),
    (r"C:\Users\Walid\AppData\Local\Temp\sub", True, "safe", True),
    (r"C:\Users\Walid\AppData\Local\Google\Chrome\User Data\Default\Cache", True, "safe", True),
    (r"C:\Users\Walid\AppData\Local\Google\Chrome\User Data\Default\Code Cache", True, "safe", True),
    (r"C:\Users\Walid\AppData\Local\Google\Chrome\User Data\Default", True, "keep", False),   # bookmarks, passwords
    (r"C:\Users\Walid\AppData\Local\Programs\Python", True, "keep", False),
    (r"C:\Users\Walid\AppData\Roaming\Code", True, "review", False),
    (r"C:\Users\Walid\Documents\thesis", True, "keep", False),
    (r"C:\Users\Walid\Documents\proj\__pycache__", True, "safe", True),
    (r"C:\Users\Walid\Downloads", True, "review", False),
    (r"C:\Users\Walid\.cache\huggingface\hub", True, "review", False),    # large but re-downloadable: user decides
    (r"C:\Users\Walid\proj\.git\objects", True, "keep", False),
    (r"C:\Users\Walid\proj\node_modules", True, "review", False),
    (r"C:\Users\Walid\anaconda3\envs\torch", True, "keep", False),
    (r"C:\Users\Walid\anaconda3\pkgs", True, "review", False),
    (r"C:\Users\Walid\Temp", True, "review", False),                # a user folder merely *named* Temp is not junk
    (r"C:\Temp", True, "review", False),
    (r"D:\games\stuff", True, "review", False),
])
def test_folder_verdicts(path, is_dir, verdict, cleanable):
    info = classifier.classify(path, is_dir)
    assert (info["verdict"], info["cleanable"]) == (verdict, cleanable), info


@pytest.mark.parametrize("path,verdict", [
    (r"C:\pagefile.sys", "keep"),
    (r"C:\hiberfil.sys", "keep"),
    (r"C:\Windows\System32\kernel32.dll", "keep"),
    (r"C:\Program Files\App\x.tmp", "keep"),                        # a .tmp in a protected place stays protected
    (r"C:\Users\Walid\AppData\Local\Temp\a.tmp", "safe"),
    (r"C:\Users\Walid\Desktop\notes.tmp", "keep"),                  # personal folders are never junk
    (r"C:\Users\Walid\Projects\x.dmp", "safe"),
    (r"C:\Users\Walid\Projects\~$report.docx", "safe"),             # Office lock file
    (r"C:\Users\Walid\Downloads\setup.exe", "review"),
    (r"C:\Users\Walid\Downloads\ubuntu.iso", "review"),
    (r"C:\Users\Walid\Projects\model.bin", "review"),
    (r"D:\backup\db.bak", "review"),
])
def test_file_verdicts(path, verdict):
    assert classifier.classify(path, False)["verdict"] == verdict


def test_every_verdict_has_an_explanation():
    for path, is_dir in [(r"C:\Windows\WinSxS", True), (r"C:\x\y", True), (r"C:\x\a.tmp", False), (r"C:\x\y.bak", False)]:
        info = classifier.classify(path, is_dir)
        assert info["label"] and len(info["reason"]) > 10


def test_hibernation_hint_never_suggests_deleting():
    assert "powercfg" in classifier.classify(r"C:\hiberfil.sys", False)["reason"]


# ─── explorer: category / browse / smart clean ───────────────

def test_explore_category_splits_other_by_verdict(tmp_path):
    root = str(tmp_path)
    write(f"{root}/proj/data.xyz", 1_000_000)                      # unknown type -> "Other", review
    write(f"{root}/proj/__pycache__/m.pyc", 400_000)               # regenerable -> safe
    write(f"{root}/proj/.git/objects/pack.idx", 300_000)           # repository history -> keep
    write(f"{root}/proj/notes.txt", 10_000)                        # Document, not Other

    result = storage.explore_category(root, "Other", top_n=10, time_limit=20)
    assert result["files"] == 3
    assert result["summary"]["safe"]["size_bytes"] == 400_000
    assert result["summary"]["review"]["size_bytes"] == 1_000_000
    assert result["summary"]["keep"]["size_bytes"] == 300_000
    assert result["total_bytes"] == 1_700_000

    # agrees with the main scan's "Other" figure
    scan_other = next(t for t in storage.scan_path(root, 5, 1, 20)["file_types"] if t["category"] == "Other")
    assert scan_other["size_bytes"] == result["total_bytes"] and scan_other["count"] == result["files"]

    names = [f["name"] for f in result["largest_files"]]
    assert names == ["data.xyz", "m.pyc", "pack.idx"]
    by_name = {f["name"]: f for f in result["largest_files"]}
    assert by_name["m.pyc"]["verdict"] == "safe" and by_name["m.pyc"]["can_delete"]
    assert by_name["pack.idx"]["verdict"] == "keep" and not by_name["pack.idx"]["can_delete"]
    exts = {e["extension"]: e for e in result["extensions"]}
    assert exts[".pyc"]["verdict"] == "safe" and exts[".xyz"]["verdict"] == "review"
    assert sum(l["size_bytes"] for l in result["locations"]) == 1_700_000   # locations add up to the total
    assert result["locations"][0]["size_bytes"] == 1_000_000


def test_explore_category_temp_files_are_safe(tmp_path):
    write(f"{tmp_path}/a/x.tmp", 5000)
    result = storage.explore_category(str(tmp_path), "Temporary", top_n=5, time_limit=20)
    assert result["summary"]["safe"]["size_bytes"] == 5000


def test_explore_category_validates_input(tmp_path):
    with pytest.raises(ValueError):
        storage.explore_category(str(tmp_path), "Nonsense")
    with pytest.raises(ValueError):
        storage.explore_category(str(tmp_path / "missing"), "Other")


def test_browse_folder(tmp_path):
    root = str(tmp_path)
    write(f"{root}/big/a.bin", 2_000_000)
    write(f"{root}/big/deep/b.bin", 1_000_000)
    write(f"{root}/small/c.bin", 10)
    write(f"{root}/top.tmp", 500)
    result = storage.browse_folder(root)
    assert [i["name"] for i in result["items"]] == ["big", "top.tmp", "small"]
    big = result["items"][0]
    assert big["is_dir"] and big["size_bytes"] == 3_000_000
    assert result["items"][1]["verdict"] == "safe" and result["items"][1]["can_delete"]
    assert result["total_bytes"] == 3_000_510 and result["parent"] == os.path.dirname(root)
    inner = storage.browse_folder(f"{root}/big")
    assert [i["name"] for i in inner["items"]] == ["a.bin", "deep"] or [i["name"] for i in inner["items"]] == ["deep", "a.bin"]
    with pytest.raises(ValueError):
        storage.browse_folder(f"{root}/nope")


def test_browse_skips_links(tmp_path):
    write(f"{tmp_path}/real/f.bin", 1000)
    try:
        os.symlink(f"{tmp_path}/real", f"{tmp_path}/link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert [i["name"] for i in storage.browse_folder(str(tmp_path))["items"]] == ["real"]


def test_clean_target_accepts_only_safe_cleanable_folders(tmp_path):
    cache = tmp_path / "proj" / "__pycache__"
    write(str(cache / "a.pyc"), 700)
    write(str(cache / "sub" / "b.pyc"), 300)
    write(str(tmp_path / "proj" / "main.py"), 50)
    git = tmp_path / "proj" / ".git"
    write(str(git / "HEAD"), 20)
    node = tmp_path / "proj" / "node_modules"
    write(str(node / "x" / "index.js"), 99)

    ok = storage.clean_target(str(cache))
    assert ok["success"] and ok["freed_bytes"] == 1000 and cache.exists() and not list(cache.iterdir())
    assert (tmp_path / "proj" / "main.py").exists()                 # neighbours untouched

    for refused in (git, node, tmp_path / "proj", tmp_path):         # keep / review / unknown folders
        out = storage.clean_target(str(refused))
        assert not out["success"], refused
    assert (git / "HEAD").exists() and (node / "x" / "index.js").exists()


def test_clean_target_cannot_be_tricked_with_path_tricks(tmp_path):
    cache = tmp_path / "proj" / "__pycache__"
    write(str(cache / "a.pyc"), 10)
    write(str(tmp_path / "proj" / "main.py"), 50)
    # "..\" segments resolve to the project folder, which is not safe-to-clean
    out = storage.clean_target(str(cache) + os.sep + "..")
    assert not out["success"] and (tmp_path / "proj" / "main.py").exists()
    # a link that points at a cache folder is refused outright
    try:
        os.symlink(str(cache), str(tmp_path / "link"), target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    assert not storage.clean_target(str(tmp_path / "link"))["success"]
    assert (cache / "a.pyc").exists()


def test_clean_target_never_cleans_protected_locations(tmp_path, monkeypatch):
    win = tmp_path / "Windows"
    write(str(win / "Prefetch" / "x.pf"), 10)
    monkeypatch.setenv("WINDIR", str(win))
    monkeypatch.setenv("SystemRoot", str(win))
    assert not storage.clean_target(str(win / "Prefetch"))["success"]
    assert (win / "Prefetch" / "x.pf").exists()


def test_location_grouping_goes_deeper_inside_users():
    j = lambda *p: os.sep.join(p)
    root = j("", "data") if os.sep == "/" else "C:\\"
    base = root.rstrip(os.sep)
    assert storage._group_key(root, j(base, "Windows", "System32", "drivers", "etc")) == j(base, "Windows", "System32", "drivers")
    assert storage._group_key(root, j(base, "Users", "Walid", "AppData", "Local", "Google", "Chrome", "Cache")) == \
        j(base, "Users", "Walid", "AppData", "Local", "Google")
    assert storage._group_key(root, root) == root.rstrip("\\/") + os.sep
