"""Auto-unlock policy: the security-critical decision machine (WS-F).

These tests exist to prove the safety properties, not just behaviour:
  * a foreign (manual) lock is NEVER auto-unlocked (T9);
  * no positive proximity evidence never unlocks (T2);
  * every necessary condition is genuinely necessary (drop one -> no unlock).
"""

import pytest

from stavau.core.autounlock import AutoUnlockConfig, AutoUnlockPolicy, LockOrigin


def make_policy(
    *, enabled: bool = True, strict: float = 0.5, dwell: float = 5.0
) -> AutoUnlockPolicy:
    return AutoUnlockPolicy(
        AutoUnlockConfig(enabled=enabled, strict_ratio=strict, dwell_seconds=dwell)
    )


def arm_with_stavau_lock(policy: AutoUnlockPolicy) -> None:
    """Simulate: stavau locks, then the observer confirms the screen is locked."""
    policy.note_stavau_lock()
    policy.note_lock_observed(True)
    assert policy.origin is LockOrigin.STAVAU


class TestOriginClassification:
    def test_stavau_lock_then_observed_is_stavau_origin(self) -> None:
        policy = make_policy()
        arm_with_stavau_lock(policy)

    def test_foreign_lock_is_never_stavau(self) -> None:
        # Observed locked WITHOUT a preceding stavau lock = manual/foreign.
        policy = make_policy()
        policy.note_lock_observed(True)
        assert policy.origin is LockOrigin.FOREIGN

    def test_unlock_resets_to_unlocked(self) -> None:
        policy = make_policy()
        arm_with_stavau_lock(policy)
        policy.note_lock_observed(False)
        assert policy.origin is LockOrigin.UNLOCKED

    def test_unknown_state_does_not_fabricate_origin(self) -> None:
        policy = make_policy()
        policy.note_lock_observed(None)
        assert policy.origin is LockOrigin.UNLOCKED
        arm_with_stavau_lock(policy)
        policy.note_lock_observed(None)  # a gap must not change a known origin
        assert policy.origin is LockOrigin.STAVAU


class TestDecideSafety:
    def test_disabled_never_unlocks(self) -> None:
        policy = make_policy(enabled=False)
        arm_with_stavau_lock(policy)
        assert policy.decide(0.1, 3.0, is_paired=True, now=100.0) is False

    def test_foreign_lock_never_unlocks_even_when_present_and_paired(self) -> None:
        # The core T9 guarantee.
        policy = make_policy(dwell=0.0)
        policy.note_lock_observed(True)  # foreign
        assert policy.decide(0.0, 3.0, is_paired=True, now=0.0) is False
        assert policy.decide(0.0, 3.0, is_paired=True, now=100.0) is False

    def test_requires_paired(self) -> None:
        policy = make_policy(dwell=0.0)
        arm_with_stavau_lock(policy)
        assert policy.decide(0.1, 3.0, is_paired=False, now=0.0) is False
        assert policy.decide(0.1, 3.0, is_paired=True, now=0.0) is True

    def test_no_signal_never_unlocks(self) -> None:
        # T2: absence of proximity evidence must not unlock (opposite of lock).
        policy = make_policy(dwell=0.0)
        arm_with_stavau_lock(policy)
        assert policy.decide(None, 3.0, is_paired=True, now=0.0) is False

    def test_must_be_within_strict_threshold_not_just_radius(self) -> None:
        policy = make_policy(strict=0.5, dwell=0.0)
        arm_with_stavau_lock(policy)
        # radius 3 m, strict 0.5 -> threshold 1.5 m. 2 m is inside the radius but
        # NOT inside the stricter unlock threshold.
        assert policy.decide(2.0, 3.0, is_paired=True, now=0.0) is False
        assert policy.decide(1.4, 3.0, is_paired=True, now=0.0) is True

    def test_dwell_must_elapse_continuously(self) -> None:
        policy = make_policy(strict=0.5, dwell=5.0)
        arm_with_stavau_lock(policy)
        assert policy.decide(0.5, 3.0, is_paired=True, now=0.0) is False  # dwell starts
        assert policy.decide(0.5, 3.0, is_paired=True, now=4.9) is False
        assert policy.decide(0.5, 3.0, is_paired=True, now=5.0) is True

    def test_leaving_the_threshold_resets_the_dwell(self) -> None:
        policy = make_policy(strict=0.5, dwell=5.0)
        arm_with_stavau_lock(policy)
        policy.decide(0.5, 3.0, is_paired=True, now=0.0)  # dwell starts at 0
        # Step outside the strict threshold: dwell must reset.
        assert policy.decide(2.0, 3.0, is_paired=True, now=3.0) is False
        # Back inside: the clock restarts from here, not from t=0.
        assert policy.decide(0.5, 3.0, is_paired=True, now=6.0) is False
        assert policy.decide(0.5, 3.0, is_paired=True, now=11.0) is True

    def test_signal_loss_mid_dwell_resets(self) -> None:
        policy = make_policy(strict=0.5, dwell=5.0)
        arm_with_stavau_lock(policy)
        policy.decide(0.5, 3.0, is_paired=True, now=0.0)
        assert policy.decide(None, 3.0, is_paired=True, now=2.0) is False  # reset
        assert policy.decide(0.5, 3.0, is_paired=True, now=3.0) is False  # restart
        assert policy.decide(0.5, 3.0, is_paired=True, now=8.0) is True


class TestConfigValidation:
    @pytest.mark.parametrize("ratio", [0.0, -0.1, 1.5])
    def test_bad_strict_ratio_rejected(self, ratio: float) -> None:
        with pytest.raises(ValueError):
            AutoUnlockPolicy(AutoUnlockConfig(strict_ratio=ratio))

    def test_negative_dwell_rejected(self) -> None:
        with pytest.raises(ValueError):
            AutoUnlockPolicy(AutoUnlockConfig(dwell_seconds=-1.0))
