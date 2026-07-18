"""Менеджер тем: светлая / тёмная."""

import tkinter as tk
import tkinter.ttk as ttk

from config import COLORS, DARK_COLORS, LIGHT_COLORS, FONT_FAMILY, FONT_SIZE, FONT_SIZE_SMALL


class ThemeManager:
    """Управляет переключением темы и перекраской виджетов."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._theme: str = "light"
        self._colors: dict[str, str] = dict(COLORS)

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def colors(self) -> dict[str, str]:
        return self._colors

    @property
    def bg(self) -> str:
        return self._colors["bg"]

    @property
    def fg(self) -> str:
        return self._colors["fg"]

    @property
    def border(self) -> str:
        return self._colors["border"]

    @property
    def ph(self) -> str:
        return self._colors["ph"]

    @property
    def status(self) -> str:
        return self._colors["status"]

    @property
    def btn_active_bg(self) -> str:
        return self._colors["btn_active_bg"]

    @property
    def select_bg(self) -> str:
        return self._colors["select_bg"]

    @property
    def select_fg(self) -> str:
        return self._colors["select_fg"]

    @property
    def is_dark(self) -> bool:
        return self._theme == "dark"

    def toggle(self) -> None:
        """Переключает тему, обновляет _colors."""
        if self._theme == "light":
            self._theme = "dark"
            self._colors.update(DARK_COLORS)
        else:
            self._theme = "light"
            self._colors.update(LIGHT_COLORS)

    def apply_to_all(self, root: tk.Widget | None = None) -> None:
        """Рекурсивно перекрашивает все виджеты."""
        target = root or self.root
        self._recolor(target)

        target.configure(bg=self.bg)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Vertical.TScrollbar",
            background=self.bg,
            troughcolor=self.bg,
            bordercolor=self.border,
            arrowcolor=self.fg,
        )
        style.configure(
            "Horizontal.TScrollbar",
            background=self.bg,
            troughcolor=self.bg,
            bordercolor=self.border,
            arrowcolor=self.fg,
        )

    def _recolor(self, w: tk.Widget) -> None:
        """Перекрашивает один виджет и его детей."""
        kw: dict[str, str] = {}
        if isinstance(w, (tk.Frame, tk.LabelFrame)):
            kw["bg"] = self.bg
        elif isinstance(w, (tk.Label, tk.Message)):
            kw["bg"] = self.bg
            kw["fg"] = self.fg
        elif isinstance(w, tk.Button):
            kw["bg"] = self.bg
            kw["fg"] = self.fg
            kw["activebackground"] = self.btn_active_bg
            kw["activeforeground"] = self.fg
        elif isinstance(w, tk.Text):
            kw["bg"] = self.bg
            kw["fg"] = self.fg
            kw["insertbackground"] = self.fg
            kw["selectbackground"] = self.select_bg
            kw["selectforeground"] = self.select_fg
        elif isinstance(w, tk.Listbox):
            kw["bg"] = self.bg
            kw["fg"] = self.fg
            kw["selectbackground"] = self.select_bg
            kw["selectforeground"] = self.select_fg
        elif isinstance(w, (tk.Radiobutton, tk.Checkbutton)):
            kw["bg"] = self.bg
            kw["fg"] = self.fg
            kw["selectcolor"] = self.bg
            kw["activebackground"] = self.bg
            kw["activeforeground"] = self.fg
        elif isinstance(w, tk.Entry):
            kw["bg"] = self.bg
            kw["fg"] = self.fg
            kw["insertbackground"] = self.fg
        if kw:
            try:
                w.configure(**kw)
            except tk.TclError:
                pass
        for c in w.winfo_children():
            self._recolor(c)
