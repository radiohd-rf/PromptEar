"""Тесты логгера."""

import logging

import pytest

from utils.logger import LOG_FILE, get_logger, setup_logger


def _cleanup():
    import utils.logger as log_mod
    log_mod._logger = None


@pytest.fixture(autouse=True)
def reset_logger():
    _cleanup()
    yield
    _cleanup()


def test_setup_logger_creates_file():
    logger = setup_logger()
    assert logger.name == "PromptEar"
    assert logger.level == logging.DEBUG
    assert LOG_FILE.exists()


def test_setup_logger_returns_logger():
    logger = setup_logger()
    assert isinstance(logger, logging.Logger)


def test_get_logger_same_instance():
    logger1 = setup_logger()
    logger2 = get_logger()
    assert logger1 is logger2
