"""Local, append-only event log (JSONL). Never leaves the machine.

Privacy constraints (see docs/threat-model.md, T7): local file with per-user
permissions, size-capped rotation, no device identifiers beyond the
user-chosen alias, one-command purge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_LOG_BYTES = 1_000_000


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
            "detail": detail,
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
