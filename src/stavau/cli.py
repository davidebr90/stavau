"""stavau command-line interface (v0.1)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from stavau import __version__
from stavau.config.settings import ConfigError, Settings, event_log_path
from stavau.core.calibrate import fit_model, median_rssi
from stavau.core.distance import CalibrationModel
from stavau.core.events import EventLog
from stavau.core.monitor import sample_rssi, scan_devices
from stavau.core.presence import PresenceState
from stavau.core.session import MonitorSession, Tick
from stavau.platform.base import Locker, UnsupportedPlatformError, get_locker

_CALIBRATION_STATIONS = (1.0, 3.0)
_STATION_SAMPLE_SECONDS = 8.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stavau",
        description="Privacy by proximity: auto-lock your workstation when you walk away.",
    )
    parser.add_argument("--version", action="version", version=f"stavau {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", help="guided pairing and RSSI calibration wizard")
    p_setup.add_argument("--address", help="skip the scan and use this device address")
    p_setup.add_argument("--alias", help="friendly name stored in config and logs")
    p_setup.add_argument(
        "--skip-calibration",
        action="store_true",
        help="use generic defaults instead of the guided calibration (less accurate)",
    )
    p_setup.add_argument("--scan-timeout", type=float, default=10.0, help="discovery scan seconds")
    p_setup.add_argument(
        "--radius", type=float, default=None, help="safety radius in metres (1-10, default 3)"
    )
    p_setup.add_argument(
        "--pair",
        action="store_true",
        help="attempt BLE bonding (pairing) for a stable identity; otherwise "
        "associate pairing-less via advertisement scanning",
    )
    p_setup.add_argument(
        "--strategy",
        choices=["auto", "adv_scan", "classic_link", "adv_monitor", "gatt_link"],
        default="auto",
        help="proximity strategy: 'auto' detects it from the device (default); "
        "'classic_link' for an idle Android that does not advertise; "
        "'adv_monitor' offloads presence to the controller (Linux/BlueZ, low power); "
        "'gatt_link' polls RSSI over a held BLE connection (macOS/Linux)",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_pair = sub.add_parser("pair", help="bond the configured device (or --address) via BLE")
    p_pair.add_argument("--address", help="device to pair (default: the configured one)")
    p_pair.set_defaults(func=cmd_pair)

    p_run = sub.add_parser("run", help="start proximity monitoring")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="log lock decisions without actually locking the screen",
    )
    p_run.add_argument(
        "--duration", type=float, default=None, help="stop after N seconds (for testing)"
    )
    p_run.set_defaults(func=cmd_run)

    p_tray = sub.add_parser("tray", help="run the monitor with a system-tray status icon")
    p_tray.add_argument(
        "--dry-run",
        action="store_true",
        help="log lock decisions without actually locking the screen",
    )
    p_tray.set_defaults(func=cmd_tray)

    p_gui = sub.add_parser("gui", help="open the graphical interface (requires stavau[gui])")
    p_gui.set_defaults(func=cmd_gui)

    p_status = sub.add_parser("status", help="show connection state, RSSI and estimated distance")
    p_status.add_argument("--timeout", type=float, default=8.0, help="listen seconds")
    p_status.set_defaults(func=cmd_status)

    p_log = sub.add_parser("log", help="show recent lock/unlock events")
    p_log.add_argument("--count", type=int, default=20, help="number of events to show")
    p_log.add_argument("--clear", action="store_true", help="delete the local event log")
    p_log.add_argument("--export", action="store_true", help="print raw JSONL records")
    p_log.set_defaults(func=cmd_log)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    try:
        exit_code: int = args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        exit_code = 130
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        exit_code = 2
    except UnsupportedPlatformError as exc:
        print(f"platform error: {exc}", file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)


# ---------------------------------------------------------------- setup


def cmd_setup(args: argparse.Namespace) -> int:
    address: str | None = args.address
    alias: str = args.alias or ""

    if address is None:
        print(f"Scanning for BLE devices ({args.scan_timeout:g} s)...")
        devices = asyncio.run(scan_devices(timeout=args.scan_timeout))
        if not devices:
            print("No BLE devices found. Is Bluetooth on?", file=sys.stderr)
            return 1
        print(f"\n  #  {'RSSI':>6}  {'address':<17}  name")
        for index, device in enumerate(devices[:20], start=1):
            print(f" {index:>2}  {device.rssi:>4} dBm  {device.address:<17}  {device.name}")
        print(
            "\nTip: unnamed entries with rotating addresses are usually phones with MAC\n"
            "randomization. Bond your phone in the OS Bluetooth settings first, then\n"
            "pick it here (on Linux, bonded devices show their stable identity address)."
        )
        choice = input("\nDevice number: ").strip()
        try:
            selected = devices[int(choice) - 1]
        except (ValueError, IndexError):
            print(f"invalid choice: {choice!r}", file=sys.stderr)
            return 1
        address = selected.address
        alias = alias or selected.name

    settings = Settings(device_address=address.upper(), device_alias=alias or address.upper())
    if args.radius is not None:
        settings.radius_m = args.radius

    _identify_and_associate(settings, pair=args.pair, forced_strategy=args.strategy)

    if args.skip_calibration:
        print(
            "Calibration skipped: using generic defaults "
            f"(rssi_at_1m={settings.rssi_at_1m:g} dBm, n={settings.path_loss_exponent:g}). "
            "Re-run 'stavau setup' with calibration for accurate distances."
        )
    else:
        model = _run_calibration(settings.device_address)
        settings.rssi_at_1m = model.rssi_at_1m
        settings.path_loss_exponent = model.path_loss_exponent

    settings.validate()
    path = settings.save()
    print(f"\nSaved configuration to {path}")
    print(f"Trusted device: {settings.device_alias} ({settings.device_address})")
    print(f"Kind: {settings.device_kind}  strategy: {settings.strategy}  ({settings.association})")
    print(f"Safety radius: {settings.radius_m:g} m, grace time: {settings.grace_seconds:g} s")
    print(
        f"Guardrail: pause locking after {settings.breaker_max_locks} locks within "
        f"{settings.breaker_window_seconds:g} s"
    )
    print("Start monitoring with: stavau run")
    return 0


def _identify_and_associate(settings: Settings, *, pair: bool, forced_strategy: str) -> None:
    """Probe the device, classify it, pick a strategy, and associate it.

    Association is pairing-less by default (advertisement scanning). With
    ``pair=True`` we attempt BLE bonding for a stable identity; on failure we
    fall back to pairing-less and tell the user to bond via the OS dialog.
    ``forced_strategy`` other than "auto" overrides the detected strategy — the
    escape hatch for an idle Android that will not advertise during the probe.
    """
    from stavau.core.deviceid import classify
    from stavau.core.monitor import probe_device

    print("\nIdentifying device (5 s probe)...")
    observation = asyncio.run(probe_device(settings.device_address, 5.0))
    classification = classify(observation)
    settings.device_kind = classification.kind.value

    print(f"  Detected: {classification.rationale}")
    for warning in classification.warnings:
        print(f"  ! {warning}")

    if forced_strategy != "auto":
        settings.strategy = forced_strategy
        print(f"  Strategy forced to '{forced_strategy}' (overriding auto-detection).")
    else:
        settings.strategy = classification.effective.value
        if not classification.recommended_is_implemented:
            print(
                f"  Recommended strategy '{classification.recommended.value}' is not yet "
                f"implemented; using '{classification.effective.value}'."
            )

    if pair:
        settings.association = "paired" if _try_pair(settings.device_address) else "pairing-less"
    else:
        settings.association = "pairing-less"
        print(
            "  Association: pairing-less (advertisement scanning). Run 'stavau setup "
            "--pair' or bond in your OS Bluetooth settings for a stable identity."
        )


def _try_pair(address: str) -> bool:
    from bleak.exc import BleakError

    from stavau.core.monitor import pair_device

    print(f"  Pairing with {address}... (confirm any OS dialog)")
    try:
        asyncio.run(pair_device(address))
    except (BleakError, OSError, asyncio.TimeoutError) as exc:
        print(
            f"  ! Pairing failed ({exc}). Falling back to pairing-less. You can bond "
            "the device from your OS Bluetooth settings instead.",
            file=sys.stderr,
        )
        return False
    print("  Paired successfully (bonded).")
    return True


def cmd_pair(args: argparse.Namespace) -> int:
    if args.address:
        address = args.address.upper()
        settings: Settings | None = None
    else:
        settings = Settings.load()
        settings.validate()
        address = settings.device_address
    paired = _try_pair(address)
    if settings is not None:
        settings.association = "paired" if paired else "pairing-less"
        settings.save()
    return 0 if paired else 1


def _run_calibration(address: str) -> CalibrationModel:
    print(
        "\nCalibration: keep the device where you normally carry it (pocket counts!)\n"
        "and stand at each requested distance."
    )
    stations: list[tuple[float, float]] = []
    for distance in _CALIBRATION_STATIONS:
        input(f"\nStand at {distance:g} m from this computer, then press Enter... ")
        print(f"  sampling for {_STATION_SAMPLE_SECONDS:g} s...")
        samples = asyncio.run(sample_rssi(address, _STATION_SAMPLE_SECONDS))
        if len(samples) < 3:
            print(
                f"  only {len(samples)} advertisements received - device not advertising "
                "or out of range; this station is skipped."
            )
            continue
        rssi = median_rssi(samples)
        print(f"  median RSSI at {distance:g} m: {rssi:.0f} dBm ({len(samples)} samples)")
        stations.append((distance, rssi))

    if not stations:
        raise ConfigError("calibration failed: no usable samples - re-run 'stavau setup'")
    try:
        model = fit_model(stations)
    except ValueError as exc:
        # Implausible fit (movement, reflections): fall back to the 1 m reference only.
        print(f"  fit rejected ({exc}); using the first station with the default exponent")
        model = fit_model(stations[:1])
    print(
        f"\nCalibrated: rssi_at_1m={model.rssi_at_1m:.1f} dBm, "
        f"path loss exponent n={model.path_loss_exponent:.2f}"
    )
    return model


# ---------------------------------------------------------------- run


def cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.load()
    settings.validate()
    locker: Locker | None = None if args.dry_run else get_locker()
    session = MonitorSession(settings, locker, EventLog(event_log_path()))

    mode = "dry-run" if locker is None else f"armed ({locker.name})"
    print(
        f"stavau {__version__} monitoring '{settings.device_alias}' [{mode}] "
        f"radius={settings.radius_m:g} m grace={settings.grace_seconds:g} s "
        f"guardrail={settings.breaker_max_locks} locks/"
        f"{settings.breaker_window_seconds:g}s - Ctrl+C to stop",
        flush=True,
    )
    printer = _ConsolePrinter(dry_run=locker is None)
    try:
        asyncio.run(session.run(duration=args.duration, on_tick=printer))
    except Exception as exc:  # noqa: BLE001 - refuse loudly with an actionable reason (I1)
        # Startup failure = clean refusal to start. Mid-run death while ARMED
        # must fail safe: lock before exiting (I1), since protection is ending.
        if locker is not None and printer.saw_ticks:
            with contextlib.suppress(Exception):
                locker.lock()
                print("monitor died mid-run: screen locked as a precaution", flush=True)
        hint = ""
        with contextlib.suppress(Exception):
            from stavau.core.radiostate import radio_available

            if asyncio.run(radio_available()) is False:
                hint = " (the Bluetooth radio is OFF - turn it on and retry)"
        print(f"monitor aborted: {exc}{hint}", file=sys.stderr, flush=True)
        return 3
    return 0


class _ConsolePrinter:
    """Prints per-tick status and announces lock / guardrail transitions."""

    def __init__(self, dry_run: bool) -> None:
        self._dry_run = dry_run
        self._prev_state: PresenceState | None = None
        self._was_paused = False
        self.saw_ticks = False

    def __call__(self, tick: Tick) -> None:
        self.saw_ticks = True
        # A fresh transition into AWAY is when the lock fired this tick.
        if tick.state is PresenceState.AWAY and self._prev_state is not PresenceState.AWAY:
            if tick.breaker_paused:
                print(
                    f">>> guardrail active: lock SUPPRESSED, resuming in "
                    f"{tick.breaker_seconds_remaining:.0f} s",
                    flush=True,
                )
            elif self._dry_run:
                print(">>> LOCK (dry-run: screen not actually locked)", flush=True)
            else:
                print(">>> screen locked", flush=True)
        if tick.breaker_paused and not self._was_paused:
            print(
                f"!!! GUARDRAIL TRIPPED: too many locks too fast - locking paused for "
                f"{tick.breaker_seconds_remaining:.0f} s so you can operate",
                file=sys.stderr,
                flush=True,
            )
        self._was_paused = tick.breaker_paused
        self._prev_state = tick.state

        if tick.rssi is not None:
            rssi_text = f"{tick.rssi:6.1f} dBm"
        elif tick.radio_off:
            rssi_text = "BLUETOOTH OFF"
        else:
            rssi_text = " no signal"
        distance_text = f"{tick.distance:5.2f} m" if tick.distance is not None else "    ? m"
        pause_text = " [PAUSED]" if tick.breaker_paused else ""
        # flush: status must reach logs/pipes in real time, not on buffer boundaries
        print(
            f"[{tick.elapsed:5.0f}s] rssi={rssi_text}  dist={distance_text}  "
            f"state={tick.state.value}{pause_text}",
            flush=True,
        )


# ---------------------------------------------------------------- tray


def cmd_tray(args: argparse.Namespace) -> int:
    settings = Settings.load()
    settings.validate()
    try:
        from stavau.ui.tray import run_tray
    except ImportError:
        print(
            "tray dependencies missing - install them with: pip install 'stavau[tray]'",
            file=sys.stderr,
        )
        return 2
    locker: Locker | None = None if args.dry_run else get_locker()
    return run_tray(settings, locker)


# ---------------------------------------------------------------- gui


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from stavau.ui.gui.app import run_gui
    except ImportError:
        print(
            "gui dependencies missing - install them with: pip install 'stavau[gui]'",
            file=sys.stderr,
        )
        return 2
    try:
        settings: Settings | None = Settings.load()
    except ConfigError:
        settings = None  # first run: the GUI guides device selection
    return run_gui(settings)


# ---------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    settings = Settings.load()
    settings.validate()
    model = CalibrationModel(
        rssi_at_1m=settings.rssi_at_1m, path_loss_exponent=settings.path_loss_exponent
    )
    print(
        f"device: {settings.device_alias} ({settings.device_address})  "
        f"radius: {settings.radius_m:g} m - listening {args.timeout:g} s..."
    )
    print(
        f"kind: {settings.device_kind}  strategy: {settings.strategy}  "
        f"association: {settings.association}"
    )
    samples = asyncio.run(sample_rssi(settings.device_address, args.timeout))
    if not samples:
        print(
            "device not seen: out of range, Bluetooth off, or its address rotated\n"
            "(bond the device in your OS Bluetooth settings for a stable identity)"
        )
        return 1
    rssi = median_rssi(samples)
    distance = model.distance_m(rssi)
    inside = "inside" if distance <= settings.radius_m else "OUTSIDE"
    print(
        f"rssi: {rssi:.0f} dBm ({len(samples)} advertisements)  "
        f"estimated distance: {distance:.2f} m - {inside} the safety radius"
    )
    return 0


# ---------------------------------------------------------------- log


def cmd_log(args: argparse.Namespace) -> int:
    log = EventLog(event_log_path())
    if args.clear:
        log.clear()
        print("event log cleared")
        return 0
    records = log.tail(args.count)
    if not records:
        print("no events recorded yet")
        return 0
    if args.export:
        import json

        for record in records:
            print(
                json.dumps(
                    {
                        "timestamp": record.timestamp,
                        "event": record.event,
                        "detail": record.detail,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    for record in records:
        detail = "  ".join(f"{key}={value}" for key, value in record.detail.items())
        print(f"{record.timestamp}  {record.event:<16} {detail}")
    return 0
