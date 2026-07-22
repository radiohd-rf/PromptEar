#!/usr/bin/env python3
"""PromptEar — точка входа (Web + PyWebView)."""

import os
import socketserver
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web.server import app


def _find_free_port() -> int:
    with socketserver.TCPServer(("127.0.0.1", 0), None) as s:
        return s.server_address[1]


def main() -> None:
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, threaded=True),
        daemon=True,
    ).start()

    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    import webview

    window = webview.create_window(
        "PromptEar",
        url,
        width=860,
        height=720,
        resizable=True,
    )

    webview.start()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_log = Path(__file__).resolve().parent / "crash.log"
        with open(str(crash_log), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)
