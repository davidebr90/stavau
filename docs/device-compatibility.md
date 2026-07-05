# Device Compatibility & Presence-Detection Strategies

> Research summary (July 2026) answering: *do we need companion apps on the phone/watch, or can stavau rely on native OS Bluetooth pairing?* Short answer: **native pairing is enough for phones — but no single detection strategy works for every device, so stavau needs a small strategy engine that picks the right one per device.**

## 1. How pairing works (and what stavau gets out of it)

Bluetooth LE pairing is initiated at OS level — no app is required on either side. The PC (central) connects and starts SMP pairing; the phone shows the native system dialog (Just Works, 6-digit passkey or numeric comparison depending on I/O capabilities). **Bonding** then stores two keys that matter to us:

- **LTK** (Long-Term Key) — lets the devices re-encrypt future connections without re-pairing.
- **IRK** (Identity Resolving Key) — lets the PC **resolve the phone's rotating MAC addresses** (RPA, rotated every ~15–45 min on iOS/Android) back to its stable identity address. On Linux, BlueZ feeds bonded IRKs to the kernel's resolving list and thereafter reports the *identity* address in scan results and on the D-Bus Device object.

So the answer to "can we use the native system pairing request?" is **yes** — `bluetoothctl pair` / Windows Settings / the stavau wizard triggering the OS dialog. The IRK is exactly why our setup wizard requires bonding first.

## 2. What each device family actually does over the air

| Device | Advertises BLE when idle? | LE-connectable from a PC? | Classic BT reachable when bonded? | Verdict |
|---|---|---|---|---|
| **iPhone / iPad** | ✅ Continuously (Apple Continuity "Nearby Info", mfr ID `0x004C`, every few seconds) | ✅ Exposes a GATT server (ANCS/AMS ecosystem); pairing can be initiated from the PC | ✅ | **Best case.** Works natively: bond once → resolved advertisements (Linux) or GATT link. No app needed. |
| **Apple Watch** | ✅ Continuously (Nearby Info, like iPhone) | ⚠️ Pairs only with iPhones; PC bonding not supported | ❌ | Detectable via Continuity traffic + RSSI, but **without a bond there is no IRK** → identity across MAC rotations needs fingerprinting (as done by ESPHome/Home Assistant presence projects). Best-effort tier. |
| **Android phone** | ❌ **Not reliably.** Idle Androids don't advertise BLE; app-based advertising is killed in background since Android 12 | ❌ Not when idle (no advertising → not connectable) | ✅ **Yes — page scan stays on for bonded devices** | **Classic Bluetooth is the reliable channel.** Same approach as Microsoft's Dynamic Lock and Linux BlueProximity: poll RSSI/presence on the bonded classic link. |
| **Wear OS / Galaxy Watch** | ⚠️ Varies by model/firmware | ⚠️ Generally locked to the paired phone | ⚠️ Some models | Treat like Apple Watch: opportunistic. A companion app is the only robust path (future work). |

Key prior art validating the classic-Bluetooth strategy: **Windows Dynamic Lock** polls the RSSI of the bonded phone's connection and locks when it drops (`rssiMaxDelta` default −10 dB, lock ~30 s after out-of-range); **BlueProximity** has done the same on Linux via `hcitool rssi` since 2007.

## 3. Why a companion app does NOT solve iOS (and isn't needed)

An iOS companion app advertising a service UUID sounds attractive but **does not work**: when an iOS app is backgrounded, CoreBluetooth moves its service UUIDs into the Apple-proprietary "overflow area" of the advertisement, which is **only discoverable by other iOS devices** scanning for that exact UUID. A Linux/Windows PC never sees it. The viable iOS companion design would be the reverse (app in `bluetooth-central` background mode connecting *out* to a GATT server hosted by the PC) — worth considering someday for hardened setups, but unnecessary: iPhones already advertise Continuity packets constantly without any app.

For **Android**, a companion app (foreground service advertising or connecting to the PC) would work but fights Doze/battery optimizations; the bonded-classic-link strategy needs zero code on the phone. Companion apps remain a **v2.x option**, not a requirement.

## 4. Platform capabilities on the PC side

| Capability | Linux (BlueZ ≥ 5.55) | Windows 10+ | macOS |
|---|---|---|---|
| Scan advertisements + RSSI | ✅ bleak/D-Bus | ✅ bleak/WinRT | ✅ bleak/CoreBluetooth |
| RPA→identity resolution of bonded devices during scan | ✅ kernel resolving list | ⚠️ stack resolves for connections; watcher behavior to validate on hardware | ⚠️ CoreBluetooth hides addresses (UUIDs per device) |
| RSSI of an **open LE/GATT connection** | ✅ HCI `Read RSSI` (`hcitool rssi`, works for LE and classic handles) | ❌ **No public API** (advertisement RSSI only) | ✅ `readRSSI` (exposed by bleak on Darwin only) |
| RSSI of a bonded **classic** link | ✅ `hcitool rssi` | ❌ no public API → presence-only polling (connect attempt succeeded/failed) | ⚠️ IOBluetooth `rawRSSI` |

