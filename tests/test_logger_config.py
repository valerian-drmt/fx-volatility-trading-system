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

    with caplog.at_level(logging.DEBUG):
        logger.debug("probe")

    rendered = caplog.records[0].message
    assert rendered == "probe"
