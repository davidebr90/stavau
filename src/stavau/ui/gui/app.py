"""PySide6 desktop shell for stavau (v0.3 GUI MVP).

A thin presentation layer: every business rule (distance model, presence
state machine, guardrail, calibration fit, device classification) lives in
`stavau.core.*` / `stavau.config.settings` and is only ever called into, never
reimplemented here. `viewmodel.py` holds every piece of formatting/validation
logic so it can be unit-tested without Qt; this module wires that logic to
widgets and worker threads.

Threading model mirrors `ui/tray.py`: `MonitorSession.run()` is async and is
driven on a background `QThread` via `asyncio.run`; the thread emits a Qt
signal per tick, and the UI updates only on the main thread in the slot.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stavau.config.settings import Settings, event_log_path
from stavau.core.deviceid import IMPLEMENTED_STRATEGIES, Strategy
from stavau.core.events import EventLog
from stavau.core.monitor import NearbyCache, sample_rssi, scan_devices
from stavau.core.session import MonitorSession, Tick
from stavau.platform.base import Locker, get_locker
from stavau.ui.gui import viewmodel as vm

_LOGO_CANDIDATES = ("stavau_light_transparent.png", "stavau_dark_transparent.png")


def _find_logo() -> Path | None:
    """Best-effort lookup of a repo-root logo/ file; optional, never required."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        logo_dir = parent / "logo"
        if logo_dir.is_dir():
            for name in _LOGO_CANDIDATES:
                candidate = logo_dir / name
                if candidate.is_file():
                    return candidate
    return None


# ---------------------------------------------------------------- scan worker


class _ScanWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            devices = asyncio.run(scan_devices(timeout=8.0))
        except Exception as exc:  # noqa: BLE001 - surface any backend failure to the UI
            self.failed.emit(str(exc))
            return
        self.finished.emit(devices)


# ---------------------------------------------------------------- calibration worker


class _SampleWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, address: str, seconds: float) -> None:
        super().__init__()
        self._address = address
        self._seconds = seconds

    def run(self) -> None:
        try:
            samples = asyncio.run(sample_rssi(self._address, self._seconds))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(samples)


# ---------------------------------------------------------------- monitor worker


class _MonitorWorker(QObject):
    tick = Signal(object)
    stopped = Signal(str)

    def __init__(self, settings: Settings, locker: Locker | None) -> None:
        super().__init__()
        self._settings = settings
        self._locker = locker
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        log = EventLog(event_log_path())
        session = MonitorSession(self._settings, self._locker, log, nearby=NearbyCache())
        try:
            asyncio.run(
                session.run(stop=self._stop_event.is_set, on_tick=lambda t: self.tick.emit(t))
            )
        except Exception as exc:  # noqa: BLE001 - surface, mirror tray.py's death handling
            self.stopped.emit(str(exc))
            return
        self.stopped.emit("")


# ---------------------------------------------------------------- calibration dialog


