"""Presence state machine: hysteresis + dwell timers around the safety radius.

Fail-safe invariant: any uncertainty (link lost, no samples) must drive the
machine toward AWAY, never toward NEAR.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PresenceState(Enum):
    NEAR = "near"
    LEAVING = "leaving"  # beyond radius, grace timer running
    AWAY = "away"  # lock has been requested
    RETURNING = "returning"  # back inside return threshold, dwell running


@dataclass
class PresenceConfig:
    radius_m: float = 3.0
    grace_seconds: float = 10.0
    return_seconds: float = 3.0
    # Return threshold is stricter than the leave threshold (Schmitt trigger)
    # so the state can't oscillate when hovering exactly at the radius.
    return_ratio: float = 0.8


class PresenceMachine:
    """Drive with `update(distance_m, now)`; returns True when a lock must fire.

    `distance_m=None` means "no reliable signal" (link lost, Bluetooth off)
    and is treated as infinitely far — the fail-safe path.
    """

    def __init__(self, config: PresenceConfig) -> None:
        self._cfg = config
        self._state = PresenceState.NEAR
        self._timer_start: float | None = None

    @property
    def state(self) -> PresenceState:
        return self._state

    def update(self, distance_m: float | None, now: float) -> bool:
        d = float("inf") if distance_m is None else distance_m
        beyond = d > self._cfg.radius_m
        within_return = d < self._cfg.radius_m * self._cfg.return_ratio

        if self._state is PresenceState.NEAR:
            if beyond:
                self._state = PresenceState.LEAVING
                self._timer_start = now
        elif self._state is PresenceState.LEAVING:
            if not beyond:
                self._state = PresenceState.NEAR
                self._timer_start = None
            elif (
                self._timer_start is not None and now - self._timer_start >= self._cfg.grace_seconds
            ):
                self._state = PresenceState.AWAY
                self._timer_start = None
                return True  # caller must lock the screen now
        elif self._state is PresenceState.AWAY:
            if within_return:
                self._state = PresenceState.RETURNING
                self._timer_start = now
        elif self._state is PresenceState.RETURNING:
            if not within_return:
                self._state = PresenceState.AWAY
                self._timer_start = None
            elif (
                self._timer_start is not None
                and now - self._timer_start >= self._cfg.return_seconds
            ):
                self._state = PresenceState.NEAR
                self._timer_start = None
        return False
