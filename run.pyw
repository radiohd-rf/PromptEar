#!/usr/bin/env pythonw
"""Запуск PromptEar без окна терминала."""

import os
import sys

# pythonw.exe устанавливает stdout/stderr в None — чиним
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from main import main

main()