## 5a. Implementation status (v0.2)

**Landed:** device intelligence (`core/deviceid.py`) classifies the trusted
device from advertised Bluetooth SIG company IDs (Apple `0x004C`, Samsung
`0x0075`, Google `0x00E0`, Microsoft `0x0006`, Garmin/Fitbit) and recommends a
strategy. `stavau setup` probes the device for 5 s, records `device_kind` /
`strategy` / `association` in config, and `stavau status` reports them.
Association is pairing-less by default (advertisement scanning); `stavau setup
--pair` / `stavau pair` attempt BLE bonding (best-effort via bleak) and fall
back to pairing-less with guidance.

**Verified live:** a Samsung device is correctly classified as Android-kind, the
engine recommends `classic_link`, honestly reports it is not yet implemented,
and falls back to `adv_scan` with a warning.

**Not yet implemented:** the `GATT_LINK` and `CLASSIC_LINK` runtime strategies
(only `ADV_SCAN` runs today) and per-strategy RSSI acquisition. The
classification already routes toward them and records the recommendation so the
switch is a localized change.

## 5b. Resulting design: the strategy engine (v0.2)

`ProximitySource` becomes pluggable, with auto-selection per device ("device intelligence"):

```
Strategy A — ADV_SCAN      advertisement scanning, bonded-identity filter
                           (v0.1 behaviour; primary for Apple devices on Linux)
Strategy B — GATT_LINK     keep a GATT connection, poll link RSSI
                           (Linux via HCI Read RSSI; macOS via readRSSI)
Strategy C — CLASSIC_LINK  bonded classic link: RSSI on Linux/macOS,
                           reachability polling on Windows (degraded: binary
                           near/away with a longer grace time, like Dynamic Lock)
```

**Device intelligence at setup:** the wizard probes the chosen device and records the evidence in config:

1. Manufacturer data `0x004C` → Apple → expect continuous advertising → prefer A (Linux) / B.
2. No BLE advertisements from a bonded phone within a probe window → Android-like → prefer C.
3. Connectable advertisement + successful GATT connect → B available.
4. Probe results + chosen strategy stored in `config.json`; `stavau status` reports which strategy is active and why.
5. Runtime fallback: if the active strategy yields no samples for N windows, try the next one before declaring AWAY — the fail-safe ordering never changes (no data ⇒ lock).

**Accuracy note:** strategy C on Windows gives reachability, not distance — the radius slider degrades to "in range / out of range". The GUI must say this honestly for the affected combination (Android + Windows) instead of showing a fake distance.

## 6. Sources

- [Microsoft Learn — Dynamic Lock](https://learn.microsoft.com/en-us/windows/security/identity-protection/hello-for-business/hello-feature-dynamic-lock) (bonded-phone RSSI polling, `rssiMaxDelta`)
- [Novel Bits — Bluetooth Addresses & Privacy (RPA/IRK)](https://novelbits.io/bluetooth-address-privacy-ble/)
- [BlueZ mgmt-api — Load Identity Resolving Keys / kernel resolving list](https://github.com/bluez/bluez/wiki/MGMT)
- [org.bluez.Device D-Bus API — resolved identity Address after pairing](https://manpages.ubuntu.com/manpages/noble/man5/org.bluez.Device.5.html)
- [Apple — Core Bluetooth Background Processing (overflow area)](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html)
- [David G. Young — Hacking the Overflow Area](https://davidgyoungtech.com/2020/05/07/hacking-the-overflow-area)
- [Handoff All Your Privacy — Apple Continuity protocol analysis (arXiv:1904.10600)](https://arxiv.org/pdf/1904.10600)
- [ESPHome Apple Watch presence detection (Nearby Info fingerprinting)](https://github.com/dalehumby/ESPHome-Apple-Watch-detection)
- [DEV — Android 12+ background BLE advertising restrictions](https://dev.to/ble_advertiser/why-your-android-ble-advertisements-silently-fail-in-the-background-on-android-12-and-how-to-fix-it-n0d)
- [Punch Through — Android BLE guide](https://punchthrough.com/android-ble-guide/)
- [bleak issue #1131 — RSSI after connection (Darwin-only)](https://github.com/hbldh/bleak/issues/1131)
- [32feet issue #310 — Windows has no API for open-connection RSSI](https://github.com/inthehand/32feet/issues/310)
- [BlueProximity (Linux classic-BT proximity lock prior art)](https://github.com/rschrenk/blueproximity)
- [daniloaz — lock/unlock by Bluetooth proximity with hcitool rssi](https://www.daniloaz.com/en/blog/automatically-lock-unlock-your-screen-by-bluetooth-device-proximity)
