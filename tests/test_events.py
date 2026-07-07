from pathlib import Path

from stavau.core.events import MAX_DETAIL_CHARS, EventLog


class TestEventLog:
    def test_oversized_detail_string_is_truncated(self, tmp_path: Path) -> None:
        # Finding 14: an unbounded str(exc) must not dominate the log budget.
        log = EventLog(tmp_path / "events.jsonl")
        log.append("lock_failed", error="A" * 100_000, code=7)
        record = log.tail(1)[0]
        assert len(record.detail["error"]) <= MAX_DETAIL_CHARS + len("…(truncated)")
        assert record.detail["error"].endswith("…(truncated)")
        assert record.detail["code"] == 7  # non-string values untouched

    def test_short_detail_string_is_not_truncated(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "events.jsonl")
        log.append("x", error="brief")
        assert log.tail(1)[0].detail["error"] == "brief"

    def test_append_and_tail_roundtrip(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "events.jsonl")
        log.append("monitor_started", device="phone", dry_run=True)
        log.append("lock_triggered", dry_run=True)
        records = log.tail(10)
        assert [r.event for r in records] == ["monitor_started", "lock_triggered"]
        assert records[0].detail == {"device": "phone", "dry_run": True}
        assert records[0].timestamp  # ISO timestamp present

    def test_tail_respects_count(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "events.jsonl")
        for i in range(10):
            log.append("tick", n=i)
        records = log.tail(3)
        assert [r.detail["n"] for r in records] == [7, 8, 9]

    def test_corrupt_line_is_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        log.append("ok_before")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{torn write!!!\n")
        log.append("ok_after")
        assert [r.event for r in log.tail(10)] == ["ok_before", "ok_after"]

    def test_rotation_at_max_bytes_caps_size_keeping_recent_events(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        log = EventLog(path, max_bytes=200)
        for i in range(20):
            log.append("tick", n=i)
        assert path.with_suffix(".jsonl.1").exists()
        assert path.stat().st_size < 200
        # Older batches are dropped by design (size cap); what remains is the
        # most recent contiguous window, ending with the last event.
        kept = [r.detail["n"] for r in log.tail(100)]
        assert kept[-1] == 19
        assert kept == list(range(kept[0], 20))

    def test_clear_removes_current_and_rotated(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        log = EventLog(path, max_bytes=200)
        for i in range(20):
            log.append("tick", n=i)
        log.clear()
        assert not path.exists()
        assert not path.with_suffix(".jsonl.1").exists()
        assert log.tail(10) == []
