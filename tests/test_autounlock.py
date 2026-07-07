"""Auto-unlock policy: the security-critical decision machine (WS-F).

These tests exist to prove the safety properties, not just behaviour:
  * a foreign (manual) lock is NEVER auto-unlocked (T9);
  * a stale stavau-lock expectation never captures a later foreign lock (T9);
  * no positive proximity evidence never unlocks (T2);
  * the anti-relay dwell cannot be satisfied on the arming tick;
  * every necessary condition is genuinely necessary (drop one -> no unlock).
"""

import pytest

from stavau.core.autounlock import AutoUnlockConfig, AutoUnlockPolicy, LockOrigin


def make_policy(
    *,
    enabled: bool = True,
    strict: float = 0.5,
    dwell: float = 5.0,
    expect_window: float = 5.0,
) -> AutoUnlockPolicy:
    return AutoUnlockPolicy(
        AutoUnlockConfig(
            enabled=enabled,
            strict_ratio=strict,
            dwell_seconds=dwell,
            expect_window_seconds=expect_window,
        )
    )


def arm_with_stavau_lock(policy: AutoUnlockPolicy, now: float = 0.0) -> None:
    """Simulate: stavau locks, then the observer confirms the screen is locked."""
    policy.note_stavau_lock(now)
    policy.note_lock_observed(True, now)
    assert policy.origin is LockOrigin.STAVAU


def unlock_after_dwell(
    policy: AutoUnlockPolicy, distance: float, radius: float = 3.0, *, dwell: float = 5.0
) -> bool:
    """Drive one arming tick + one tick past the dwell; return the final decide."""
    policy.decide(distance, radius, is_paired=True, now=0.0)  # arms the dwell
    return policy.decide(distance, radius, is_paired=True, now=dwell)


class TestOriginClassification:
    def test_stavau_lock_then_observed_is_stavau_origin(self) -> None:
        policy = make_policy()
        arm_with_stavau_lock(policy)

    def test_foreign_lock_is_never_stavau(self) -> None:
        # Observed locked WITHOUT a preceding stavau lock = manual/foreign.
        policy = make_policy()
        policy.note_lock_observed(True, 0.0)
        assert policy.origin is LockOrigin.FOREIGN

    def test_unlock_resets_to_unlocked(self) -> None:
        policy = make_policy()
        arm_with_stavau_lock(policy)
        policy.note_lock_observed(False, 1.0)
        assert policy.origin is LockOrigin.UNLOCKED

    def test_unknown_state_does_not_fabricate_origin(self) -> None:
        policy = make_policy()
        policy.note_lock_observed(None, 0.0)
        assert policy.origin is LockOrigin.UNLOCKED
        arm_with_stavau_lock(policy)
        policy.note_lock_observed(None, 1.0)  # a gap must not change a known origin
        assert policy.origin is LockOrigin.STAVAU

    def test_stale_expectation_does_not_capture_later_foreign_lock(self) -> None:
        # T9 fail-open guard: stavau's lock action fired but the observer never
        # saw the lock (missed edge / silent no-op), so the expectation stays
        # latched. A much later manual Win+L must NOT inherit stavau origin.
        policy = make_policy(expect_window=5.0)
        policy.note_stavau_lock(0.0)
        for t in range(1, 60):  # observer reports 'unknown' for a long time
            policy.note_lock_observed(None, float(t))
        policy.note_lock_observed(True, 1000.0)  # a genuine manual lock, much later
        assert policy.origin is LockOrigin.FOREIGN
        assert policy.decide(0.0, 3.0, is_paired=True, now=1000.0) is False

    def test_expectation_within_window_is_stavau(self) -> None:
        policy = make_policy(expect_window=5.0)
        policy.note_stavau_lock(0.0)
        policy.note_lock_observed(True, 3.0)  # observed within the window
        assert policy.origin is LockOrigin.STAVAU


class TestDecideSafety:
    def test_disabled_never_unlocks(self) -> None:
        policy = make_policy(enabled=False)
        arm_with_stavau_lock(policy)
        assert policy.decide(0.1, 3.0, is_paired=True, now=100.0) is False

    def test_foreign_lock_never_unlocks_even_when_present_and_paired(self) -> None:
        # The core T9 guarantee.
        policy = make_policy()
        policy.note_lock_observed(True, 0.0)  # foreign
        assert policy.decide(0.0, 3.0, is_paired=True, now=0.0) is False
        assert policy.decide(0.0, 3.0, is_paired=True, now=100.0) is False

    def test_requires_paired(self) -> None:
        policy = make_policy(dwell=5.0)
        arm_with_stavau_lock(policy)
        assert policy.decide(0.1, 3.0, is_paired=False, now=0.0) is False
        # Paired: arm the dwell, then a tick past it unlocks.
        assert policy.decide(0.1, 3.0, is_paired=True, now=0.0) is False  # arms
        assert policy.decide(0.1, 3.0, is_paired=True, now=5.0) is True

    def test_no_signal_never_unlocks(self) -> None:
        # T2: absence of proximity evidence must not unlock (opposite of lock).
        policy = make_policy()
        arm_with_stavau_lock(policy)
        assert policy.decide(None, 3.0, is_paired=True, now=0.0) is False
        assert policy.decide(None, 3.0, is_paired=True, now=100.0) is False

    def test_must_be_within_strict_threshold_not_just_radius(self) -> None:
        policy = make_policy(strict=0.5, dwell=5.0)
        arm_with_stavau_lock(policy)
        # radius 3 m, strict 0.5 -> threshold 1.5 m. 2 m is inside the radius but
        # NOT inside the stricter unlock threshold: never unlocks, ever.
        assert policy.decide(2.0, 3.0, is_paired=True, now=0.0) is False
        assert policy.decide(2.0, 3.0, is_paired=True, now=100.0) is False
        # 1.4 m is inside the strict threshold: unlocks after the dwell.
        assert unlock_after_dwell(policy, 1.4) is True

    def test_dwell_is_not_satisfied_on_the_arming_tick(self) -> None:
        # The very first in-range tick must never unlock, no matter how large now
        # is — the dwell clock only starts here (regression for the collapsed
        # identical-branch trap).
        policy = make_policy(strict=0.5, dwell=5.0)
        arm_with_stavau_lock(policy)
        assert policy.decide(0.5, 3.0, is_paired=True, now=1_000_000.0) is False

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

    @pytest.mark.parametrize("dwell", [0.0, -1.0])
    def test_non_positive_dwell_rejected(self, dwell: float) -> None:
        with pytest.raises(ValueError):
            AutoUnlockPolicy(AutoUnlockConfig(dwell_seconds=dwell))

    @pytest.mark.parametrize("window", [0.0, -1.0])
    def test_non_positive_expect_window_rejected(self, window: float) -> None:
        with pytest.raises(ValueError):
            AutoUnlockPolicy(AutoUnlockConfig(expect_window_seconds=window))