class CalibrationWizard(QDialog):
    """Two-step guided calibration: stand at 1 m, then 3 m, then fit + persist."""

    _STATIONS = (1.0, 3.0)
    _SAMPLE_SECONDS = 8.0

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibration wizard")
        self._settings = settings
        self._station_index = 0
        self._results: list[vm.CalibrationStationResult] = []
        self._outcome: vm.CalibrationOutcome | None = None
        self._thread: QThread | None = None
        self._worker: _SampleWorker | None = None

        layout = QVBoxLayout(self)
        self._instructions = QLabel()
        self._instructions.setWordWrap(True)
        layout.addWidget(self._instructions)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        self._sample_button = QPushButton("Sample")
        self._sample_button.clicked.connect(self._on_sample_clicked)
        buttons.addWidget(self._sample_button)
        layout.addLayout(buttons)

        self._box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._box.rejected.connect(self.reject)
        layout.addWidget(self._box)

        self._show_station_prompt()

    def _show_station_prompt(self) -> None:
        distance = self._STATIONS[self._station_index]
        self._instructions.setText(
            f"Step {self._station_index + 1} of {len(self._STATIONS)}: stand at {distance:g} m "
            "from this computer with the device where you normally carry it, then press Sample."
        )

    def _on_sample_clicked(self) -> None:
        self._sample_button.setEnabled(False)
        self._status.setText("Sampling...")
        self._thread = QThread(self)
        self._worker = _SampleWorker(self._settings.device_address, self._SAMPLE_SECONDS)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_samples)
        self._worker.failed.connect(self._on_sample_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_sample_failed(self, error: str) -> None:
        self._sample_button.setEnabled(True)
        self._status.setText(f"Sampling failed: {error}")

    def _on_samples(self, samples: list[float]) -> None:
        distance = self._STATIONS[self._station_index]
        result = vm.summarize_station(distance, samples)
        self._results.append(result)
        self._status.setText(result.message)
        self._sample_button.setEnabled(True)

        self._station_index += 1
        if self._station_index < len(self._STATIONS):
            self._show_station_prompt()
        else:
            self._finish()

    def _finish(self) -> None:
        outcome = vm.summarize_calibration_fit(self._results)
        self._outcome = outcome
        self._instructions.setText("Calibration complete.")
        self._status.setText(outcome.message)
        self._sample_button.setEnabled(False)
        if outcome.ok:
            self._box.setStandardButtons(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            ok_button = self._box.button(QDialogButtonBox.StandardButton.Ok)
            if ok_button is not None:
                ok_button.clicked.connect(self.accept)

    @property
    def outcome(self) -> vm.CalibrationOutcome | None:
        return self._outcome


# ---------------------------------------------------------------- main window


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("stavau")
        self._settings = settings if settings is not None else _load_or_default_settings()

        logo_path = _find_logo()
        if logo_path is not None:
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                self.setWindowIcon(QIcon(pixmap))

        self._scan_thread: QThread | None = None
        self._scan_worker: _ScanWorker | None = None
        self._monitor_thread: QThread | None = None
        self._monitor_worker: _MonitorWorker | None = None

        tabs = QTabWidget()
        tabs.addTab(self._build_device_tab(), "Device")
        tabs.addTab(self._build_settings_tab(), "Settings")
        tabs.addTab(self._build_monitor_tab(), "Monitor")
        tabs.addTab(self._build_calibration_tab(), "Calibration")
        self.setCentralWidget(tabs)
        self.resize(640, 480)

    # ------------------------------------------------------------- DEVICE tab

    def _build_device_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._device_label = QLabel()
        self._refresh_device_label()
        layout.addWidget(self._device_label)

        self._scan_button = QPushButton("Scan")
        self._scan_button.clicked.connect(self._on_scan_clicked)
        layout.addWidget(self._scan_button)

        self._scan_status = QLabel("")
        layout.addWidget(self._scan_status)

        self._scan_table = QTableWidget(0, 3)
        self._scan_table.setHorizontalHeaderLabels(["RSSI", "Address", "Name"])
        self._scan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._scan_table.itemSelectionChanged.connect(self._on_scan_row_selected)
        layout.addWidget(self._scan_table)

        self._scan_rows: list[vm.ScanRow] = []
        return widget

    def _refresh_device_label(self) -> None:
        s = self._settings
        self._device_label.setText(
            f"Trusted device: {s.device_alias or '(none)'}  ({s.device_address or 'not set'})"
        )

    def _on_scan_clicked(self) -> None:
        self._scan_button.setEnabled(False)
        self._scan_status.setText("Scanning...")
        self._scan_thread = QThread(self)
        self._scan_worker = _ScanWorker()
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.start()

    def _on_scan_failed(self, error: str) -> None:
        self._scan_button.setEnabled(True)
        self._scan_status.setText(f"Scan failed: {error}")

    def _on_scan_finished(self, devices: list[object]) -> None:
        self._scan_button.setEnabled(True)
        rows = vm.format_scan_rows(devices)  # type: ignore[arg-type]
        self._scan_rows = rows
        self._scan_status.setText(f"Found {len(rows)} device(s), strongest signal first.")
        self._scan_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._scan_table.setItem(i, 0, QTableWidgetItem(vm.format_rssi(row.rssi)))
            self._scan_table.setItem(i, 1, QTableWidgetItem(row.address))
            self._scan_table.setItem(i, 2, QTableWidgetItem(row.name))

    def _on_scan_row_selected(self) -> None:
        selected = self._scan_table.selectedIndexes()
        if not selected:
            return
        row_index = selected[0].row()
        if row_index >= len(self._scan_rows):
            return
        row = self._scan_rows[row_index]
        self._settings.device_address = row.address.upper()
        self._settings.device_alias = row.name if row.name != "<unnamed>" else row.address
        self._settings.save()
        self._refresh_device_label()

    # ------------------------------------------------------------ SETTINGS tab

    def _build_settings_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self._radius_slider = QSlider(Qt.Orientation.Horizontal)
        self._radius_slider.setRange(1, 10)
        self._radius_slider.setValue(int(round(self._settings.radius_m)))
        self._radius_value_label = QLabel(f"{self._settings.radius_m:g} m")
        self._radius_slider.valueChanged.connect(self._on_radius_changed)
        radius_row = QHBoxLayout()
        radius_row.addWidget(self._radius_slider)
        radius_row.addWidget(self._radius_value_label)
        form.addRow("Radius", radius_row)

        self._grace_spin = QDoubleSpinBox()
        self._grace_spin.setRange(3.0, 60.0)
        self._grace_spin.setValue(self._settings.grace_seconds)
        self._grace_spin.setSuffix(" s")
        form.addRow("Grace period", self._grace_spin)

        self._strategy_combo = QComboBox()
        strategy_values = [s.value for s in IMPLEMENTED_STRATEGIES] or [Strategy.ADV_SCAN.value]
        if self._settings.strategy not in strategy_values:
            strategy_values.append(self._settings.strategy)
        for value in sorted(set(strategy_values)):
            self._strategy_combo.addItem(value)
        index = self._strategy_combo.findText(self._settings.strategy)
        if index >= 0:
            self._strategy_combo.setCurrentIndex(index)
        self._strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        form.addRow("Strategy", self._strategy_combo)

        self._max_locks_spin = QSpinBox()
        self._max_locks_spin.setRange(1, 100)
        self._max_locks_spin.setValue(self._settings.breaker_max_locks)
        form.addRow("Guardrail: max locks", self._max_locks_spin)

        self._window_spin = QDoubleSpinBox()
        self._window_spin.setRange(1.0, 3600.0)
        self._window_spin.setValue(self._settings.breaker_window_seconds)
        self._window_spin.setSuffix(" s")
        form.addRow("Guardrail: window", self._window_spin)

        self._cooldown_spin = QDoubleSpinBox()
        self._cooldown_spin.setRange(1.0, 3600.0)
        self._cooldown_spin.setValue(self._settings.breaker_cooldown_seconds)
        self._cooldown_spin.setSuffix(" s")
        form.addRow("Guardrail: cooldown", self._cooldown_spin)

        layout.addLayout(form)

        self._caveat_label = QLabel()
        self._caveat_label.setWordWrap(True)
        self._caveat_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._caveat_label)
        self._refresh_caveat()

        self._settings_status = QLabel("")
        self._settings_status.setWordWrap(True)
        layout.addWidget(self._settings_status)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self._on_save_settings)
        layout.addWidget(save_button)
        layout.addStretch(1)
        return widget

    def _on_radius_changed(self, value: int) -> None:
        clamped = vm.clamp_radius(float(value))
        self._radius_value_label.setText(f"{clamped:g} m")

    def _on_strategy_changed(self, _text: str) -> None:
        self._refresh_caveat()

    def _refresh_caveat(self) -> None:
        text = vm.strategy_caveat(self._strategy_combo.currentText(), sys.platform)
        self._caveat_label.setText(text)
        self._caveat_label.setVisible(bool(text))

    def _on_save_settings(self) -> None:
        self._settings.radius_m = vm.clamp_radius(float(self._radius_slider.value()))
        self._settings.grace_seconds = vm.clamp_grace(self._grace_spin.value())
        self._settings.strategy = self._strategy_combo.currentText()
        self._settings.breaker_max_locks = self._max_locks_spin.value()
        self._settings.breaker_window_seconds = self._window_spin.value()
        self._settings.breaker_cooldown_seconds = self._cooldown_spin.value()

        result = vm.validate_settings_message(self._settings)
        if not result.ok:
            self._settings_status.setText(f"Not saved: {result.message}")
            return
        self._settings.save()
        self._settings_status.setText("Saved.")

    # ------------------------------------------------------------- MONITOR tab

    def _build_monitor_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._status_line = QLabel("stopped")
        layout.addWidget(self._status_line)

        buttons = QHBoxLayout()
        self._dry_run_button = QPushButton("Start (dry-run)")
        self._dry_run_button.clicked.connect(self._on_start_dry_run)
        buttons.addWidget(self._dry_run_button)

        self._armed_button = QPushButton("Start (armed)")
        self._armed_button.clicked.connect(self._on_start_armed)
        buttons.addWidget(self._armed_button)

        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._on_stop_monitor)
        buttons.addWidget(self._stop_button)
        layout.addLayout(buttons)

        log_group = QGroupBox("Event log (tail)")
        log_layout = QVBoxLayout(log_group)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        log_layout.addWidget(self._log_view)
        refresh_button = QPushButton("Refresh log")
        refresh_button.clicked.connect(self._refresh_log_view)
        log_layout.addWidget(refresh_button)
        layout.addWidget(log_group)

        self._refresh_log_view()
        return widget

    def _refresh_log_view(self) -> None:
        log = EventLog(event_log_path())
        records = log.tail(50)
        lines = [
            f"{r.timestamp}  {r.event}  " + "  ".join(f"{k}={v}" for k, v in r.detail.items())
            for r in records
        ]
        self._log_view.setPlainText("\n".join(lines) if lines else "(no events recorded yet)")

    def _on_start_dry_run(self) -> None:
        self._start_monitor(locker=None)

    def _on_start_armed(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Start armed monitoring",
            "This will actually lock the screen when the trusted device leaves the "
            "safety radius. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            locker = get_locker()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot start", f"No locker available: {exc}")
            return
        self._start_monitor(locker=locker)

    def _start_monitor(self, locker: Locker | None) -> None:
        result = vm.validate_settings_message(self._settings)
        if not result.ok:
            QMessageBox.warning(self, "Cannot start", result.message)
            return
        self._dry_run_button.setEnabled(False)
        self._armed_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._status_line.setText("starting...")

        self._monitor_thread = QThread(self)
        self._monitor_worker = _MonitorWorker(self._settings, locker)
        self._monitor_worker.moveToThread(self._monitor_thread)
        self._monitor_thread.started.connect(self._monitor_worker.run)
        self._monitor_worker.tick.connect(self._on_tick)
        self._monitor_worker.stopped.connect(self._on_monitor_stopped)
        self._monitor_thread.start()

    def _on_tick(self, tick: object) -> None:
        assert isinstance(tick, Tick)
        self._status_line.setText(vm.format_status(tick))

    def _on_monitor_stopped(self, error: str) -> None:
        self._dry_run_button.setEnabled(True)
        self._armed_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._status_line.setText(f"stopped: {error}" if error else "stopped")
        if self._monitor_thread is not None:
            self._monitor_thread.quit()
            self._monitor_thread.wait()
        self._monitor_thread = None
        self._monitor_worker = None
        self._refresh_log_view()

    def _on_stop_monitor(self) -> None:
        if self._monitor_worker is not None:
            self._monitor_worker.stop()

    # --------------------------------------------------------- CALIBRATION tab

    def _build_calibration_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(
            QLabel(
                "Calibrate distance estimation for the trusted device by sampling RSSI "
                "at two known distances."
            )
        )
        start_button = QPushButton("Start calibration wizard")
        start_button.clicked.connect(self._on_start_calibration)
        layout.addWidget(start_button)
        self._calibration_status = QLabel(
            f"Current: rssi_at_1m={self._settings.rssi_at_1m:g} dBm, "
            f"n={self._settings.path_loss_exponent:g}"
        )
        self._calibration_status.setWordWrap(True)
        layout.addWidget(self._calibration_status)
        layout.addStretch(1)
        return widget

    def _on_start_calibration(self) -> None:
        if not self._settings.device_address:
            QMessageBox.warning(self, "No device", "Pick a trusted device on the Device tab first.")
            return
        wizard = CalibrationWizard(self._settings, self)
        if wizard.exec() == QDialog.DialogCode.Accepted and wizard.outcome is not None:
            outcome = wizard.outcome
            if outcome.ok and outcome.rssi_at_1m is not None:
                self._settings.rssi_at_1m = outcome.rssi_at_1m
                self._settings.path_loss_exponent = outcome.path_loss_exponent or (
                    self._settings.path_loss_exponent
                )
                self._settings.save()
                self._calibration_status.setText(
                    f"Current: rssi_at_1m={self._settings.rssi_at_1m:g} dBm, "
                    f"n={self._settings.path_loss_exponent:g}"
                )


def _load_or_default_settings() -> Settings:
    try:
        settings = Settings.load()
    except Exception:  # noqa: BLE001 - first run / corrupt config: start from defaults
        settings = Settings()
    return settings


def run_gui(settings_or_none: Settings | None = None) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    window = MainWindow(settings_or_none)
    window.show()
    return app.exec()
