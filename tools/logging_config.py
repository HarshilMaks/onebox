"""Compatibility import for tool modules; logging is configured by process entrypoints."""

from server.logging_config import setup_logging

__all__ = ["setup_logging"]
