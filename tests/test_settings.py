from pathlib import Path

import pytest

from stavau.config.settings import ConfigError, Settings


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        original = Settings(
            device_address="AA:BB:CC:DD:EE:FF",
            device_alias="my phone",
            radius_m=5.0,
            rssi_at_1m=-58.5,
            path_loss_exponent=2.3,
        )
        path = tmp_path / "config.json"
        original.save(path)
        assert Settings.load(path) == original

    def test_load_missing_file_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="stavau setup"):
            Settings.load(tmp_path / "nope.json")

    def test_load_corrupt_json_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="unreadable"):
            Settings.load(path)

    def test_load_non_object_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ConfigError, match="malformed"):
            Settings.load(path)

    def test_unknown_keys_are_ignored_for_forward_compatibility(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        Settings(device_address="AA:BB:CC:DD:EE:FF").save(path)
        raw = path.read_text(encoding="utf-8").replace(
            '"schema_version": 1', '"schema_version": 1, "future_option": true'
        )
        path.write_text(raw, encoding="utf-8")
        loaded = Settings.load(path)
        assert loaded.device_address == "AA:BB:CC:DD:EE:FF"


class TestValidation:
    def test_missing_device_rejected(self) -> None:
        with pytest.raises(ConfigError, match="stavau setup"):
            Settings().validate()

    @pytest.mark.parametrize("radius", [0.5, 0.0, 11.0, -3.0])
    def test_radius_out_of_range_rejected(self, radius: float) -> None:
        settings = Settings(device_address="AA:BB:CC:DD:EE:FF", radius_m=radius)
        with pytest.raises(ConfigError, match="radius"):
            settings.validate()

    def test_too_short_grace_rejected(self) -> None:
        settings = Settings(device_address="AA:BB:CC:DD:EE:FF", grace_seconds=1.0)
        with pytest.raises(ConfigError, match="grace"):
            settings.validate()

    def test_defaults_with_device_are_valid(self) -> None:
        Settings(device_address="AA:BB:CC:DD:EE:FF").validate()

    def test_auto_unlock_requires_acknowledgement(self) -> None:
        s = Settings(device_address="AA:BB:CC:DD:EE:FF", auto_unlock=True, association="paired")
        with pytest.raises(ConfigError, match="acknowledgement"):
            s.validate()

    def test_auto_unlock_requires_paired_device(self) -> None:
        s = Settings(
            device_address="AA:BB:CC:DD:EE:FF",
            auto_unlock=True,
            auto_unlock_ack=True,
            association="pairing-less",
        )
        with pytest.raises(ConfigError, match="paired"):
            s.validate()

    def test_auto_unlock_acknowledged_and_paired_is_valid(self) -> None:
        Settings(
            device_address="AA:BB:CC:DD:EE:FF",
            auto_unlock=True,
            auto_unlock_ack=True,
            association="paired",
        ).validate()

    # --- CARD-H0: config robustness (findings 3, 7) ---

    @pytest.mark.parametrize("exponent", [0.5, 1.19, 4.51, 10.0, -2.0])
    def test_implausible_path_loss_exponent_rejected_as_config_error(self, exponent: float) -> None:
        # A hand-edited/corrupt exponent must surface as ConfigError (handled by
        # main()), never as a raw ValueError from CalibrationModel deep in run().
        s = Settings(device_address="AA:BB:CC:DD:EE:FF", path_loss_exponent=exponent)
        with pytest.raises(ConfigError, match="path_loss_exponent"):
            s.validate()

    @pytest.mark.parametrize("rssi", [1.0, 50.0, -200.0])
    def test_out_of_range_rssi_at_1m_rejected(self, rssi: float) -> None:
        s = Settings(device_address="AA:BB:CC:DD:EE:FF", rssi_at_1m=rssi)
        with pytest.raises(ConfigError, match="rssi_at_1m"):
            s.validate()

    def test_auto_unlock_ratio_validated_even_when_disabled(self) -> None:
        # A stale bad value must be rejected regardless of the on/off flag.
        s = Settings(
            device_address="AA:BB:CC:DD:EE:FF",
            auto_unlock=False,
            auto_unlock_strict_ratio=-5.0,
        )
        with pytest.raises(ConfigError, match="strict_ratio"):
            s.validate()

    def test_auto_unlock_dwell_must_be_positive_even_when_disabled(self) -> None:
        s = Settings(
            device_address="AA:BB:CC:DD:EE:FF",
            auto_unlock=False,
            auto_unlock_dwell_seconds=0.0,
        )
        with pytest.raises(ConfigError, match="dwell"):
            s.validate()

    def test_negative_presence_max_age_rejected(self) -> None:
        s = Settings(
            device_address="AA:BB:CC:DD:EE:FF",
            integration_presence_max_age=-1.0,
        )
        with pytest.raises(ConfigError, match="presence_max_age"):
            s.validate()


class TestNumericCoercion:
    def test_integer_field_stored_as_float_is_coerced(self, tmp_path: Path) -> None:
        # A config.json with "smoothing_window": 8.0 must not crash deque(maxlen=...)
        # deep in the pipeline — load() coerces declared int fields.
        path = tmp_path / "config.json"
        Settings(device_address="AA:BB:CC:DD:EE:FF").save(path)
        raw = path.read_text(encoding="utf-8").replace(
            '"smoothing_window": 8', '"smoothing_window": 8.0'
        )
        path.write_text(raw, encoding="utf-8")
        loaded = Settings.load(path)
        assert loaded.smoothing_window == 8
        assert isinstance(loaded.smoothing_window, int)

    def test_port_stored_as_float_is_coerced(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        Settings(device_address="AA:BB:CC:DD:EE:FF").save(path)
        raw = path.read_text(encoding="utf-8").replace(
            '"integration_mqtt_port": 1883', '"integration_mqtt_port": 1883.0'
        )
        path.write_text(raw, encoding="utf-8")
        loaded = Settings.load(path)
        assert loaded.integration_mqtt_port == 1883
        assert isinstance(loaded.integration_mqtt_port, int)

    def test_uncoercible_numeric_field_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        Settings(device_address="AA:BB:CC:DD:EE:FF").save(path)
        raw = path.read_text(encoding="utf-8").replace(
            '"smoothing_window": 8', '"smoothing_window": "eight"'
        )
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(ConfigError, match="smoothing_window"):
            Settings.load(path)
