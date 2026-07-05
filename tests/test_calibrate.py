import math

import pytest

from stavau.core.calibrate import fit_model, median_rssi


def synth_rssi(rssi_at_1m: float, n: float, distance: float) -> float:
    return rssi_at_1m - 10 * n * math.log10(distance)


class TestFitModel:
    def test_multi_station_fit_recovers_known_parameters(self) -> None:
        truth_1m, truth_n = -55.0, 2.2
        stations = [(d, synth_rssi(truth_1m, truth_n, d)) for d in (1.0, 3.0, 5.0)]
        model = fit_model(stations)
        assert model.rssi_at_1m == pytest.approx(truth_1m, abs=0.01)
        assert model.path_loss_exponent == pytest.approx(truth_n, abs=0.01)

    def test_single_station_at_1m_sets_reference_with_default_exponent(self) -> None:
        model = fit_model([(1.0, -60.0)])
        assert model.rssi_at_1m == pytest.approx(-60.0)
        assert model.path_loss_exponent == pytest.approx(2.0)

    def test_single_station_at_other_distance_back_projects_reference(self) -> None:
        # -66.02 dBm at 2 m with n=2 corresponds to -60 dBm at 1 m.
        model = fit_model([(2.0, synth_rssi(-60.0, 2.0, 2.0))])
        assert model.rssi_at_1m == pytest.approx(-60.0, abs=0.01)

    def test_implausible_fit_is_rejected(self) -> None:
        # Nearly flat RSSI across distances -> absurdly small exponent.
        with pytest.raises(ValueError):
            fit_model([(1.0, -60.0), (10.0, -61.0)])

    def test_duplicate_distances_rejected(self) -> None:
        with pytest.raises(ValueError):
            fit_model([(2.0, -60.0), (2.0, -65.0)])

    @pytest.mark.parametrize("stations", [[], [(-1.0, -60.0)], [(0.0, -60.0)]])
    def test_invalid_stations_rejected(self, stations: list[tuple[float, float]]) -> None:
        with pytest.raises(ValueError):
            fit_model(stations)


class TestMedianRssi:
    def test_median(self) -> None:
        assert median_rssi([-60.0, -95.0, -61.0]) == pytest.approx(-61.0)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            median_rssi([])
