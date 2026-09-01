"""Logging configuration module.

Provides setup_logging() for entry point scripts and get_logger() for all modules.
"""

from .logconfig import (
    HANDLER_NAME,
    LOG_BACKUP_COUNT,
    MAX_LOG_BYTES,
    get_logger,
    setup_logging,
)

__all__ = [
    "HANDLER_NAME",
    "LOG_BACKUP_COUNT",
    "MAX_LOG_BYTES",
    "get_logger",
    "setup_logging",
]
