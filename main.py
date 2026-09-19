#!/usr/bin/env python3
"""PromptEar — точка входа (Web + PyWebView)."""

import ctypes
import os
import socketserver
import sys
import threading
import time
import traceback
import urllib.request
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web.server import app

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
GCLP_HICON = -14
GCLP_HICONSM = -34
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1


def _set_app_user_model_id() -> None:
    """Отдельный AppUserModelID — иначе таскбар группирует окно под иконку pythonw."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PromptEar.desktop"
        )
    except Exception:
        pass


def _apply_taskbar_icon(window_title: str, icon_path: Path) -> None:
    """Ставит иконку класса окна — именно её показывает таскбар."""
    user32 = ctypes.windll.user32

    hicon = user32.LoadImageW(
        None, str(icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE
    )
    if not hicon:
        return

    try:
        set_class_long = user32.SetClassLongPtrW
    except AttributeError:
        set_class_long = user32.SetClassLongW

    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if window_title.lower() in buf.value.lower():
            found.append(hwnd)
            return False
        return True

    for _ in range(40):
        found.clear()
        user32.EnumWindows(enum_cb, 0)
        if found:
            hwnd = found[0]
            set_class_long(hwnd, GCLP_HICON, hicon)
            set_class_long(hwnd, GCLP_HICONSM, hicon)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            return
        time.sleep(0.25)


def _find_free_port() -> int:
    with socketserver.TCPServer(
        ("127.0.0.1", 0), socketserver.BaseRequestHandler
    ) as s:
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

    icon_path = Path(__file__).resolve().parent / "assets" / "icon.ico"
    icon = str(icon_path) if icon_path.is_file() else None

    _set_app_user_model_id()

    webview.create_window(
        "PromptEar",
        url,
        width=1150,
        height=760,
        resizable=True,
    )

    def _after_start() -> None:
        if icon:
            thread = threading.Thread(
                target=_apply_taskbar_icon, args=("PromptEar", icon_path), daemon=True
            )
            thread.start()

    webview.start(icon=icon, func=_after_start)
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
