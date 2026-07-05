import pytest

from stavau.core.distance import CalibrationModel, RssiSmoother


class TestCalibrationModel:
    def test_distance_at_reference_rssi_is_1m(self) -> None:
        model = CalibrationModel(rssi_at_1m=-55.0, path_loss_exponent=2.0)
        assert model.distance_m(-55.0) == pytest.approx(1.0)

    def test_free_space_doubling_distance_costs_6dbm(self) -> None:
        model = CalibrationModel(rssi_at_1m=-55.0, path_loss_exponent=2.0)
        assert model.distance_m(-61.0) == pytest.approx(2.0, rel=0.01)
        assert model.distance_m(-75.0) == pytest.approx(10.0, rel=0.01)

    def test_weaker_signal_means_farther(self) -> None:
        model = CalibrationModel(rssi_at_1m=-50.0, path_loss_exponent=2.5)
        assert model.distance_m(-80.0) > model.distance_m(-60.0)

    @pytest.mark.parametrize("bad_n", [0.5, 1.1, 4.6, 10.0])
    def test_implausible_exponent_rejected(self, bad_n: float) -> None:
        with pytest.raises(ValueError):
            CalibrationModel(rssi_at_1m=-55.0, path_loss_exponent=bad_n)


class TestRssiSmoother:
    def test_constant_signal_passes_through(self) -> None:
        s = RssiSmoother(window=4)
        for _ in range(10):
            s.push(-60.0)
        assert s.value == pytest.approx(-60.0)

    def test_single_spike_is_rejected_by_median_prefilter(self) -> None:
        s = RssiSmoother(window=4, median_window=3)
        for _ in range(4):
            s.push(-60.0)
        s.push(-95.0)  # spike (e.g. body momentarily blocking antenna)
        s.push(-60.0)
        # Median window of 3 never lets the lone -95 through.
        assert s.value == pytest.approx(-60.0)

    def test_sustained_change_converges(self) -> None:
        s = RssiSmoother(window=4, median_window=3)
        for _ in range(6):
            s.push(-55.0)
        for _ in range(8):
            s.push(-80.0)
        assert s.value == pytest.approx(-80.0)

    def test_value_before_samples_raises(self) -> None:
        with pytest.raises(ValueError):
            _ = RssiSmoother().value
