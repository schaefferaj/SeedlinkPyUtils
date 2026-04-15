"""Rotating file + console logging setup for SeedlinkPyUtils."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logger(name: str = "seedlink_py_utils",
                 log_file: Optional[str] = None,
                 max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 5,
                 level: int = logging.INFO) -> logging.Logger:
    """Configure a logger with console output and optional rotating file handler.

    Parameters
    ----------
    name : str
        Logger name.
    log_file : str, optional
        Path to a log file. If None, only console logging is configured.
    max_bytes : int
        Rotate when the log file reaches this size (default: 10 MB).
    backup_count : int
        Number of rotated copies to keep (default: 5).
    level : int
        Logging level (default: INFO).
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()  # idempotent if called twice

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        fh = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    logger.propagate = False
    return logger
