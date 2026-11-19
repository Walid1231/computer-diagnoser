"""
Computer Diagnoser — Native Desktop Application
Launches the diagnostic dashboard as a standalone desktop window.
No browser needed.

DEV MODE: Edit any file and changes apply:
  - Python files (.py): Restart the app
  - Frontend files (HTML/CSS/JS): Just close & reopen the app
"""

import socket
import threading
import time
import urllib.request

import uvicorn
import webview

from main import app as fastapi_app


def _bind_free_port() -> socket.socket:
    """Reserve a free local port and keep it bound until the server takes it over.

    The old launcher hard-coded port 8888: if anything else used it (or a second copy of
    the app was open), the new window silently showed the *other* server's pages.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def _wait_until_ready(port: int, timeout: float = 20.0) -> bool:
    """Poll the health endpoint instead of sleeping a fixed 1.5 s and hoping."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.1)
    return False


if __name__ == "__main__":
    sock = _bind_free_port()
    port = sock.getsockname()[1]

    # log_config=None: the packaged app is built with --noconsole, where sys.stdout is None
    # and uvicorn's default colourised log formatter crashes on it at startup.
    config = uvicorn.Config(fastapi_app, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    server_thread.start()

    if not _wait_until_ready(port):
        raise SystemExit("Computer Diagnoser could not start its local server.")

    webview.create_window(
        title="Computer Diagnoser",
        url=f"http://127.0.0.1:{port}",
        width=1280,
        height=800,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
    )

    # Blocks until the window is closed.
    webview.start()
    server.should_exit = True
