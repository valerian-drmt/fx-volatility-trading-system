import logging
import colorlog


def colored_logger(name: str = None, level=logging.INFO):
    logger = colorlog.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if already configured
    if not logger.hasHandlers():
        handler = colorlog.StreamHandler()
        handler.setFormatter(colorlog.ColoredFormatter(
            fmt="%(log_color)s[%(asctime)s] %(levelname)s - %(filename)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                'DEBUG': 'white',
                'INFO': 'light_green',
                'WARNING': 'yellow',
                'ERROR': 'light_red',
                'CRITICAL': 'bold_red',
            },
            style='%'
        ))
        logger.addHandler(handler)

    return logger
