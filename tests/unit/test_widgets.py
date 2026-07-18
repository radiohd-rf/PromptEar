"""Тесты переиспользуемых виджетов."""

import tkinter as tk
from pathlib import Path

import pytest

from ui.widgets import PlaceholderEntry, PlaceholderListbox


@pytest.fixture
def root():
    """Создаёт настоящее Tk-окно (тестируем реальные виджеты)."""
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_placeholder_shown_initially(root):
    entry = PlaceholderEntry(root, placeholder="введите текст")
    assert entry._placeholder == "введите текст"
    assert entry.get() == "введите текст"
    assert entry.cget("fg") == "#aaa"


def test_focus_in_clears_placeholder(root):
    entry = PlaceholderEntry(root, placeholder="введите текст")
    entry._on_focus_in(None)
    assert entry.get() == ""  # placeholder очищен при фокусе
    entry.insert(0, "пользовательский ввод")
    entry.config(fg="#222")
    assert entry.get() == "пользовательский ввод"
    assert entry.cget("fg") == "#222"


def test_get_value_returns_none_for_placeholder(root):
    entry = PlaceholderEntry(root, placeholder="введите текст")
    assert entry.get_value() is None


def test_set_text_updates(root):
    entry = PlaceholderEntry(root, placeholder="введите текст")
    entry.set_text("новый текст")
    assert entry.get() == "новый текст"
    assert entry.cget("fg") == "#222"


def test_placeholder_shown_empty(root):
    lb = PlaceholderListbox(root, placeholder="файлы не выбраны")
    assert lb.size() == 1
    assert lb.get(0) == "файлы не выбраны"


def test_add_file_hides_placeholder(root):
    lb = PlaceholderListbox(root, placeholder="файлы не выбраны")
    lb.add_file("test.wav")
    assert lb.size() == 1
    assert lb.get(0) == "test.wav"


def test_get_files_excludes_placeholder(root):
    lb = PlaceholderListbox(root, placeholder="файлы не выбраны")
    lb.add_file("a.wav")
    lb.add_file("b.wav")
    files = lb.get_files()
    assert files == [Path("a.wav"), Path("b.wav")]


def test_clear_restores_placeholder(root):
    lb = PlaceholderListbox(root, placeholder="файлы не выбраны")
    lb.add_file("test.wav")
    lb.clear()
    assert lb.size() == 1
    assert lb.get(0) == "файлы не выбраны"
    assert lb.has_placeholder() is True
