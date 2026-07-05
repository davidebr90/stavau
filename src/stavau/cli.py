"""stavau command-line interface (v0.1)."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from stavau import __version__
from stavau.config.settings import ConfigError, Settings, event_log_path
from stavau.core.calibrate import fit_model, median_rssi
from stavau.core.distance import CalibrationModel
from stavau.core.events import EventLog
from stavau.core.monitor import BleProximitySource, RssiTracker, sample_rssi, scan_devices
from stavau.core.presence import PresenceConfig, PresenceMachine, PresenceState
from stavau.platform.base import Locker, LockError, UnsupportedPlatformError, get_locker

_CALIBRATION_STATIONS = (1.0, 3.0)
_STATION_SAMPLE_SECONDS = 8.0
_LOCK_RETRY_SECONDS = 5.0


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
    p_setup.set_defaults(func=cmd_setup)

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
    print(f"Safety radius: {settings.radius_m:g} m, grace time: {settings.grace_seconds:g} s")
    print("Start monitoring with: stavau run")
    return 0


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
    return asyncio.run(_run_loop(settings, locker, duration=args.duration))


async def _run_loop(settings: Settings, locker: Locker | None, duration: float | None) -> int:
    model = CalibrationModel(
        rssi_at_1m=settings.rssi_at_1m, path_loss_exponent=settings.path_loss_exponent
    )
    machine = PresenceMachine(
        PresenceConfig(
            radius_m=settings.radius_m,
            grace_seconds=settings.grace_seconds,
            return_seconds=settings.return_seconds,
        )
    )
    tracker = RssiTracker(smoothing_window=settings.smoothing_window)
    source = BleProximitySource(settings.device_address, tracker)
    log = EventLog(event_log_path())

    mode = "dry-run" if locker is None else f"armed ({locker.name})"
    print(
        f"stavau {__version__} monitoring '{settings.device_alias}' [{mode}] "
        f"radius={settings.radius_m:g} m grace={settings.grace_seconds:g} s - Ctrl+C to stop",
        flush=True,
    )
    log.append("monitor_started", device=settings.device_alias, dry_run=locker is None)

    await source.start()
    started = time.monotonic()
    last_state = machine.state
    lock_pending_since: float | None = None
    try:
        while True:
            now = time.monotonic()
            rssi = tracker.smoothed(now)
            distance = model.distance_m(rssi) if rssi is not None else None
            must_lock = machine.update(distance, now)

            if machine.state is not last_state:
                log.append(
                    "state_changed",
                    state=machine.state.value,
                    distance=None if distance is None else round(distance, 2),
                )
                last_state = machine.state
            if machine.state is PresenceState.NEAR:
                lock_pending_since = None

            retry_due = (
                lock_pending_since is not None and now - lock_pending_since >= _LOCK_RETRY_SECONDS
            )
            if must_lock or retry_due:
                locked = _trigger_lock(locker, log)
                # Fail-safe: while we are AWAY and the lock keeps failing,
                # keep retrying instead of silently giving up.
                lock_pending_since = None if locked else now

            _print_status(now - started, rssi, distance, machine.state)
            if duration is not None and now - started >= duration:
                return 0
            await asyncio.sleep(1.0)
    finally:
        await source.stop()
        log.append("monitor_stopped")


def _print_status(
    elapsed: float, rssi: float | None, distance: float | None, state: PresenceState
) -> None:
    rssi_text = f"{rssi:6.1f} dBm" if rssi is not None else " no signal"
    distance_text = f"{distance:5.2f} m" if distance is not None else "    ? m"
    # flush: status must reach logs/pipes in real time, not on buffer boundaries
    print(
        f"[{elapsed:5.0f}s] rssi={rssi_text}  dist={distance_text}  state={state.value}",
        flush=True,
    )


def _trigger_lock(locker: Locker | None, log: EventLog) -> bool:
    if locker is None:
        print(">>> LOCK (dry-run: screen not actually locked)", flush=True)
        log.append("lock_triggered", dry_run=True)
        return True
    try:
        locker.lock()
    except LockError as exc:
        # Loud failure: a silent one would leave the user believing they're protected.
        log.append("lock_failed", error=str(exc))
        print(f"!!! LOCK FAILED, retrying in {_LOCK_RETRY_SECONDS:g} s: {exc}", file=sys.stderr)
        return False
    log.append("lock_triggered", dry_run=False)
    print(">>> screen locked", flush=True)
    return True


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
