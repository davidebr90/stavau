"""Device intelligence: classify a BLE device and recommend a proximity strategy.

Pure, testable logic. Given what a device advertises (Bluetooth SIG company IDs
in its manufacturer data, service UUIDs, name, connectability), infer the kind
of device and the proximity strategy that will actually work for it — see
docs/device-compatibility.md for the research behind these choices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Bluetooth SIG assigned company identifiers (subset we care about).
APPLE_COMPANY_ID = 0x004C
MICROSOFT_COMPANY_ID = 0x0006
SAMSUNG_COMPANY_ID = 0x0075
GOOGLE_COMPANY_ID = 0x00E0
GARMIN_COMPANY_ID = 0x0087
FITBIT_COMPANY_ID = 0x00A0


class DeviceKind(Enum):
    APPLE = "apple"  # iPhone / iPad / Apple Watch — advertises Continuity constantly
    ANDROID = "android"  # Samsung / Google — idle phones often stop advertising BLE
    MICROSOFT = "microsoft"  # Windows device / Surface
    WEARABLE = "wearable"  # Garmin / Fitbit and similar fitness devices
    GENERIC = "generic"  # advertises, but no identifying vendor (beacon-like)
    UNKNOWN = "unknown"  # nothing observed yet


class Strategy(Enum):
    ADV_SCAN = "adv_scan"  # scan advertisements + RSSI (implemented, v0.1)
    GATT_LINK = "gatt_link"  # RSSI over a held GATT connection (planned)
    CLASSIC_LINK = "classic_link"  # bonded Bluetooth Classic link (planned)


# Strategies with a working runtime implementation. CLASSIC_LINK runs with real
# RSSI on Linux (hcitool) and reachability on Windows (WinRT); GATT_LINK is
# still planned.
IMPLEMENTED_STRATEGIES = frozenset({Strategy.ADV_SCAN, Strategy.CLASSIC_LINK})


@dataclass(frozen=True)
class Observation:
    """What a probe collected about a device over a short scanning window."""

    company_ids: frozenset[int] = frozenset()
    service_uuids: frozenset[str] = frozenset()
    name: str = ""
    advertisement_count: int = 0


@dataclass(frozen=True)
class Classification:
    kind: DeviceKind
    recommended: Strategy
    rationale: str
    warnings: list[str] = field(default_factory=list)

    @property
    def recommended_is_implemented(self) -> bool:
        return self.recommended in IMPLEMENTED_STRATEGIES

    @property
    def effective(self) -> Strategy:
        """The strategy stavau will actually run: the recommendation if it is
        implemented, otherwise the best available fallback (ADV_SCAN)."""
        return self.recommended if self.recommended_is_implemented else Strategy.ADV_SCAN


def classify(obs: Observation) -> Classification:
    ids = obs.company_ids

    if APPLE_COMPANY_ID in ids:
        return Classification(
            kind=DeviceKind.APPLE,
            recommended=Strategy.ADV_SCAN,
            rationale=(
                "Apple device (iPhone/iPad/Watch): broadcasts Continuity packets "
                "continuously, so advertisement scanning tracks it natively. "
                "Bond it in your OS Bluetooth settings for a stable identity across "
                "MAC rotations."
            ),
        )

    if ids & {SAMSUNG_COMPANY_ID, GOOGLE_COMPANY_ID}:
        return Classification(
            kind=DeviceKind.ANDROID,
            recommended=Strategy.CLASSIC_LINK,
            rationale=(
                "Android device (Samsung/Google): idle Android phones frequently "
                "stop advertising BLE, so the bonded Bluetooth Classic link "
                "(classic_link strategy) is the reliable channel."
            ),
            warnings=[
                "classic_link gives real RSSI on Linux (hcitool). On Windows it is "
                "reachability-only (in-range / out-of-range) and reflects active "
                "Classic connections, so keep the phone bonded and connected.",
            ],
        )

    if MICROSOFT_COMPANY_ID in ids:
        return Classification(
            kind=DeviceKind.MICROSOFT,
            recommended=Strategy.ADV_SCAN,
            rationale="Microsoft device: advertisement scanning is appropriate.",
        )

    if ids & {GARMIN_COMPANY_ID, FITBIT_COMPANY_ID}:
        return Classification(
            kind=DeviceKind.WEARABLE,
            recommended=Strategy.ADV_SCAN,
            rationale=(
                "Fitness wearable: usually advertises steadily; advertisement "
                "scanning works. Accuracy depends on how the device is worn."
            ),
        )

    if obs.advertisement_count > 0:
        return Classification(
            kind=DeviceKind.GENERIC,
            recommended=Strategy.ADV_SCAN,
            rationale=(
                "Unidentified but actively advertising device: advertisement "
                "scanning will track it as long as it keeps advertising."
            ),
        )

    return Classification(
        kind=DeviceKind.UNKNOWN,
        recommended=Strategy.ADV_SCAN,
        rationale="No advertisements observed during the probe.",
        warnings=[
            "The device was not seen advertising. If it is an idle Android phone "
            "this is expected — bond it and set the classic_link strategy. If it is "
            "powered off or out of range, bring it closer and re-run setup.",
        ],
    )
