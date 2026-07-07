"""Settings: one JSON file, schema-versioned, atomic writes, no secrets.

Pairing keys live in the OS Bluetooth stack, never here. The file stores the
device identity address + alias, thresholds and calibration constants only.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "stavau"
SCHEMA_VERSION = 1


class ConfigError(RuntimeError):
    pass


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "config.json"


def event_log_path() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False)) / "events.jsonl"


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    device_address: str = ""
    device_alias: str = ""
    # Device intelligence / association (v0.2). Recorded at setup.
    device_kind: str = "unknown"  # see core.deviceid.DeviceKind
    strategy: str = "adv_scan"  # effective proximity strategy, see core.deviceid.Strategy
    association: str = "pairing-less"  # "pairing-less" (advertisement) or "paired" (bonded)
    # UI language: "auto" resolves to the OS language when a catalog exists,
    # falling back to English (see stavau.i18n.resolve_language).
    language: str = "auto"
    # Auto-unlock (advanced, off by default; see core.autounlock + threat-model T9).
    auto_unlock: bool = False
    auto_unlock_ack: bool = False  # explicit risk acknowledgement, required to enable
    auto_unlock_strict_ratio: float = 0.5  # must be within radius*ratio (stricter than lock)
    auto_unlock_dwell_seconds: float = 5.0  # continuously, before unlocking (anti-relay)
    # Smart-home integration (optional, off by default; local-network MQTT to
    # Home Assistant etc.). Empty host/topic => inert, no network I/O (I3). The
    # MQTT password is never stored here — read from $STAVAU_MQTT_PASSWORD.
    integration_mqtt_host: str = ""
    integration_mqtt_port: int = 1883
    integration_mqtt_username: str = ""
    integration_presence_topic: str = ""  # consume: external presence in
    integration_present_values: str = "on,home,present,occupied,true,1"
    integration_action_topic: str = ""  # emit: lock/unlock events out
    radius_m: float = 3.0
    grace_seconds: float = 10.0
    return_seconds: float = 3.0
    smoothing_window: int = 8
    rssi_at_1m: float = -59.0
    path_loss_exponent: float = 2.0
    # Anti-runaway guardrail: pause locking after too many locks too fast.
    breaker_max_locks: int = 3
    breaker_window_seconds: float = 120.0
    breaker_cooldown_seconds: float = 300.0

    def validate(self) -> None:
        if not self.device_address:
            raise ConfigError("no trusted device configured - run 'stavau setup' first")
        if not 1.0 <= self.radius_m <= 10.0:
            raise ConfigError("radius_m must be between 1 and 10 metres")
        if self.grace_seconds < 3.0:
            raise ConfigError("grace_seconds must be at least 3 (anti false-positive floor)")
        if self.smoothing_window < 1:
            raise ConfigError("smoothing_window must be at least 1")
        if self.breaker_max_locks < 1:
            raise ConfigError("breaker_max_locks must be at least 1")
        if self.breaker_window_seconds <= 0 or self.breaker_cooldown_seconds <= 0:
            raise ConfigError("breaker window/cooldown seconds must be positive")
        if self.auto_unlock:
            # Auto-unlock is an explicit, acknowledged, bonded-only opt-in.
            if not self.auto_unlock_ack:
                raise ConfigError(
                    "auto_unlock requires an explicit risk acknowledgement "
                    "(re-run setup with --enable-auto-unlock --i-understand-the-risk)"
                )
            if self.association != "paired":
                raise ConfigError(
                    "auto_unlock requires a paired (bonded) device — run 'stavau pair' first"
                )
            if not 0.0 < self.auto_unlock_strict_ratio <= 1.0:
                raise ConfigError("auto_unlock_strict_ratio must be in (0, 1]")
            if self.auto_unlock_dwell_seconds < 0:
                raise ConfigError("auto_unlock_dwell_seconds must be non-negative")
        # Integration MQTT is optional; only sanity-check the port if a host is set.
        if self.integration_mqtt_host and not 1 <= self.integration_mqtt_port <= 65535:
            raise ConfigError("integration_mqtt_port must be between 1 and 65535")

    def save(self, path: Path | None = None) -> Path:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dataclasses.asdict(self), indent=2)
        # Atomic write: a crash mid-save must never leave a torn config.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, target)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        source = path or config_path()
        if not source.exists():
            raise ConfigError(f"no configuration at {source} - run 'stavau setup' first")
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(
                f"configuration at {source} is unreadable ({exc}) - re-run 'stavau setup'"
            ) from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"configuration at {source} is malformed - re-run 'stavau setup'")
        known = {f.name for f in dataclasses.fields(cls)}
        data = {key: value for key, value in raw.items() if key in known}
        try:
            return cls(**data)
        except TypeError as exc:
            raise ConfigError(
                f"configuration at {source} has invalid values ({exc}) - re-run 'stavau setup'"
            ) from exc
