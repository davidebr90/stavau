import threading

import pytest

from stavau.core.monitor import RssiTracker


class TestRssiTracker:
    def test_smoothed_value_when_fresh(self) -> None:
        tracker = RssiTracker(smoothing_window=4, stale_seconds=15.0)
        for t in range(6):
            tracker.push(-60.0, now=float(t))
        assert tracker.smoothed(now=6.0) == pytest.approx(-60.0)

    def test_no_samples_means_none(self) -> None:
        tracker = RssiTracker(smoothing_window=4)
        assert tracker.smoothed(now=0.0) is None

    def test_stale_samples_mean_none_fail_safe(self) -> None:
        tracker = RssiTracker(smoothing_window=4, stale_seconds=15.0)
        tracker.push(-60.0, now=0.0)
        assert tracker.smoothed(now=10.0) is not None
        assert tracker.smoothed(now=15.1) is None

    def test_long_gap_resets_smoothing_history(self) -> None:
        tracker = RssiTracker(smoothing_window=4, stale_seconds=15.0)
        for t in range(6):
            tracker.push(-55.0, now=float(t))
        # Device disappears for a minute, then reappears much farther away:
        # the old -55 dBm history must not dilute the new reading.
        tracker.push(-85.0, now=70.0)
        assert tracker.smoothed(now=70.0) == pytest.approx(-85.0)

    def test_last_seen_tracks_pushes(self) -> None:
        tracker = RssiTracker(smoothing_window=4)
        assert tracker.last_seen is None
        tracker.push(-60.0, now=12.5)
        assert tracker.last_seen == 12.5

    def test_concurrent_push_and_read_is_safe(self) -> None:
        # push() runs on bleak's scanner-callback thread while the event loop
        # reads smoothed() — the two must not race on the smoother/deques.
        # Hammer from a writer thread; every read must be a finite float in the
        # pushed range (or None), never a torn value or an exception.
        tracker = RssiTracker(smoothing_window=8, stale_seconds=15.0)
        stop = threading.Event()
        errors: list[BaseException] = []

        def writer() -> None:
            t = 0.0
            try:
                while not stop.is_set():
                    tracker.push(-60.0, now=t)
                    t += 0.001
            except BaseException as exc:  # noqa: BLE001 - record any race failure
                errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(20000):
                value = tracker.smoothed(now=1.0)
                if value is not None:
                    assert -61.0 <= value <= -59.0
        finally:
            stop.set()
            thread.join()
        assert not errors
