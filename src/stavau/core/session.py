"""Shared monitoring loop used by both the CLI (`run`) and the tray UI.

Encapsulates the full pipeline — RSSI tracking, distance model, presence state
machine, lock action, retry-on-failure and the anti-runaway circuit breaker —
behind one async `run()` with a per-tick callback. Keeping it in one place
means the fail-safe and guardrail logic cannot drift between front-ends.

Lock-state feedback: an optional `LockStateObserver` reports whether the OS
session is already locked. The loop polls `observer.current()` once per tick
instead of using `subscribe()` callbacks — the loop already wakes every
second, polling keeps every state read on the event-loop thread (no
cross-thread callback synchronization), and one tick of latency is well below
the grace period. When the observer says the screen is already locked, the
redundant lock action is skipped (and NOT registered on the circuit breaker —
no actual lock happened). An unknown state (None, missing observer, or an
observer error) NEVER suppresses locking: the loop then behaves exactly as if
no observer existed (invariant I1).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from stavau.config.settings import Settings
from stavau.core.breaker import BreakerConfig, LockCircuitBreaker
from stavau.core.distance import CalibrationModel
from stavau.core.events import EventLog
from stavau.core.monitor import NearbyCache, RssiTracker
from stavau.core.presence import PresenceConfig, PresenceMachine, PresenceState
from stavau.core.radiostate import radio_available
from stavau.core.strategy import ProximitySource, build_source
from stavau.platform.base import Locker, LockError
from stavau.platform.lockstate import LockStateObserver, get_lock_state_observer

_LOCK_RETRY_SECONDS = 5.0


class _UnsetType:
    """Sentinel type: distinguishes "argument not passed" from an explicit None."""


_UNSET = _UnsetType()


@dataclass(frozen=True)
class Tick:
    elapsed: float
    rssi: float | None
    distance: float | None
    state: PresenceState
    breaker_paused: bool
    breaker_seconds_remaining: float
    screen_locked: bool | None
    radio_off: bool


class MonitorSession:
    def __init__(
        self,
        settings: Settings,
        locker: Locker | None,
        log: EventLog,
        *,
        nearby: NearbyCache | None = None,
        observer: LockStateObserver | None | _UnsetType = _UNSET,
    ) -> None:
        self._settings = settings
        self._locker = locker
        self._log = log
        # The session owns the observer lifecycle: it is closed when run() ends.
        self._observer = get_lock_state_observer() if isinstance(observer, _UnsetType) else observer
        self._observer_error_logged = False
        self._model = CalibrationModel(
            rssi_at_1m=settings.rssi_at_1m, path_loss_exponent=settings.path_loss_exponent
        )
        self._machine = self._new_machine()
        self._tracker = RssiTracker(smoothing_window=settings.smoothing_window)
        built = build_source(
            settings.strategy,
            settings.device_address,
            self._tracker,
            nearby,
            radius_m=settings.radius_m,
            grace_seconds=settings.grace_seconds,
            rssi_at_1m=settings.rssi_at_1m,
            path_loss_exponent=settings.path_loss_exponent,
        )
        self._source = built.source
        self._effective_strategy = built.effective_strategy
        self._strategy_note = built.note
        self._breaker = LockCircuitBreaker(
            BreakerConfig(
                max_locks=settings.breaker_max_locks,
                window_seconds=settings.breaker_window_seconds,
                cooldown_seconds=settings.breaker_cooldown_seconds,
            )
        )
        self._radio_state: bool | None = None
        self._radio_off_reported = False
        self._ticks_since_radio_check = 0

    @property
    def source(self) -> ProximitySource:
        return self._source

    @property
    def strategy_note(self) -> str:
        return self._strategy_note

    @property
    def machine(self) -> PresenceMachine:
        return self._machine

    def _new_machine(self) -> PresenceMachine:
        return PresenceMachine(
            PresenceConfig(
                radius_m=self._settings.radius_m,
                grace_seconds=self._settings.grace_seconds,
                return_seconds=self._settings.return_seconds,
            )
        )

    def retarget(self, address: str) -> None:
        """Switch to another device live: reset RSSI history and presence state."""
        self._source.retarget(address)
        self._tracker.reset()
        self._machine = self._new_machine()

    async def run(
        self,
        *,
        stop: Callable[[], bool] | None = None,
        duration: float | None = None,
        on_tick: Callable[[Tick], None] | None = None,
    ) -> None:
        self._log.append(
            "monitor_started",
            device=self._settings.device_alias,
            dry_run=self._locker is None,
            strategy=self._effective_strategy,
        )
        await self._source.start()
        started = time.monotonic()
        last_state = self._machine.state
        lock_pending_since: float | None = None
        breaker_announced = False
        last_known_lock_state: bool | None = None
        try:
            while stop is None or not stop():
                now = time.monotonic()
                screen_locked = self._poll_lock_state()
                if screen_locked is not None:
                    if last_known_lock_state is not None and screen_locked != last_known_lock_state:
                        self._log.append("session_locked" if screen_locked else "session_unlocked")
                    last_known_lock_state = screen_locked
                rssi = self._tracker.smoothed(now)
                distance = self._model.distance_m(rssi) if rssi is not None else None

                # Refresh the radio-off reading at most once every 5 ticks: the
                # probe shells out (Linux) or calls into WinRT (Windows), so it
                # must stay cheap relative to the once-a-second tick cadence.
                if rssi is None:
                    if self._ticks_since_radio_check >= 5 or self._radio_state is None:
                        self._radio_state = await radio_available()
                        self._ticks_since_radio_check = 0
                    else:
                        self._ticks_since_radio_check += 1
                else:
                    self._radio_state = None
                    self._ticks_since_radio_check = 0

                radio_off = rssi is None and self._radio_state is False
                if radio_off and not self._radio_off_reported:
                    self._log.append("radio_off")
                    self._radio_off_reported = True
                elif not radio_off and self._radio_off_reported:
                    self._log.append("radio_on")
                    self._radio_off_reported = False

                must_lock = self._machine.update(distance, now)

                if self._machine.state is not last_state:
                    self._log.append(
                        "state_changed",
                        state=self._machine.state.value,
                        distance=None if distance is None else round(distance, 2),
                    )
                    last_state = self._machine.state
                if self._machine.state is PresenceState.NEAR:
                    lock_pending_since = None

                retry_due = (
                    lock_pending_since is not None
                    and now - lock_pending_since >= _LOCK_RETRY_SECONDS
                )
                if must_lock or retry_due:
                    if self._breaker.is_paused(now):
                        # Guardrail active: do not lock, and stop retrying so the
                        # user gets an uninterrupted window to disable the daemon.
                        lock_pending_since = None
                        if not breaker_announced:
                            self._log.append(
                                "breaker_suppressed_lock",
                                resume_in_s=round(self._breaker.seconds_remaining(now)),
                            )
                            breaker_announced = True
                    elif screen_locked is True:
                        # Observer confirms the screen is already locked: skip the
                        # redundant lock action. Counts as success for retry purposes
                        # but is NOT registered on the breaker — no actual lock
                        # happened. Only an affirmative True skips; None/unknown
                        # keeps locking (invariant I1).
                        breaker_announced = False
                        lock_pending_since = None
                        self._log.append("lock_skipped_already_locked")
                    else:
                        breaker_announced = False
                        if self._trigger_lock():
                            lock_pending_since = None
                            if self._breaker.register_lock(now):
                                self._log.append(
                                    "breaker_tripped",
                                    max_locks=self._settings.breaker_max_locks,
                                    cooldown_s=round(self._settings.breaker_cooldown_seconds),
                                )
                        else:
                            lock_pending_since = now

                if on_tick is not None:
                    paused = self._breaker.is_paused(now)
                    on_tick(
                        Tick(
                            elapsed=now - started,
                            rssi=rssi,
                            distance=distance,
                            state=self._machine.state,
                            breaker_paused=paused,
                            breaker_seconds_remaining=self._breaker.seconds_remaining(now),
                            screen_locked=screen_locked,
                            radio_off=radio_off,
                        )
                    )

                if duration is not None and now - started >= duration:
                    return
                await _sleep(1.0)
        finally:
            await self._source.stop()
            if self._observer is not None:
                # Advisory-only resource: a failing close must not mask shutdown.
                with contextlib.suppress(Exception):
                    self._observer.close()
            self._log.append("monitor_stopped")

    def _poll_lock_state(self) -> bool | None:
        """Read the observed lock state, degrading any failure to "unknown".

        Invariant I1: this must never raise — an observer problem downgrades to
        None so the caller keeps locking exactly as if no observer existed.
        """
        if self._observer is None:
            return None
        try:
            state = self._observer.current()
        except Exception as exc:
            if not self._observer_error_logged:
                # Log once per error streak to avoid flooding the event log.
                self._log.append("lock_observer_error", error=str(exc))
                self._observer_error_logged = True
            return None
        self._observer_error_logged = False
        return state

    def _trigger_lock(self) -> bool:
        if self._locker is None:
            self._log.append("lock_triggered", dry_run=True)
            return True
        try:
            self._locker.lock()
        except LockError as exc:
            # Loud failure: a silent one would leave the user believing they're protected.
            self._log.append("lock_failed", error=str(exc))
            return False
        self._log.append("lock_triggered", dry_run=False)
        return True


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
