from stavau.core.presence import PresenceConfig, PresenceMachine, PresenceState


def make_machine(radius: float = 3.0, grace: float = 10.0) -> PresenceMachine:
    return PresenceMachine(PresenceConfig(radius_m=radius, grace_seconds=grace, return_seconds=3.0))


class TestLockTrigger:
    def test_lock_fires_only_after_grace_period(self) -> None:
        m = make_machine()
        assert m.update(5.0, now=0.0) is False  # beyond radius, timer starts
        assert m.state is PresenceState.LEAVING
        assert m.update(5.0, now=9.0) is False  # still within grace
        assert m.update(5.0, now=10.0) is True  # grace elapsed -> lock
        assert m.state is PresenceState.AWAY

    def test_brief_excursion_does_not_lock(self) -> None:
        m = make_machine()
        m.update(5.0, now=0.0)
        assert m.update(2.0, now=4.0) is False  # came back before grace
        assert m.state is PresenceState.NEAR

    def test_signal_loss_is_fail_safe(self) -> None:
        m = make_machine()
        assert m.update(None, now=0.0) is False  # link lost -> treated as far
        assert m.state is PresenceState.LEAVING
        assert m.update(None, now=10.0) is True  # locks after grace


class TestHysteresis:
    def test_hovering_at_radius_does_not_unlatch_away_state(self) -> None:
        m = make_machine(radius=3.0)
        m.update(5.0, now=0.0)
        m.update(5.0, now=10.0)  # AWAY
        # 2.9 m is inside the leave radius but outside the return threshold (2.4 m)
        m.update(2.9, now=11.0)
        assert m.state is PresenceState.AWAY

    def test_return_requires_dwell(self) -> None:
        m = make_machine(radius=3.0)
        m.update(5.0, now=0.0)
        m.update(5.0, now=10.0)  # AWAY
        m.update(1.0, now=11.0)  # inside return threshold, dwell starts
        assert m.state is PresenceState.RETURNING
        m.update(1.0, now=14.0)  # dwell (3 s) elapsed
        assert m.state is PresenceState.NEAR
