"""Anti-runaway safety guardrail for the lock action.

If a bug, a flapping RSSI, or a misconfiguration makes stavau lock the screen
over and over, the user could be shut out of their own machine. This circuit
breaker caps that: after `max_locks` locks within `window_seconds`, it trips
and *suppresses further locks* for `cooldown_seconds`, giving the user an
uninterrupted window to disable the daemon. It fails toward usability on this
axis on purpose — the opposite failure (locked out) is worse than a temporary
gap in proximity locking, and the OS idle-timeout lock still applies underneath.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class BreakerConfig:
    max_locks: int = 3
    window_seconds: float = 120.0
    cooldown_seconds: float = 300.0

    def validate(self) -> None:
        if self.max_locks < 1:
            raise ValueError("breaker max_locks must be at least 1")
        if self.window_seconds <= 0 or self.cooldown_seconds <= 0:
            raise ValueError("breaker window/cooldown must be positive")


class LockCircuitBreaker:
    """Tracks recent locks and pauses locking when they come too fast.

    Usage each time a lock would fire:
        if breaker.is_paused(now):
            ...suppress...
        else:
            locked = do_lock()
            if locked and breaker.register_lock(now):
                ...breaker just tripped, log it...
    """

    def __init__(self, config: BreakerConfig | None = None) -> None:
        self._cfg = config or BreakerConfig()
        self._cfg.validate()
        self._lock_times: deque[float] = deque()
        self._paused_until: float | None = None

    @property
    def config(self) -> BreakerConfig:
        return self._cfg

    def is_paused(self, now: float) -> bool:
        """True while locking is suppressed. Clears itself when cooldown ends."""
        if self._paused_until is None:
            return False
        if now < self._paused_until:
            return True
        # Cooldown elapsed: resume with a clean slate so the next burst is
        # measured fresh rather than against pre-pause history.
        self._paused_until = None
        self._lock_times.clear()
        return False

    def register_lock(self, now: float) -> bool:
        """Record a lock that just happened. Returns True if it trips the breaker."""
        self._lock_times.append(now)
        cutoff = now - self._cfg.window_seconds
        # Evict entries strictly older than the window; a lock exactly
        # window_seconds old is kept, so the window is inclusive of its boundary.
        # This trips fractionally more eagerly (the safe direction for an
        # anti-lockout guard) — chosen deliberately, pinned by a unit test.
        while self._lock_times and self._lock_times[0] < cutoff:
            self._lock_times.popleft()
        if len(self._lock_times) >= self._cfg.max_locks:
            self._paused_until = now + self._cfg.cooldown_seconds
            return True
        return False

    def resume_at(self) -> float | None:
        """Monotonic time when the pause ends, or None if not paused."""
        return self._paused_until

    def seconds_remaining(self, now: float) -> float:
        if self._paused_until is None:
            return 0.0
        return max(0.0, self._paused_until - now)
