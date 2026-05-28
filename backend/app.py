"""
Computer Diagnoser — Native Desktop Application
Launches the diagnostic dashboard as a standalone desktop window.
No browser needed.

DEV MODE: Edit any file and changes apply:
  - Python files (.py): Server auto-restarts
  - Frontend files (HTML/CSS/JS): Just close & reopen the app
"""

import threading
import time
import sys
import webview
import uvicorn
from main import app as fastapi_app


def start_server():
    """Run the FastAPI server in a background thread with auto-reload disabled
    (reload doesn't work in threads, but restarts are fast with the bat file)."""
    uvicorn.run(fastapi_app, host="127.0.0.1", port=8888, log_level="warning")


if __name__ == "__main__":
    # Start the API server in background
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Give the server a moment to boot
    time.sleep(1.5)

    # Create native desktop window
    window = webview.create_window(
        title="Computer Diagnoser",
        url="http://127.0.0.1:8888",
        width=1280,
        height=800,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
    )

    # Start the native window (blocks until closed)
    webview.start()
