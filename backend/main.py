"""
Computer Diagnoser - Backend API Server v2.0
Serves diagnostic data to the frontend dashboard via FastAPI.
"""

import os
import shutil
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from diagnostics import storage, system, network, startup
from diagnostics.chat_engine import process_question
from diagnostics.ai_chat import process_ai_question
from diagnostics.config import load_config, save_config, is_ai_enabled
from diagnostics import health, security, tools
from diagnostics.version import __version__
from diagnostics import updater

app = FastAPI(title="Computer Diagnoser", version=__version__)

# Allow CORS for local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def serve_frontend():
    """Serve the main dashboard HTML."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ─── SYSTEM ENDPOINTS ─────────────────────────────────────────────

@app.get("/api/system/info")
async def api_system_info():
    return system.get_system_info()


@app.get("/api/system/cpu")
async def api_cpu_info():
    return system.get_cpu_info()


@app.get("/api/system/memory")
async def api_memory_info():
    return system.get_memory_info()


@app.get("/api/system/processes")
async def api_top_processes():
    return system.get_top_processes()


@app.get("/api/system/battery")
async def api_battery_info():
    info = system.get_battery_info()
    if info is None:
        return {"available": False}
    return {**info, "available": True}


# ─── STORAGE ENDPOINTS ────────────────────────────────────────────

@app.get("/api/storage/drives")
async def api_drives():
    return storage.get_drive_usage()


@app.get("/api/storage/large-files")
async def api_large_files(path: str = "C:\\", top_n: int = 30, min_size_mb: float = 50):
    return storage.get_large_files(path, top_n, min_size_mb)


@app.get("/api/storage/folder-sizes")
async def api_folder_sizes(path: str = "C:\\"):
    return storage.get_folder_sizes(path)


@app.get("/api/storage/file-types")
async def api_file_types(path: str = "C:\\"):
    return storage.get_file_type_breakdown(path)


@app.get("/api/storage/temp-files")
async def api_temp_files():
    return storage.get_temp_files()


@app.get("/api/storage/duplicates")
async def api_find_duplicates(path: str = "C:\\", min_size_mb: float = 10):
    return tools.find_duplicates(path, min_size_mb)


# ─── NETWORK ENDPOINTS ────────────────────────────────────────────

@app.get("/api/network/interfaces")
async def api_network_interfaces():
    return network.get_network_info()


@app.get("/api/network/connections")
async def api_network_connections():
    return network.get_active_connections()


@app.get("/api/network/ping")
async def api_ping(host: str = "8.8.8.8"):
    return network.ping_test(host)


@app.get("/api/network/speedtest")
async def api_speed_test():
    return tools.speed_test()


# ─── STARTUP ENDPOINTS ────────────────────────────────────────────

@app.get("/api/startup/programs")
async def api_startup_programs():
    return startup.get_startup_programs()


class StartupToggle(BaseModel):
    name: str
    action: str  # "enable" or "disable"
    scope: str
    location: str

@app.post("/api/startup/toggle")
async def api_toggle_startup(req: StartupToggle):
    return tools.toggle_startup_item(req.name, req.action, req.scope, req.location)


# ─── HEALTH ENDPOINTS ─────────────────────────────────────────────

@app.get("/api/health/temperatures")
async def api_temperatures():
    return health.get_temperatures()


@app.get("/api/health/battery")
async def api_battery_health():
    return health.get_battery_health()


@app.get("/api/health/disks")
async def api_disk_health():
    return health.get_disk_health()


@app.get("/api/health/crashes")
async def api_crash_logs():
    return health.get_crash_logs()


@app.get("/api/health/programs")
async def api_installed_programs():
    return health.get_installed_programs()


# ─── SECURITY ENDPOINTS ───────────────────────────────────────────

@app.get("/api/security/status")
async def api_security_status():
    return security.get_security_status()


@app.get("/api/security/defender")
async def api_defender():
    return security.get_defender_status()


@app.get("/api/security/firewall")
async def api_firewall():
    return security.get_firewall_status()


@app.get("/api/security/updates")
async def api_updates():
    return security.get_update_status()


# ─── TOOLS ENDPOINTS ──────────────────────────────────────────────

class KillRequest(BaseModel):
    pid: int

@app.post("/api/tools/kill-process")
async def api_kill_process(req: KillRequest):
    return tools.kill_process(req.pid)


@app.post("/api/tools/system-boost")
async def api_system_boost():
    return tools.system_boost()


# ─── CHAT ENDPOINT ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str

@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """Process a question. Uses AI if configured, otherwise rule engine."""
    if is_ai_enabled():
        return process_ai_question(req.question)
    return process_question(req.question)


# ─── CONFIG / SETTINGS ENDPOINTS ──────────────────────────────────

class ConfigUpdate(BaseModel):
    ai_provider: str = ""
    api_key: str = ""
    endpoint_url: str = ""
    model_name: str = ""
    api_format: str = "openai"
    ai_enabled: bool = False

@app.get("/api/config")
async def api_get_config():
    """Get current configuration (API key is masked)."""
    config = load_config()
    key = config.get("api_key", "")
    masked = ""
    if key:
        masked = key[:4] + "*" * (len(key) - 8) + key[-4:] if len(key) > 8 else "****"
    return {
        "ai_provider": config.get("ai_provider", ""),
        "endpoint_url": config.get("endpoint_url", ""),
        "model_name": config.get("model_name", ""),
        "api_format": config.get("api_format", "openai"),
        "api_key_masked": masked,
        "has_key": bool(key),
        "ai_enabled": config.get("ai_enabled", False),
    }

@app.get("/api/config/presets")
async def api_get_presets():
    """Get all available LLM provider presets."""
    from diagnostics.config import get_presets
    presets = get_presets()
    return {k: {"label": v["label"], "endpoint_url": v["endpoint_url"],
                "model_name": v["model_name"], "api_format": v["api_format"],
                "needs_key": v["needs_key"]} for k, v in presets.items()}

@app.post("/api/config")
async def api_save_config(req: ConfigUpdate):
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
async def api_test_config():
    """Test the current AI configuration with a simple query."""
    if not is_ai_enabled():
        return {"success": False, "error": "AI is not configured. Save your settings first."}

    result = process_ai_question("Say hello in one sentence to confirm you're working.")
    if result.get("intent") == "ai_response":
        return {"success": True, "message": "AI connection working!", "response": result["messages"][0][:200]}
    else:
        return {"success": False, "error": result.get("messages", ["Unknown error"])[0]}


# ─── DELETE / CLEAN ENDPOINTS ─────────────────────────────────────

PROTECTED_PATHS = [
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\ProgramData", "C:\\Users\\Default", "C:\\Recovery",
    "C:\\$Recycle.Bin", "C:\\System Volume Information",
]

class DeleteFileRequest(BaseModel):
    path: str

class DeleteFolderContentsRequest(BaseModel):
    path: str


@app.post("/api/action/delete-file")
async def api_delete_file(req: DeleteFileRequest):
    """Delete a single file. Protected system paths are blocked."""
    path = os.path.abspath(req.path)

    # Safety checks
    for protected in PROTECTED_PATHS:
        if path.lower().startswith(protected.lower()):
            return {"success": False, "error": f"Cannot delete files in protected path: {protected}"}

    if not os.path.isfile(path):
        return {"success": False, "error": "File not found."}

    try:
        size = os.path.getsize(path)
        os.remove(path)
        return {
            "success": True,
            "message": f"Deleted: {os.path.basename(path)}",
            "freed": storage._format_bytes(size),
            "freed_bytes": size,
        }
    except PermissionError:
        return {"success": False, "error": "Permission denied. File may be in use."}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/action/clean-folder")
async def api_clean_folder(req: DeleteFolderContentsRequest):
    """Delete the contents of a folder (temp/cache cleanup). The folder itself is kept."""
    path = os.path.abspath(req.path)

    # Safety checks
    for protected in PROTECTED_PATHS:
        if path.lower().startswith(protected.lower()):
            return {"success": False, "error": f"Cannot clean protected path: {protected}"}

    if not os.path.isdir(path):
        return {"success": False, "error": "Directory not found."}

    freed = 0
    errors = 0

    try:
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    size = os.path.getsize(item_path)
                    os.remove(item_path)
                    freed += size
                elif os.path.isdir(item_path):
                    size = storage._dir_size(item_path)
                    shutil.rmtree(item_path, ignore_errors=True)
                    freed += size
            except (PermissionError, OSError):
                errors += 1
                continue
    except (PermissionError, OSError) as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "message": f"Cleaned: {os.path.basename(path)}",
        "freed": storage._format_bytes(freed),
        "freed_bytes": freed,
        "errors": errors,
    }


# ─── UPDATE ENDPOINTS ─────────────────────────────────────────────

_update_cache = {}  # Stores download info between check and install

@app.get("/api/update/check")
async def api_check_update():
    """Check GitHub for a newer version."""
    result = updater.check_for_update()
    if result.get("available") and result.get("download_url"):
        _update_cache["download_url"] = result["download_url"]
        _update_cache["latest_version"] = result["latest_version"]
    return result


@app.post("/api/update/install")
async def api_install_update():
    """Download and install the latest update."""
    url = _update_cache.get("download_url")
    if not url:
        check = updater.check_for_update()
        if not check.get("available"):
            return {"success": False, "error": "No update available."}
        url = check.get("download_url")
        if not url:
            return {"success": False, "error": "No download URL found in the release."}

    # Download
    dl_result = updater.download_update(url)
    if not dl_result["success"]:
        return dl_result

    # Install (launches batch script and the app will exit)
    install_result = updater.install_update(dl_result["updater_path"])
    if install_result["success"]:
        # Schedule app shutdown after responding
        import asyncio
        async def shutdown_later():
            await asyncio.sleep(2)
            os._exit(0)
        asyncio.create_task(shutdown_later())

    return install_result


@app.get("/api/version")
async def api_version():
    """Return the current app version."""
    return {"version": __version__}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8888)
