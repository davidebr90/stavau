"""Local configuration persistence (JSON, schema-versioned, atomic writes)."""

from stavau.config.settings import ConfigError, Settings, config_path, event_log_path

__all__ = ["ConfigError", "Settings", "config_path", "event_log_path"]
