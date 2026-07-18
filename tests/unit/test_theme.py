"""Тесты менеджера тем."""

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from ui.theme import ThemeManager


@pytest.fixture
def manager():
    root = MagicMock(spec=tk.Tk)
    return ThemeManager(root)


def test_toggle_light_to_dark(manager):
    assert manager.theme == "light"
    assert manager.colors["bg"] == "#fff"
    manager.toggle()
    assert manager.theme == "dark"
    assert manager.colors["bg"] == "#1e1e1e"


def test_toggle_dark_to_light(manager):
    manager.toggle()
    assert manager.theme == "dark"
    manager.toggle()
    assert manager.theme == "light"
    assert manager.colors["bg"] == "#fff"


def test_colors_property_returns_current(manager):
    assert manager.colors is manager._colors


def test_is_dark_property(manager):
    assert manager.is_dark is False
    manager.toggle()
    assert manager.is_dark is True
