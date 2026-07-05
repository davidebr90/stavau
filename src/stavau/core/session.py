"""Shared monitoring loop used by both the CLI (`run`) and the tray UI.

Encapsulates the full pipeline — RSSI tracking, distance model, presence state
machine, lock action, retry-on-failure and the anti-runaway circuit breaker —
behind one async `run()` with a per-tick callback. Keeping it in one place
means the fail-safe and guardrail logic cannot drift between front-ends.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from stavau.config.settings import Settings
from stavau.core.breaker import BreakerConfig, LockCircuitBreaker
from stavau.core.distance import CalibrationModel
from stavau.core.events import EventLog
from stavau.core.monitor import BleProximitySource, NearbyCache, RssiTracker
from stavau.core.presence import PresenceConfig, PresenceMachine, PresenceState
from stavau.platform.base import Locker, LockError

_LOCK_RETRY_SECONDS = 5.0


@dataclass(frozen=True)
class Tick:
    elapsed: float
    rssi: float | None
    distance: float | None
    state: PresenceState
    breaker_paused: bool
    breaker_seconds_remaining: float


class MonitorSession:
    def __init__(
        self,
        settings: Settings,
        locker: Locker | None,
        log: EventLog,
        *,
        nearby: NearbyCache | None = None,
    ) -> None:
        self._settings = settings
        self._locker = locker
        self._log = log
        self._model = CalibrationModel(
            rssi_at_1m=settings.rssi_at_1m, path_loss_exponent=settings.path_loss_exponent
        )
        self._machine = self._new_machine()
        self._tracker = RssiTracker(smoothing_window=settings.smoothing_window)
        self._source = BleProximitySource(settings.device_address, self._tracker, nearby=nearby)
        self._breaker = LockCircuitBreaker(
            BreakerConfig(
                max_locks=settings.breaker_max_locks,
                window_seconds=settings.breaker_window_seconds,
                cooldown_seconds=settings.breaker_cooldown_seconds,
            )
        )

    @property
    def source(self) -> BleProximitySource:
        return self._source

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
        )
        await self._source.start()
        started = time.monotonic()
        last_state = self._machine.state
        lock_pending_since: float | None = None
        breaker_announced = False
        try:
            while stop is None or not stop():
                now = time.monotonic()
                rssi = self._tracker.smoothed(now)
                distance = self._model.distance_m(rssi) if rssi is not None else None
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
                        )
                    )

                if duration is not None and now - started >= duration:
                    return
                await _sleep(1.0)
        finally:
            await self._source.stop()
            self._log.append("monitor_stopped")

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
