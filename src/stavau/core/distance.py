"""RSSI -> distance estimation with smoothing.

Model: log-distance path loss.
    RSSI(d) = rssi_at_1m - 10 * n * log10(d)
    =>  d   = 10 ** ((rssi_at_1m - rssi) / (10 * n))

`rssi_at_1m` and the path-loss exponent `n` are fitted per user/environment by
the calibration wizard (see docs/rssi-calibration.md). RSSI must never be
consumed raw: pass samples through `RssiSmoother` first.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class CalibrationModel:
    """Fitted parameters of the log-distance path loss model."""

    rssi_at_1m: float
    path_loss_exponent: float

    # Fits outside this range almost always mean a botched calibration run
    # (movement, strong reflections), not a real environment.
    MIN_EXPONENT = 1.2
    MAX_EXPONENT = 4.5

    def __post_init__(self) -> None:
        if not (self.MIN_EXPONENT <= self.path_loss_exponent <= self.MAX_EXPONENT):
            raise ValueError(
                f"path_loss_exponent {self.path_loss_exponent} outside plausible "
                f"range [{self.MIN_EXPONENT}, {self.MAX_EXPONENT}]; re-run calibration"
            )

    def distance_m(self, rssi: float) -> float:
        """Estimated distance in metres for a (smoothed) RSSI value in dBm."""
        return float(10 ** ((self.rssi_at_1m - rssi) / (10 * self.path_loss_exponent)))


class RssiSmoother:
    """Median pre-filter (spike rejection) followed by a moving average.

    Feed one raw sample at a time; read `.value` for the smoothed RSSI.
    """

    def __init__(self, window: int = 8, median_window: int = 3) -> None:
        if window < 1 or median_window < 1:
            raise ValueError("window sizes must be >= 1")
        self._median_buf: deque[float] = deque(maxlen=median_window)
        self._avg_buf: deque[float] = deque(maxlen=window)

    def push(self, rssi: float) -> float:
        self._median_buf.append(rssi)
        self._avg_buf.append(statistics.median(self._median_buf))
        return self.value

    @property
    def value(self) -> float:
        if not self._avg_buf:
            raise ValueError("no samples yet")
        return sum(self._avg_buf) / len(self._avg_buf)
