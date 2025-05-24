import logging
import colorlog

def colored_logger(level=logging.INFO):
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        fmt="%(log_color)s[%(asctime)s] %(levelname)-8s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            'DEBUG':    'white',
            'INFO':     'light_green',
            'WARNING':  'yellow',
            'ERROR':    'light_red',
            'CRITICAL': 'bold_red',
        },
        reset=True,
        secondary_log_colors={},
        style='%'
    ))

    logger = colorlog.getLogger()
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger