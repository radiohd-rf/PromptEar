"""PromptEar — точка входа (минимальный реэкспорт для тестов и совместимости)."""

import importlib.util
import tkinter as tk

from ui.root import PromptEarApp

__all__ = ["PromptEarApp"]


def main() -> None:
    """Запускает приложение."""
    HAS_DND = importlib.util.find_spec("tkinterdnd2") is not None
    if HAS_DND:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    root.withdraw()
    app = PromptEarApp(root)
    root.deiconify()
    app.run()


if __name__ == "__main__":
    main()
