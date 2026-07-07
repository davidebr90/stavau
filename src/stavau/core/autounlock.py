"""Auto-unlock decision policy — an advanced, off-by-default convenience.

Auto-unlock is the riskiest feature stavau can offer, so its decision logic is
a small, pure, exhaustively-tested state machine kept apart from I/O. It is
designed against the threat model *before* anything else:

  * T9 (auto-unlock abuse): stavau must NEVER unlock a screen it did not itself
    lock. If you press Win+L, or a screensaver / another tool locks the
    session, auto-unlock stays disarmed — full stop. The machine only arms when
    it observes that *its own* proximity lock is the reason the screen is
    locked, and disarms on any foreign lock or on unlock.

  * T2 (relay/amplification): a relayed device can look "present". Auto-unlock
    therefore demands *stronger* evidence than locking ever does — the device
    must be well *inside* a stricter fraction of the safety radius, continuously
    for a dwell period, before an unlock is permitted. Absence of signal never
    unlocks (the opposite of the lock fail-safe): no positive proximity
    evidence ⇒ no unlock.

  * Bonding: auto-unlock requires a *paired* (bonded) association, because a
    pairing-less advertisement identity is trivially spoofable.

All of these are necessary conditions ANDed together in `decide()`. Enabling
the feature also requires an explicit risk acknowledgement (see config) and an
OS that actually exposes a safe unlock (see platform.unlock) — without both,
the feature refuses to run rather than degrading.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LockOrigin(Enum):
    """Why the screen is currently locked (as far as stavau can tell)."""

    UNLOCKED = "unlocked"  # screen is not locked
    STAVAU = "stavau"  # stavau's own proximity lock — the only auto-unlockable case
    FOREIGN = "foreign"  # manual Win+L, screensaver, another tool — NEVER auto-unlock


@dataclass
class AutoUnlockConfig:
    enabled: bool = False
    # Device must be within radius * strict_ratio (stricter than the lock radius).
    strict_ratio: float = 0.5
    # ...continuously for this long before an unlock is permitted (anti-relay dwell).
    dwell_seconds: float = 5.0
    # Auto-unlock requires bonding; an unbonded advertisement id is spoofable.
    require_paired: bool = True
    # A stavau lock we expect must be *observed* within this window, otherwise the
    # observed lock is treated as foreign. Without this bound a stavau lock that is
    # never observed (missed edge, or a lock action that silently no-ops) would
    # leave the expectation latched, and a much later manual Win+L would then be
    # misclassified as ours and become auto-unlockable — a T9 fail-open.
    expect_window_seconds: float = 5.0

    def validate(self) -> None:
        if not 0.0 < self.strict_ratio <= 1.0:
            raise ValueError("auto_unlock strict_ratio must be in (0, 1]")
        if self.dwell_seconds <= 0:
            # The dwell is the anti-relay gate on the riskiest feature; a zero
            # dwell would allow an unlock on the very next in-range tick.
            raise ValueError("auto_unlock dwell_seconds must be positive")
        if self.expect_window_seconds <= 0:
            raise ValueError("auto_unlock expect_window_seconds must be positive")


class AutoUnlockPolicy:
    """Tracks lock origin + proximity dwell and decides when unlocking is allowed.

    Wiring contract (the session must honour all of these):
      * call `note_stavau_lock(now)` right after stavau's own lock action
        succeeds;
      * call `note_lock_observed(is_locked, now)` every tick with the real
        observed lock state (from the lock-state observer) — this is what
        distinguishes a stavau lock from a foreign one and detects unlocks;
      * call `decide(distance, radius_m, is_paired, now)` each tick and, only if
        it returns True, perform the actual unlock.

    `now` is a monotonic timestamp shared with `decide`; it bounds how long a
    pending stavau-lock expectation stays valid (see `expect_window_seconds`).
    """

    def __init__(self, config: AutoUnlockConfig) -> None:
        config.validate()
        self._cfg = config
        self._origin = LockOrigin.UNLOCKED
        self._expecting_own_lock = False
        self._expecting_since = 0.0
        self._close_since: float | None = None

    @property
    def origin(self) -> LockOrigin:
        return self._origin

    def note_stavau_lock(self, now: float) -> None:
        """stavau just issued a lock. The next observed 'locked' is ours —

        but only if it is observed within `expect_window_seconds`; a stale
        expectation must not capture a later foreign lock (T9).
        """
        self._expecting_own_lock = True
        self._expecting_since = now

    def note_lock_observed(self, is_locked: bool | None, now: float) -> None:
        """Feed the observed screen-lock state to classify the lock's origin.

        None (unknown) is treated conservatively: it never promotes a lock to
        'stavau' origin, so auto-unlock cannot fire without a *positive* locked
        observation — on platforms without a reliable observer, auto-unlock
        simply never triggers.
        """
        if is_locked is True:
            if self._origin is LockOrigin.UNLOCKED:
                # A fresh lock. It is ours only if we were expecting one AND it
                # arrived promptly; a stale expectation decays to 'foreign'.
                expected = (
                    self._expecting_own_lock
                    and now - self._expecting_since <= self._cfg.expect_window_seconds
                )
                self._origin = LockOrigin.STAVAU if expected else LockOrigin.FOREIGN
            # Expectation is single-shot: consume it whether or not it applied.
            self._expecting_own_lock = False
        elif is_locked is False:
            # Screen is unlocked again: reset everything to a clean slate.
            self._origin = LockOrigin.UNLOCKED
            self._expecting_own_lock = False
            self._close_since = None
        # is_locked is None: keep current origin; do not fabricate a transition.

    def decide(
        self, distance_m: float | None, radius_m: float, is_paired: bool, now: float
    ) -> bool:
        """Return True only when EVERY safety condition holds. Never raises."""
        if not self._cfg.enabled:
            return False
        if self._origin is not LockOrigin.STAVAU:
            # Only stavau's own lock is ever auto-unlockable (T9). Foreign or
            # no lock ⇒ never.
            self._close_since = None
            return False
        if self._cfg.require_paired and not is_paired:
            self._close_since = None
            return False
        # Positive proximity evidence required (T2). No signal ⇒ reset dwell.
        if distance_m is None:
            self._close_since = None
            return False
        strict_threshold = radius_m * self._cfg.strict_ratio
        if distance_m > strict_threshold:
            self._close_since = None
            return False
        # Within the strict threshold: require it continuously for the dwell.
        # The arming tick only *starts* the clock — it can never itself satisfy
        # the dwell (guarantees at least one full interval of distinct in-range
        # observations, and removes the identical-branch trap).
        if self._close_since is None:
            self._close_since = now
            return False
        return now - self._close_since >= self._cfg.dwell_seconds
