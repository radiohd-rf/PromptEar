"""Кастомный заголовок окна, перетаскивание, иконка в таскбаре."""

import ctypes
import os
import subprocess
import sys
import tempfile
import tkinter as tk

from PIL import Image, ImageTk

from config import COLORS, FONT_FAMILY, FONT_SIZE_SMALL


class TitleBar:
    """Кастомная строка заголовка для overrideredirect-окна."""

    def __init__(self, root: tk.Tk, app, theme_provider) -> None:
        self.root = root
        self._app = app
        self._theme = theme_provider
        self._drag_x = 0
        self._drag_y = 0

        base = os.path.dirname(__file__)
        self._icon_dark_path = os.path.join(base, "..", "di.ico")
        self._icon_light_path = os.path.join(base, "..", "li.ico")

        self._frame: tk.Frame | None = None
        self._lbl_icon: tk.Label | None = None
        self._lbl_title: tk.Label | None = None
        self._btn_close: tk.Label | None = None
        self._btn_theme: tk.Label | None = None

        self._load_icons()

    def _load_icons(self) -> None:
        try:
            self._icon_dark = ImageTk.PhotoImage(
                Image.open(self._icon_dark_path).resize((20, 20), Image.LANCZOS)
            )
            self._icon_light = ImageTk.PhotoImage(
                Image.open(self._icon_light_path).resize((20, 20), Image.LANCZOS)
            )
        except Exception:
            self._icon_dark = self._icon_light = None

    # ── Сборка заголовка ────────────────────────────────────────────────

    def build(self, main_frame: tk.Frame) -> None:
        """Создаёт виджеты заголовка."""
        self.root.overrideredirect(True)
        self.root.update_idletasks()
        self._add_window_shadow()

        self._frame = tk.Frame(self.root, bg=self._theme.bg, height=28, cursor="arrow")
        self._frame.pack(fill=tk.X, side=tk.TOP, before=main_frame)
        self._frame.pack_propagate(False)

        current_icon = self._icon_dark
        self._lbl_icon = tk.Label(
            self._frame,
            image=current_icon,
            bg=self._theme.bg,
            cursor="arrow",
        )
        self._lbl_icon.pack(side=tk.LEFT, padx=(4, 2))

        self._lbl_title = tk.Label(
            self._frame,
            text="PromptEar — транскрибация аудио",
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
            fg=self._theme.fg,
            bg=self._theme.bg,
        )
        self._lbl_title.pack(side=tk.LEFT, padx=2)

        self._btn_close = tk.Label(
            self._frame,
            text="✕",
            font=(FONT_FAMILY, 12),
            fg=self._theme.fg,
            bg=self._theme.bg,
            cursor="hand2",
            padx=8,
        )
        self._btn_close.pack(side=tk.RIGHT)
        self._btn_close.bind("<Button-1>", lambda e: self.root.destroy())

        self._btn_theme = tk.Label(
            self._frame,
            text="☀️",
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
            fg=self._theme.status,
            bg=self._theme.bg,
            cursor="hand2",
        )
        self._btn_theme.pack(side=tk.RIGHT, padx=(0, 4))
        self._btn_theme.bind("<Button-1>", lambda e: self._app._toggle_theme())

        self._frame.bind("<Button-1>", self._start_drag)
        self._frame.bind("<B1-Motion>", self._do_drag)
        self._lbl_title.bind("<Button-1>", self._start_drag)
        self._lbl_title.bind("<B1-Motion>", self._do_drag)
        self._lbl_icon.bind("<Button-1>", self._start_drag)
        self._lbl_icon.bind("<B1-Motion>", self._do_drag)

        self.root.after(200, self._add_to_taskbar)

    # ── Перетаскивание ──────────────────────────────────────────────────

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event: tk.Event) -> None:
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ── Тень окна ───────────────────────────────────────────────────────

    def _add_window_shadow(self) -> None:
        try:
            hwnd = self.root.winfo_id()
            margins = ctypes.c_int(-1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            GCL_STYLE = -26
            CS_DROPSHADOW = 0x00020000
            current = ctypes.windll.user32.GetClassLongPtrW(hwnd, GCL_STYLE)
            ctypes.windll.user32.SetClassLongPtrW(hwnd, GCL_STYLE, current | CS_DROPSHADOW)
        except Exception:
            pass

    # ── Панель задач ────────────────────────────────────────────────────

    def _set_window_icon(self, hwnd: int) -> None:
        phicon_large = ctypes.c_void_p(0)
        phicon_small = ctypes.c_void_p(0)
        ctypes.windll.shell32.ExtractIconExW(
            self._icon_light_path, 0,
            ctypes.byref(phicon_large), ctypes.byref(phicon_small), 1,
        )
        u = ctypes.windll.user32
        if phicon_small.value:
            u.SetClassLongPtrW(hwnd, -34, phicon_small.value)
        if phicon_large.value:
            u.SetClassLongPtrW(hwnd, -14, phicon_large.value)

    def _add_to_taskbar(self) -> None:
        hwnd = self.root.winfo_id()
        self._set_window_icon(hwnd)

        try:
            from ctypes import HRESULT, c_void_p
            from comtypes import GUID, IUnknown, COMMETHOD, CoCreateInstance, CoInitialize
            from comtypes import CLSCTX_INPROC_SERVER

            class _ITaskbarList(IUnknown):
                _iid_ = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "HrInit"),
                    COMMETHOD([], HRESULT, "AddTab", (["in"], c_void_p, "hwnd")),
                ]

            CoInitialize()
            clsid = GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}")
            taskbar = CoCreateInstance(clsid, _ITaskbarList, CLSCTX_INPROC_SERVER)
            taskbar.HrInit()
            taskbar.AddTab(hwnd)
        except Exception:
            pass

        try:
            icon_path = self._icon_light_path
            my_pid = os.getpid()
            helper = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
            helper.write(f"""import ctypes, os
I = {icon_path!r}
P = {my_pid}
p=ctypes.c_void_p(0);q=ctypes.c_void_p(0)
ctypes.windll.shell32.ExtractIconExW(I,0,ctypes.byref(p),ctypes.byref(q),1)
hs,hl=p.value,q.value
if hs:
 buf=ctypes.create_unicode_buffer(256);pp=ctypes.c_uint(0);tkhwnd=0
 def cb(h,_):
  global tkhwnd
  if not tkhwnd:
   ctypes.windll.user32.GetWindowThreadProcessId(h,ctypes.byref(pp))
   if pp.value==P:
    ctypes.windll.user32.GetClassNameW(h,buf,256)
    if buf.value=='TkTopLevel':tkhwnd=h
  return True
 W=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
 ctypes.windll.user32.EnumWindows(W(cb),0)
 if tkhwnd:
  u=ctypes.windll.user32
  u.SendMessageW(tkhwnd,0x0080,0,hs)
  if hl:u.SendMessageW(tkhwnd,0x0080,1,hl)
""")
            helper.close()
            subprocess.Popen(
                [sys.executable, helper.name],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.root.after(5000, lambda: os.unlink(helper.name))
        except Exception:
            pass

    # ── Обновление цвета (вызывается при смене темы) ────────────────────

    def update_theme(self) -> None:
        if self._frame:
            self._frame.configure(bg=self._theme.bg)
        if self._lbl_title:
            self._lbl_title.configure(bg=self._theme.bg, fg=self._theme.fg)
        if self._lbl_icon:
            icon_img = self._icon_dark if self._theme.is_dark else self._icon_light
            self._lbl_icon.configure(image=icon_img, bg=self._theme.bg)
        if self._btn_close:
            self._btn_close.configure(bg=self._theme.bg, fg=self._theme.fg)
        if self._btn_theme:
            self._btn_theme.configure(
                text="🌙" if self._theme.is_dark else "☀️",
                bg=self._theme.bg,
                fg=self._theme.status,
            )
