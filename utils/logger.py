"""Логирование в файл с ротацией."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PromptEar" / "logs"
LOG_FILE = LOG_DIR / "app.log"
MAX_BYTES = 1_048_576  # 1 MB
BACKUP_COUNT = 3

_logger: logging.Logger | None = None


def setup_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    _logger = logging.getLogger("PromptEar")
    _logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    _logger.info("Logger initialized")
    return _logger


def get_logger() -> logging.Logger:
    if _logger is None:
        return setup_logger()
    return _logger
