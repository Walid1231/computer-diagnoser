"""
Computer Diagnoser - Backend API Server v2.0
Serves diagnostic data to the frontend dashboard via FastAPI.

Endpoints are plain `def` (not `async def`) on purpose: they run blocking work
(disk scans, PowerShell, HTTP calls), and FastAPI runs `def` endpoints in a thread
pool. As `async def` they froze the entire server - and so the whole UI - for as
long as any one scan took.
"""

import os
import sys
from typing import List
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from diagnostics import storage, system, network, startup
from diagnostics.chat_engine import process_question
from diagnostics.ai_chat import process_ai_question
from diagnostics.config import load_config, save_config, is_ai_enabled
from diagnostics import health, security, tools
from diagnostics.version import __version__
from diagnostics import updater

app = FastAPI(title="Computer Diagnoser", version=__version__)

# ─── LOCAL-ONLY SECURITY ──────────────────────────────────────
# This server can delete files and kill processes, so web pages open in the user's
# browser must not be able to talk to it. The old `allow_origins=["*"]` CORS setting let
# ANY website do exactly that. Now:
#  * only loopback Host headers are served (blocks DNS-rebinding attacks), and
#  * a request carrying an Origin header must come from this very server.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _hostname(host_header: str) -> str:
    host = (host_header or "").lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0]


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    host = request.headers.get("host", "")
    if _hostname(host) not in _LOOPBACK_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)

    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc.lower() != host.lower():
        return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)

    response = await call_next(request)
    # The UI ships inside the app: never serve a stale cached copy after an update.
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Serve frontend static files
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def serve_frontend():
    """Serve the main dashboard HTML."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/health")
def api_health_check():
    """Cheap readiness probe used by the desktop launcher."""
    return {"status": "ok", "version": __version__}


def _checked_dir(path: str) -> str:
    """Validate a folder path coming from the client."""
    if not path or not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Folder not found.")
    return path


# ─── SYSTEM ENDPOINTS ─────────────────────────────────────────

@app.get("/api/system/info")
def api_system_info():
    return system.get_system_info()


@app.get("/api/system/cpu")
def api_cpu_info():
    return system.get_cpu_info()


@app.get("/api/system/memory")
def api_memory_info():
    return system.get_memory_info()


@app.get("/api/system/processes")
def api_top_processes(top_n: int = 15, sort: str = "memory"):
    return system.get_top_processes(max(1, min(top_n, 100)), sort)


@app.get("/api/system/battery")
def api_battery_info():
    info = system.get_battery_info()
    if info is None:
        return {"available": False}
    return {**info, "available": True}


# ─── STORAGE ENDPOINTS ────────────────────────────────────────

@app.get("/api/storage/drives")
def api_drives():
    return storage.get_drive_usage()


@app.get("/api/storage/scan")
def api_scan(path: str = "C:\\", top_n: int = 30, min_size_mb: float = 50):
    """One parallel pass returning file types, folder sizes and large files together."""
    try:
        return storage.scan_path(_checked_dir(path), max(1, min(top_n, 200)), max(1.0, min_size_mb))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/storage/category")
def api_explore_category(path: str = "C:\\", category: str = "Other", top_n: int = 40):
    """What is inside a file category (e.g. "Other"): locations, extensions, biggest files,
    each marked safe / review / keep."""
    try:
        return storage.explore_category(_checked_dir(path), category, max(5, min(top_n, 100)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/storage/browse")
def api_browse_folder(path: str = "C:\\"):
    """List one folder's contents with sizes and a safe / review / keep verdict per item."""
    try:
        return storage.browse_folder(_checked_dir(path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/storage/large-files")
def api_large_files(path: str = "C:\\", top_n: int = 30, min_size_mb: float = 50):
    return storage.get_large_files(_checked_dir(path), max(1, min(top_n, 200)), max(1.0, min_size_mb))


@app.get("/api/storage/folder-sizes")
def api_folder_sizes(path: str = "C:\\"):
    return storage.get_folder_sizes(_checked_dir(path))


@app.get("/api/storage/file-types")
def api_file_types(path: str = "C:\\"):
    return storage.get_file_type_breakdown(_checked_dir(path))


@app.get("/api/storage/temp-files")
def api_temp_files():
    return storage.get_temp_files()


@app.get("/api/storage/duplicates")
def api_find_duplicates(path: str = "C:\\", min_size_mb: float = 10):
    return tools.find_duplicates(_checked_dir(path), max(1.0, min_size_mb))


# ─── NETWORK ENDPOINTS ────────────────────────────────────────

@app.get("/api/network/interfaces")
def api_network_interfaces():
    return network.get_network_info()


@app.get("/api/network/connections")
def api_network_connections():
    return network.get_active_connections()


@app.get("/api/network/ping")
def api_ping(host: str = "8.8.8.8"):
    return network.ping_test(host)


@app.get("/api/network/speedtest")
def api_speed_test():
    return tools.speed_test()


# ─── STARTUP ENDPOINTS ────────────────────────────────────────

@app.get("/api/startup/programs")
def api_startup_programs():
    return startup.get_startup_programs()


class StartupToggle(BaseModel):
    name: str
    action: str  # "enable" or "disable"
    scope: str
    location: str
    source: str = "Registry"
    key: str = ""


@app.post("/api/startup/toggle")
def api_toggle_startup(req: StartupToggle):
    return tools.toggle_startup_item(req.name, req.action, req.scope, req.location, req.source, req.key)


# ─── HEALTH ENDPOINTS ─────────────────────────────────────────

@app.get("/api/health/temperatures")
def api_temperatures():
    return health.get_temperatures()


@app.get("/api/health/battery")
def api_battery_health():
    return health.get_battery_health()


@app.get("/api/health/disks")
def api_disk_health():
    return health.get_disk_health()


@app.get("/api/health/crashes")
def api_crash_logs():
    return health.get_crash_logs()


@app.get("/api/health/programs")
def api_installed_programs():
    return health.get_installed_programs()


# ─── SECURITY ENDPOINTS ───────────────────────────────────────

@app.get("/api/security/status")
def api_security_status():
    return security.get_security_status()


@app.get("/api/security/defender")
def api_defender():
    return security.get_defender_status()


@app.get("/api/security/firewall")
def api_firewall():
    return security.get_firewall_status()


@app.get("/api/security/updates")
def api_updates():
    return security.get_update_status()


# ─── TOOLS ENDPOINTS ──────────────────────────────────────────

class KillRequest(BaseModel):
    pid: int


@app.post("/api/tools/kill-process")
def api_kill_process(req: KillRequest):
    return tools.kill_process(req.pid)


@app.post("/api/tools/system-boost")
def api_system_boost():
    return tools.system_boost()


# ─── CHAT ENDPOINT ────────────────────────────────────────────

class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str
    history: List[ChatTurn] = []


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """Process a question. Uses AI if configured, otherwise rule engine."""
    if is_ai_enabled():
        return process_ai_question(req.question, req.history)
    return process_question(req.question)


# ─── CONFIG / SETTINGS ENDPOINTS ──────────────────────────────

class ConfigUpdate(BaseModel):
    ai_provider: str = ""
    api_key: str = ""
    endpoint_url: str = ""
    model_name: str = ""
    api_format: str = "openai"
    ai_enabled: bool = False


@app.get("/api/config")
def api_get_config():
    """Get current configuration (API key is masked)."""
    config = load_config()
    key = config.get("api_key", "")
    masked = ""
    if key:
        masked = key[:4] + "*" * (len(key) - 8) + key[-4:] if len(key) > 12 else "****"
    return {
        "ai_provider": config.get("ai_provider", ""),
        "endpoint_url": config.get("endpoint_url", ""),
        "model_name": config.get("model_name", ""),
        "api_format": config.get("api_format", "openai"),
        "api_key_masked": masked,
        "has_key": bool(key),
        "ai_enabled": config.get("ai_enabled", False),
        # What actually decides AI vs rule engine. Local models (Ollama, LM Studio) have no
        # key, so the UI must not infer "active" from has_key.
        "ai_active": is_ai_enabled(),
    }


@app.get("/api/config/presets")
def api_get_presets():
    """Get all available LLM provider presets."""
    from diagnostics.config import get_presets
    presets = get_presets()
    return {k: {"label": v["label"], "endpoint_url": v["endpoint_url"],
                "model_name": v["model_name"], "api_format": v["api_format"],
                "needs_key": v["needs_key"]} for k, v in presets.items()}


@app.post("/api/config")
def api_save_config(req: ConfigUpdate):
    """Save configuration."""
    config = load_config()
    if req.ai_provider:
        config["ai_provider"] = req.ai_provider
    if req.api_key:
        config["api_key"] = req.api_key
    if req.endpoint_url:
        config["endpoint_url"] = req.endpoint_url
    if req.model_name:
        config["model_name"] = req.model_name
    config["api_format"] = req.api_format
    config["ai_enabled"] = req.ai_enabled

    success = save_config(config)
    return {"success": success, "message": "Settings saved!" if success else "Failed to save settings."}


@app.post("/api/config/test")
def api_test_config():
    """Test the current AI configuration with a simple query."""
    if not is_ai_enabled():
        return {"success": False, "error": "AI is not configured. Save your settings first."}

    result = process_ai_question("Say hello in one sentence to confirm you're working.")
    if result.get("intent") == "ai_response":
        return {"success": True, "message": "AI connection working!", "response": result["messages"][0][:200]}
    return {"success": False, "error": result.get("messages", ["Unknown error"])[0]}


# ─── DELETE / CLEAN ENDPOINTS ─────────────────────────────────
# The safety rules live in diagnostics.storage so they are unit-testable:
#  * delete-file refuses system locations (Windows, Program Files, drive roots...).
#  * clean-folder only accepts folders from storage.cleanup_targets() or folders the classifier
#    rates "safe" and "cleanable" (re-checked here on the server). Before, it accepted almost
#    any path, so one bad request could empty Documents or a whole drive.

class DeleteFileRequest(BaseModel):
    path: str


class DeleteFolderContentsRequest(BaseModel):
    path: str


@app.post("/api/action/delete-file")
def api_delete_file(req: DeleteFileRequest):
    """Delete a single file. Protected system paths are blocked."""
    return storage.delete_file(req.path)


@app.post("/api/action/clean-folder")
def api_clean_folder(req: DeleteFolderContentsRequest):
    """Empty a temp/cache folder that is safe to clean. The folder itself is kept."""
    return storage.clean_target(req.path)


@app.post("/api/action/reveal")
def api_reveal(req: DeleteFolderContentsRequest):
    """Show a file or folder in Windows Explorer."""
    return storage.reveal_in_explorer(req.path)


# ─── UPDATE ENDPOINTS ─────────────────────────────────────────

_update_cache = {}  # Stores download info between check and install


@app.get("/api/update/check")
def api_check_update():
    """Check GitHub for a newer version."""
    result = updater.check_for_update()
    if result.get("available") and result.get("download_url"):
        _update_cache["download_url"] = result["download_url"]
        _update_cache["sha256"] = result.get("sha256")
        _update_cache["latest_version"] = result["latest_version"]
    return result


@app.post("/api/update/install")
def api_install_update():
    """Download and install the latest update."""
    url = _update_cache.get("download_url")
    sha256 = _update_cache.get("sha256")
    if not url:
        check = updater.check_for_update()
        if not check.get("available"):
            return {"success": False, "error": "No update available."}
        url = check.get("download_url")
        sha256 = check.get("sha256")
        if not url:
            return {"success": False, "error": "No download URL found in the release."}

    dl_result = updater.download_update(url, sha256)
    if not dl_result["success"]:
        return dl_result

    # Install (launches the batch script; this app then exits)
    install_result = updater.install_update(dl_result["updater_path"])
    if install_result["success"]:
        import threading

        def shutdown_later():
            import time
            time.sleep(2)  # let the HTTP response reach the UI first
            os._exit(0)
        threading.Thread(target=shutdown_later, daemon=True).start()

    return install_result


@app.get("/api/version")
def api_version():
    """Return the current app version."""
    return {"version": __version__}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8888)
