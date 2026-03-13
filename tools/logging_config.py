# server/logging_config.py
import logging
from logging.config import dictConfig
from server.color_formatter import ColorFormatter

class CustomStreamHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__()
        self.setFormatter(ColorFormatter())

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
       "console": {
            "()": CustomStreamHandler,
            "level": "INFO",
        }
    },
    "loggers": {
        "": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "watchfiles.main": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    }
}


def setup_logging():
    dictConfig(LOGGING_CONFIG)
