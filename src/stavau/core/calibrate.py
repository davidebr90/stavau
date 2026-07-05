"""Fit the log-distance path loss model from user calibration stations.

Linear least squares of RSSI against log10(distance): the intercept is the
reference power at 1 m, the slope is -10 * n. See docs/rssi-calibration.md.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from stavau.core.distance import CalibrationModel

DEFAULT_PATH_LOSS_EXPONENT = 2.0


def median_rssi(samples: Sequence[float]) -> float:
    if not samples:
        raise ValueError("no RSSI samples collected")
    return float(statistics.median(samples))


def fit_model(stations: Sequence[tuple[float, float]]) -> CalibrationModel:
    """Fit (rssi_at_1m, n) from (distance_m, median_rssi) stations.

    A single station fixes rssi_at_1m and keeps the default free-space-like
    exponent; two or more distinct distances fit both parameters.
    Raises ValueError when the fit is implausible (bad calibration run).
    """
    if not stations:
        raise ValueError("at least one calibration station is required")
    if any(d <= 0 for d, _ in stations):
        raise ValueError("station distances must be positive")

    if len(stations) == 1:
        distance, rssi = stations[0]
        rssi_at_1m = rssi + 10 * DEFAULT_PATH_LOSS_EXPONENT * math.log10(distance)
        return CalibrationModel(
            rssi_at_1m=rssi_at_1m, path_loss_exponent=DEFAULT_PATH_LOSS_EXPONENT
        )

    xs = [math.log10(d) for d, _ in stations]
    ys = [rssi for _, rssi in stations]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("stations must cover at least two distinct distances")
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / sxx
    return CalibrationModel(rssi_at_1m=y_mean - slope * x_mean, path_loss_exponent=-slope / 10)
