"""Backward-compatible re-exports; prefer ``app.core.config`` in new code."""

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
