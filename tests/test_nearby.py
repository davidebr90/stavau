import pytest

from stavau.core.monitor import NearbyCache, RssiTracker


class TestNearbyCache:
    def test_lists_devices_strongest_first(self) -> None:
        cache = NearbyCache(max_age_seconds=30.0)
        cache.push("AA:AA:AA:AA:AA:AA", "phone", -70.0, now=0.0)
        cache.push("BB:BB:BB:BB:BB:BB", None, -50.0, now=1.0)
        devices = cache.list(now=2.0)
        assert [d.address for d in devices] == ["BB:BB:BB:BB:BB:BB", "AA:AA:AA:AA:AA:AA"]
        assert devices[0].name == "<unnamed>"
        assert devices[1].name == "phone"

    def test_stale_entries_expire(self) -> None:
        cache = NearbyCache(max_age_seconds=30.0)
        cache.push("AA:AA:AA:AA:AA:AA", "phone", -60.0, now=0.0)
        assert cache.list(now=29.0)
        assert cache.list(now=31.0) == []

    def test_name_is_remembered_across_anonymous_advertisements(self) -> None:
        # Some devices alternate named scan responses with anonymous ADV packets:
        # a later nameless packet must not erase the name we already learned.
        cache = NearbyCache(max_age_seconds=30.0)
        cache.push("AA:AA:AA:AA:AA:AA", "phone", -60.0, now=0.0)
        cache.push("AA:AA:AA:AA:AA:AA", None, -62.0, now=1.0)
        devices = cache.list(now=2.0)
        assert devices[0].name == "phone"
        assert devices[0].rssi == pytest.approx(-62.0)


class TestTrackerReset:
    def test_reset_forgets_history(self) -> None:
        tracker = RssiTracker(smoothing_window=4)
        tracker.push(-55.0, now=0.0)
        assert tracker.smoothed(now=1.0) is not None
        tracker.reset()
        assert tracker.smoothed(now=1.0) is None
        assert tracker.last_seen is None
        # New samples after reset are not diluted by the old device's history.
        tracker.push(-85.0, now=2.0)
        assert tracker.smoothed(now=2.0) == pytest.approx(-85.0)
