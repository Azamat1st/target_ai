"""Настройки приложения из .env (спека §12)."""

from .settings import Settings, load_settings

__all__ = ["Settings", "load_settings"]
