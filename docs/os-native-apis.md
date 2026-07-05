# Native OS APIs & Protocols Reference

> Consolidated map of the **standard, officially-documented** OS interfaces
> stavau uses (or should use) for proximity, pairing, screen lock, and lock-state
> detection on Windows, Linux and macOS. Compiled from vendor documentation
> (July 2026). This is the ground truth the strategy engine builds on; keep it in
> sync as backends are added.

Legend: ✅ used today · 🔜 planned/recommended · ⚠️ limitation.

---

## 1. Windows (WinRT / Win32)

BLE and modern Bluetooth go through **WinRT** (`Windows.Devices.*`), reachable
from Python via the `winrt-*` projection packages (already pulled in by bleak).

| Concern | API / Protocol | Notes |
|---|---|---|
| BLE advertisement scan + RSSI | `Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher`; `BluetoothLEAdvertisementReceivedEventArgs.RawSignalStrengthInDBm` | ✅ via bleak. Real per-advertisement RSSI in dBm. |
| RSSI-threshold filtering (in/out ranges, sampling) | `BluetoothSignalStrengthFilter` (`InRangeThresholdInDBm`, `OutOfRangeThresholdInDBm`, `OutOfRangeTimeout`, `SamplingInterval`) | 🔜 OS-side hysteresis/debounce — could offload smoothing to the stack. |
| BLE device object / connection status | `Windows.Devices.Bluetooth.BluetoothLEDevice.FromBluetoothAddressAsync`, `.ConnectionStatus` | LE devices only. Cache must be populated by a prior scan. |
| Classic device object / connection status | `Windows.Devices.Bluetooth.BluetoothDevice.FromBluetoothAddressAsync`, `.ConnectionStatus` (`BluetoothConnectionStatus.Connected/Disconnected`) | ✅ **stavau classic_link backend.** Reachability only. |
| Classic connected-RSSI | — | ⚠️ **No public API.** Windows Dynamic Lock uses internal APIs. Confirmed limitation. |
| Device enumeration / watcher | `Windows.Devices.Enumeration.DeviceWatcher`, `DeviceInformation.GetDeviceSelectorFromPairingState` | 🔜 event-driven presence instead of polling. |
| Pairing / bonding | `DeviceInformation.Pairing.PairAsync` (WinRT) / bleak `BleakClient.pair()` | ✅ best-effort via bleak. |
| Radio (Bluetooth on/off) state | `Windows.Devices.Radios.Radio` (`RadioKind.Bluetooth`, `RadioState.On/Off`, `SetStateAsync`) | 🔜 detect "Bluetooth turned off at runtime" → fail-safe lock. |
| Screen lock (action) | Win32 `user32!LockWorkStation()` | ✅ stavau Windows locker. |
| Lock-state detection (feedback) | Win32 `WTSRegisterSessionNotification` + `WM_WTSSESSION_CHANGE`; .NET `SystemEvents.SessionSwitch` (`SessionLock`/`SessionUnlock`) | 🔜 close the loop: know if the screen is actually locked (avoid redundant locks, drive auto-unlock). |

---

## 2. Linux (BlueZ / systemd)

Everything Bluetooth is **BlueZ over D-Bus** (`org.bluez`). Session/lock is
**systemd-logind**.

| Concern | API / Protocol | Notes |
|---|---|---|
| BLE + Classic device object | `org.bluez.Device1` (D-Bus). Properties: `RSSI` (int16, readonly), `Connected`, `Paired`, `Bonded`, `Pathloss`, `TxPower`, `ServicesResolved` | `RSSI` is advertisement/inquiry RSSI; `PropertiesChanged` signals stream updates. |
| Discovery | `org.bluez.Adapter1.StartDiscovery` + `SetDiscoveryFilter` (`Transport`=le/bredr/auto, `RSSI`, `Pathloss`, `DuplicateData`) | ✅ via bleak (adv scan). |
| **Power-efficient background monitoring** | `org.bluez.AdvertisementMonitor1` / `AdvertisementMonitorManager1` — RSSI thresholds (`HighRSSIThreshold`, `LowRSSIThreshold`) + timers (`HighRSSIThresholdTimer`, `LowRSSIThresholdTimer`), pattern filters | 🔜 **Major.** Controller-offloaded in/out-of-range detection; fires even without an active discovery session; low power. Ideal for the daemon + low-energy devices. |
| Connected-classic RSSI | HCI `Read RSSI` via `hcitool rssi <MAC>` (needs an active link; `l2ping` warms it) | ✅ **stavau classic_link Linux backend.** Real RSSI relative to golden range. |
| Link quality | HCI `Read Link Quality` (`hcitool lq`) | 🔜 alternative/complementary signal. |
| Pairing / bonding | `org.bluez.Device1.Pair`; agent via `org.bluez.AgentManager1` + `org.bluez.Agent1` | 🔜 bond → kernel resolves RPAs to identity address (stable tracking). |
| RPA→identity resolution | Kernel resolving list fed with bonded IRKs (mgmt API); resolved identity appears as `Device1.Address` | ✅ implicit benefit of bonding on Linux. |
| Screen lock (action) | `loginctl lock-session` (systemd-logind D-Bus `org.freedesktop.login1`); fallbacks `xdg-screensaver lock`, `org.freedesktop.ScreenSaver.Lock` | ✅ stavau Linux locker (fallback chain). |
| Lock-state detection (feedback) | logind `LockedHint` property + `Lock`/`Unlock` signals on `org.freedesktop.login1.Session` | 🔜 close the feedback loop. |
| Radio state | `org.bluez.Adapter1.Powered`; rfkill | 🔜 detect adapter off at runtime. |

