import logging

import colorlog
import pytest

from src.core.config.logger_config import colored_logger

def test_colored_logger_builds_colorlog_logger(caplog):
    logger = colored_logger(level=logging.DEBUG)

    assert logger is colorlog.getLogger()
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1

    handler = logger.handlers[0]
    assert isinstance(handler, colorlog.StreamHandler)
    assert isinstance(handler.formatter, colorlog.ColoredFormatter)

    formatter = handler.formatter
    assert formatter._fmt == "%(log_color)s[%(asctime)s] %(levelname)s - %(filename)s - %(message)s"
    assert formatter.datefmt == "%Y-%m-%d %H:%M:%S"
    assert formatter.log_colors == {
        'DEBUG': 'white',
        'INFO': 'light_green',
        'WARNING': 'yellow',
        'ERROR': 'light_red',
        'CRITICAL': 'bold_red',
    }
    assert formatter.secondary_log_colors == {}

    with caplog.at_level(logging.DEBUG):
        logger.debug("probe")

    rendered = caplog.records[0].message
    assert rendered == "probe"
    record = caplog.records[0]
    assert record.levelno == logging.DEBUG
    assert record.message == "probe"
