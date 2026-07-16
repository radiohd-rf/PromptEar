"""Переиспользуемые виджеты с плейсхолдерами."""

import tkinter as tk
from pathlib import Path


class PlaceholderEntry(tk.Entry):
    """Entry с плейсхолдером."""

    def __init__(
        self, master, placeholder: str = "", fg: str = "#aaa", normal_fg: str = "#222", **kwargs
    ):
        self._placeholder = placeholder
        self._placeholder_fg = fg
        self._normal_fg = normal_fg
        kwargs["fg"] = fg
        super().__init__(master, **kwargs)
        if placeholder:
            self.insert(0, placeholder)
            self.bind("<FocusIn>", self._on_focus_in)
            self.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_in(self, event):
        if self.get() == self._placeholder:
            self.delete(0, tk.END)
            self.config(fg=self._normal_fg)

    def _on_focus_out(self, event):
        if not self.get().strip():
            self.delete(0, tk.END)
            self.insert(0, self._placeholder)
            self.config(fg=self._placeholder_fg)

    def get_value(self) -> str | None:
        """Возвращает значение или None, если плейсхолдер активен."""
        val = self.get()
        if val == self._placeholder:
            return None
        return val.strip() or None

    def set_text(self, text: str):
        """Устанавливает текст с правильным цветом."""
        self.delete(0, tk.END)
        self.insert(0, text)
        self.config(fg=self._normal_fg)


class PlaceholderListbox(tk.Listbox):
    """Listbox с плейсхолдером при пустом списке."""

    def __init__(
        self, master, placeholder: str = "", fg: str = "#aaa", normal_fg: str = "#222", **kwargs
    ):
        self._placeholder = placeholder
        self._placeholder_fg = fg
        self._normal_fg = normal_fg
        kwargs["fg"] = fg
        super().__init__(master, **kwargs)
        self._show_placeholder()

    def _show_placeholder(self):
        if self.size() == 0 and self._placeholder:
            self.insert(0, self._placeholder)
            self.itemconfig(0, fg=self._placeholder_fg)

    def _hide_placeholder(self):
        if self.size() > 0 and self.get(0) == self._placeholder:
            self.delete(0)

    def get_files(self) -> list[Path]:
        """Возвращает Path-ы, исключая плейсхолдер."""
        self._hide_placeholder()
        files = []
        for i in range(self.size()):
            files.append(Path(self.get(i)))
        self._show_placeholder()
        return files

    def add_file(self, path: str):
        """Добавляет файл, убирая плейсхолдер."""
        self._hide_placeholder()
        self.insert(tk.END, path)
        self.config(fg=self._normal_fg)

    def clear(self):
        """Очищает список и показывает плейсхолдер."""
        self.delete(0, tk.END)
        self._show_placeholder()

    def has_placeholder(self) -> bool:
        """Проверяет, отображается ли плейсхолдер."""
        return self.size() > 0 and self.get(0) == self._placeholder