---

## 3. macOS (CoreBluetooth / IOBluetooth)

BLE = **CoreBluetooth**; Classic = **IOBluetooth**. No stavau lock backend yet
(v0.2 target).

| Concern | API / Protocol | Notes |
|---|---|---|
| BLE scan + RSSI | `CoreBluetooth.CBCentralManager.scanForPeripherals`; delegate `centralManager(_:didDiscover:advertisementData:rssi:)` | ✅ via bleak. Real RSSI per advertisement. |
| Connected BLE RSSI | `CBPeripheral.readRSSI()` → `peripheral(_:didReadRSSI:error:)` | 🔜 the one platform where connected-RSSI is public; enables GATT_LINK cleanly. bleak exposes `get_rssi()` on Darwin only. |
| ⚠️ Address privacy | CoreBluetooth hides MAC; devices are opaque `CBPeripheral` UUIDs per-host | Tracking a fixed address like Windows/Linux isn't possible; must persist the CoreBluetooth UUID. |
| Classic RSSI / device | `IOBluetooth.IOBluetoothDevice` (`rawRSSI`, `RSSI`, `isConnected`) | 🔜 classic_link macOS backend. |
| Pairing | `IOBluetoothDevice` pairing / system pairing UI | 🔜. |
| Screen lock (action) | `CGSession -suspend` (login CoreServices); `pmset displaysleepnow` + "require password immediately" | 🔜 macOS locker. |
| Lock-state detection (feedback) | `NSDistributedNotificationCenter`: `com.apple.screenIsLocked` / `com.apple.screenIsUnlocked` (also `com.apple.screensaver.*`) | 🔜 clean, documented lock-state feedback. |
| Radio state | `CBCentralManager.state` (`.poweredOn/.poweredOff/.unauthorized`) | 🔜 detect BT off + permission state. |

---

## 4. Cross-cutting conclusions

1. **BLE advertisement RSSI is universal and real** on all three OSes (bleak
   already gives it). It is the backbone of `adv_scan`.
2. **Connected-classic RSSI is the fragmented axis:** real on Linux (`hcitool`),
   real on macOS (`readRSSI`/`rawRSSI`), **absent on Windows** (reachability
   only). The strategy engine must keep degrading honestly per-OS.
3. **BlueZ `AdvertisementMonitor1` is the single biggest efficiency win** for the
   daemon: hardware-offloaded, low-power, event-driven in/out-of-range — the
   right long-term primitive for Linux and for low/ultra-low-energy devices.
4. **Windows `BluetoothSignalStrengthFilter`** offers OS-side in/out thresholds
   and sampling — a lighter alternative to our software smoothing on Windows.
5. **Lock-state feedback exists and is documented on every OS** (WTS session
   notifications / logind `LockedHint` / `com.apple.screenIsLocked`). Wiring it
   in turns stavau from open-loop (fire lock and hope) into closed-loop
   (know the real state) — a prerequisite for safe auto-unlock.

## Sources

- [Windows — BluetoothLEAdvertisementReceivedEventArgs.RawSignalStrengthInDBm](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.advertisement.bluetoothleadvertisementreceivedeventargs.rawsignalstrengthindbm)
- [Windows — BluetoothSignalStrengthFilter](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothsignalstrengthfilter)
- [Windows — Windows.Devices.Radios.RadioState](https://learn.microsoft.com/en-us/uwp/api/windows.devices.radios.radiostate)
- [Windows — SystemEvents.SessionSwitch (lock/unlock detection)](https://learn.microsoft.com/en-us/dotnet/api/microsoft.win32.systemevents.sessionswitch)
- [BlueZ — Device API (org.bluez.Device1, RSSI)](https://bluez.readthedocs.io/en/latest/device-api/)
- [BlueZ — Advertisement Monitor API (power-efficient RSSI monitoring)](https://bluez.readthedocs.io/en/latest/advertisement-monitor-api/)
- [BlueZ — org.bluez.AdvertisementMonitor(5)](https://manpages.ubuntu.com/manpages/noble/man5/org.bluez.AdvertisementMonitor.5.html)
- [Apple — CBCentralManager (CoreBluetooth)](https://developer.apple.com/documentation/corebluetooth/cbcentralmanager)
- [Apple — Core Bluetooth](https://developer.apple.com/documentation/corebluetooth)
- [macOS — screen lock notifications & CGSession (Apple Developer Forums)](https://developer.apple.com/forums/thread/682918)
