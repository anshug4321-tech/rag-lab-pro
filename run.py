"""Start RAG Lab Pro with one command: python run.py"""
import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8000


def _open_browser() -> None:
    """Open the app in the default browser."""
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Timer(1.5, _open_browser).start()
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=False)
