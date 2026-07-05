from stavau.core.deviceid import (
    APPLE_COMPANY_ID,
    FITBIT_COMPANY_ID,
    GOOGLE_COMPANY_ID,
    MICROSOFT_COMPANY_ID,
    SAMSUNG_COMPANY_ID,
    Classification,
    DeviceKind,
    Observation,
    Strategy,
    classify,
)


def obs(*company_ids: int, count: int = 5, name: str = "") -> Observation:
    return Observation(company_ids=frozenset(company_ids), name=name, advertisement_count=count)


class TestClassification:
    def test_apple_device_recommends_adv_scan(self) -> None:
        c = classify(obs(APPLE_COMPANY_ID))
        assert c.kind is DeviceKind.APPLE
        assert c.recommended is Strategy.ADV_SCAN
        assert c.recommended_is_implemented
        assert not c.warnings

    def test_samsung_is_android_and_recommends_classic_link(self) -> None:
        c = classify(obs(SAMSUNG_COMPANY_ID))
        assert c.kind is DeviceKind.ANDROID
        assert c.recommended is Strategy.CLASSIC_LINK
        # classic_link is now implemented; it is the effective strategy.
        assert c.recommended_is_implemented
        assert c.effective is Strategy.CLASSIC_LINK
        assert c.warnings  # still warns about platform caveats (Windows reachability-only)

    def test_google_is_android(self) -> None:
        assert classify(obs(GOOGLE_COMPANY_ID)).kind is DeviceKind.ANDROID

    def test_microsoft_device(self) -> None:
        c = classify(obs(MICROSOFT_COMPANY_ID))
        assert c.kind is DeviceKind.MICROSOFT
        assert c.effective is Strategy.ADV_SCAN

    def test_fitbit_is_wearable(self) -> None:
        assert classify(obs(FITBIT_COMPANY_ID)).kind is DeviceKind.WEARABLE

    def test_apple_takes_priority_over_other_ids(self) -> None:
        # A packet carrying both Apple and Microsoft IDs is an Apple device.
        assert classify(obs(APPLE_COMPANY_ID, MICROSOFT_COMPANY_ID)).kind is DeviceKind.APPLE

    def test_unknown_vendor_but_advertising_is_generic(self) -> None:
        c = classify(obs(0x0999, count=3))
        assert c.kind is DeviceKind.GENERIC
        assert c.effective is Strategy.ADV_SCAN

    def test_no_advertisements_is_unknown_with_warning(self) -> None:
        c = classify(Observation())
        assert c.kind is DeviceKind.UNKNOWN
        assert c.warnings
        # Even unknown devices fall back to a runnable strategy.
        assert c.effective is Strategy.ADV_SCAN


class TestClassificationInvariants:
    def test_effective_strategy_is_always_implemented(self) -> None:
        from stavau.core.deviceid import IMPLEMENTED_STRATEGIES

        for company in [
            APPLE_COMPANY_ID,
            SAMSUNG_COMPANY_ID,
            GOOGLE_COMPANY_ID,
            MICROSOFT_COMPANY_ID,
            FITBIT_COMPANY_ID,
            0x1234,
        ]:
            c: Classification = classify(obs(company))
            assert c.effective in IMPLEMENTED_STRATEGIES
