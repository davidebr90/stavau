"""Local, append-only event log (JSONL). Never leaves the machine.

Privacy constraints (see docs/threat-model.md, T7): local file with per-user
permissions, size-capped rotation, no device identifiers beyond the
user-chosen alias, one-command purge.

Single-writer contract: exactly one process — the ``stavau run`` daemon — ever
appends; every other component (``status``, ``log``, the tray/GUI) only *reads*
via ``tail()``. Rotation is a check-then-act (size probe, then ``replace``), so
concurrent appenders are unsupported and could clobber a just-rotated file; the
reader already tolerates torn writes. Do not point two live daemons at one log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_LOG_BYTES = 1_000_000

# Per-value cap on serialized detail strings: an unbounded str(exc) (a giant
# stack repr, a garbled MQTT/BLE payload) could otherwise dominate the rotation
# budget and evict real history.
MAX_DETAIL_CHARS = 500


def _truncate_detail(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_DETAIL_CHARS:
        return value[:MAX_DETAIL_CHARS] + "…(truncated)"
    return value


@dataclass(frozen=True)
class EventRecord:
    timestamp: str
    event: str
    detail: dict[str, Any]


class EventLog:
    def __init__(self, path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
        self._path = path
        self._max_bytes = max_bytes

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: str, **detail: Any) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "detail": {key: _truncate_detail(value) for key, value in detail.items()},
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def tail(self, count: int = 20) -> list[EventRecord]:
        records: list[EventRecord] = []
        for path in (self._rotated_path(), self._path):
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        raw = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue  # tolerate a torn write; never crash on our own log
                    records.append(
                        EventRecord(
                            timestamp=str(raw.get("timestamp", "")),
                            event=str(raw.get("event", "")),
                            detail=dict(raw.get("detail", {})),
                        )
                    )
        return records[-count:]

    def clear(self) -> None:
        for path in (self._path, self._rotated_path()):
            path.unlink(missing_ok=True)

    def _rotated_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".1")

    def _rotate_if_needed(self) -> None:
        if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
            self._path.replace(self._rotated_path())
