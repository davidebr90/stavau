import pytest

from stavau.core.breaker import BreakerConfig, LockCircuitBreaker


def make_breaker(
    max_locks: int = 3, window: float = 120.0, cooldown: float = 300.0
) -> LockCircuitBreaker:
    return LockCircuitBreaker(
        BreakerConfig(max_locks=max_locks, window_seconds=window, cooldown_seconds=cooldown)
    )


class TestTripping:
    def test_trips_after_max_locks_in_window(self) -> None:
        b = make_breaker(max_locks=3, window=120.0)
        assert b.register_lock(0.0) is False
        assert b.register_lock(10.0) is False
        assert b.register_lock(20.0) is True  # third lock within 120 s -> trip
        assert b.is_paused(21.0) is True

    def test_does_not_trip_when_locks_are_spread_out(self) -> None:
        b = make_breaker(max_locks=3, window=120.0)
        assert b.register_lock(0.0) is False
        assert b.register_lock(130.0) is False  # first fell out of the window
        assert b.register_lock(260.0) is False
        assert b.is_paused(261.0) is False

    def test_pause_suppresses_until_cooldown_then_resumes(self) -> None:
        b = make_breaker(max_locks=3, window=120.0, cooldown=300.0)
        b.register_lock(0.0)
        b.register_lock(1.0)
        b.register_lock(2.0)  # trip at t=2 -> paused until 302
        assert b.is_paused(100.0) is True
        assert b.is_paused(301.9) is True
        assert b.is_paused(302.0) is False  # cooldown elapsed

    def test_history_cleared_after_cooldown(self) -> None:
        b = make_breaker(max_locks=3, window=120.0, cooldown=300.0)
        b.register_lock(0.0)
        b.register_lock(1.0)
        b.register_lock(2.0)  # trip
        b.is_paused(302.0)  # resume, clears history
        # A single lock right after resume must not immediately re-trip.
        assert b.register_lock(303.0) is False
        assert b.register_lock(304.0) is False
        assert b.register_lock(305.0) is True  # needs three again


class TestSecondsRemaining:
    def test_reports_cooldown_countdown(self) -> None:
        b = make_breaker(max_locks=2, window=60.0, cooldown=100.0)
        b.register_lock(0.0)
        b.register_lock(5.0)  # trip -> paused until 105
        assert b.seconds_remaining(5.0) == pytest.approx(100.0)
        assert b.seconds_remaining(55.0) == pytest.approx(50.0)
        assert b.seconds_remaining(105.0) == pytest.approx(0.0)

    def test_zero_when_not_paused(self) -> None:
        b = make_breaker()
        assert b.seconds_remaining(0.0) == 0.0
        assert b.resume_at() is None


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_locks": 0},
            {"window_seconds": 0.0},
            {"cooldown_seconds": -1.0},
        ],
    )
    def test_invalid_config_rejected(self, kwargs: dict[str, float]) -> None:
        base = {"max_locks": 3, "window_seconds": 120.0, "cooldown_seconds": 300.0}
        base.update(kwargs)
        with pytest.raises(ValueError):
            LockCircuitBreaker(BreakerConfig(**base))  # type: ignore[arg-type]
