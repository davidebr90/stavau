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

The header shows the "stavau" wordmark as styled text only (no logo image -
CARD-E2 request 1). The window/taskbar icon and an owned system tray icon are
both regenerated from the pure `viewmodel.icon_color` decision on every tick
(CARD-E2 request 2); the small padlock drawing is duplicated from
`ui/tray.py` rather than imported, so pystray/Pillow never become a GUI
dependency. All user-visible strings go through `stavau.i18n.tr()` (CARD-E2
request 3).
"""

from __future__ import annotations

import asyncio
import sys
import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stavau import __version__
from stavau.config.settings import Settings, event_log_path
from stavau.core.deviceid import IMPLEMENTED_STRATEGIES, Strategy
from stavau.core.events import EventLog
from stavau.core.monitor import NearbyCache, sample_rssi, scan_devices
from stavau.core.session import MonitorSession, Tick
from stavau.i18n import available_languages, resolve_language, set_language, tr
from stavau.platform.base import Locker, get_locker
from stavau.ui.gui import viewmodel as vm
from stavau.ui.gui.theme import DARK, LIGHT, build_stylesheet

# ---------------------------------------------------------------- state icon


def _padlock_pixmap(color: tuple[int, int, int], paused: bool = False, size: int = 64) -> QPixmap:
    """Draw a padlock silhouette filled with the state colour.

    Deliberately duplicated (not imported) from `ui/tray.py::_padlock_image`:
    that module pulls in pystray/Pillow, which must stay a `[tray]`-only
    dependency and never leak into the `[gui]` extra. The two drawings render
    the same silhouette/keyhole/pause-bar convention using each toolkit's
    native primitives (Pillow there, QPainter here).
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    scale = size / 64.0
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(*color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        shackle_pen = painter.pen()
        shackle_pen.setWidthF(7 * scale)
        shackle_pen.setColor(fill)
        painter.setPen(shackle_pen)
        painter.drawArc(
            int(18 * scale), int(8 * scale), int(28 * scale), int(28 * scale), 0, 180 * 16
        )
        painter.drawLine(int(21 * scale), int(22 * scale), int(21 * scale), int(34 * scale))
        painter.drawLine(int(43 * scale), int(22 * scale), int(43 * scale), int(34 * scale))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(
            int(12 * scale), int(30 * scale), int(40 * scale), int(28 * scale), 9 * scale, 9 * scale
        )
        inner = QColor(255, 255, 255, 235)
        painter.setBrush(inner)
        if paused:
            painter.drawRect(int(26 * scale), int(38 * scale), int(4 * scale), int(14 * scale))
            painter.drawRect(int(34 * scale), int(38 * scale), int(4 * scale), int(14 * scale))
        else:
            painter.drawEllipse(int(27 * scale), int(37 * scale), int(10 * scale), int(10 * scale))
            painter.drawRect(int(30 * scale), int(44 * scale), int(4 * scale), int(8 * scale))
    finally:
        painter.end()
    return pixmap


def _icon_for_tick(tick_or_none: Tick | None, radius_m: float, has_device: bool) -> QIcon:
    """Render the pure `viewmodel.icon_color` decision into a Qt icon."""
    decision = vm.icon_color(tick_or_none, radius_m, has_device)
    if decision == "paused":
        return QIcon(_padlock_pixmap(vm.ICON_PAUSED, paused=True))
    return QIcon(_padlock_pixmap(decision))


_ADDRESS_ROLE = int(Qt.ItemDataRole.UserRole)
_CHOSEN_BRUSH = QBrush(QColor(53, 169, 74, 60))  # translucent brand green


class _SortItem(QTableWidgetItem):
    """Table cell that sorts by a supplied key (numeric where it matters), so
    clicking a column header orders it correctly instead of lexicographically."""

    def __init__(self, text: str, sort_key: float | str) -> None:
        super().__init__(text)
        self._sort_key = sort_key

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _SortItem):
            try:
                return bool(self._sort_key < other._sort_key)  # type: ignore[operator]
            except TypeError:
                pass
        return super().__lt__(other)


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
        self.setWindowTitle(tr("calibration.wizard_title"))
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
        self._sample_button = QPushButton(tr("calibration.sample_button"))
        self._sample_button.setObjectName("Primary")
        self._sample_button.setCursor(Qt.CursorShape.PointingHandCursor)
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
            tr(
                "calibration.wizard_step",
                step=self._station_index + 1,
                total=len(self._STATIONS),
                distance=distance,
            )
        )

    def _on_sample_clicked(self) -> None:
        self._sample_button.setEnabled(False)
        self._status.setText(tr("calibration.sampling"))
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
        self._status.setText(tr("calibration.sampling_failed", error=error))

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
        self._instructions.setText(tr("calibration.complete_title"))
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
        self._settings = settings if settings is not None else _load_or_default_settings()
        set_language(resolve_language(getattr(self._settings, "language", "auto")))

        self.setWindowTitle(tr("app.header_title"))

        self._scan_thread: QThread | None = None
        self._scan_worker: _ScanWorker | None = None
        self._monitor_thread: QThread | None = None
        self._monitor_worker: _MonitorWorker | None = None

        # Modern layout: a left navigation rail + a stacked content area,
        # replacing the old tab bar. Pages are the same widgets as before.
        self._stack = QStackedWidget()
        self._stack.addWidget(self._wrap_page(tr("tab.device"), self._build_device_tab()))
        self._stack.addWidget(self._wrap_page(tr("tab.settings"), self._build_settings_tab()))
        self._stack.addWidget(self._wrap_page(tr("tab.monitor"), self._build_monitor_tab()))
        self._stack.addWidget(self._wrap_page(tr("tab.calibration"), self._build_calibration_tab()))

        sidebar = self._build_sidebar(
            [tr("tab.device"), tr("tab.settings"), tr("tab.monitor"), tr("tab.calibration")]
        )

        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self._stack)

        root = QWidget()
        root.setObjectName("Root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.resize(860, 600)

        # System tray icon: owned by the window, mirrors the taskbar icon,
        # regenerated from the same pure icon_color() decision, with a context
        # menu (right-click) to drive the app without the window in front.
        self._tray_icon = QSystemTrayIcon(self)
        self._tray_menu = QMenu()
        self._tray_show_action = QAction(tr("tray.show_window"), self)
        self._tray_show_action.triggered.connect(self._show_and_raise)
        self._tray_menu.addAction(self._tray_show_action)
        self._tray_start_action = QAction(tr("tray.start_monitor"), self)
        self._tray_start_action.triggered.connect(self._on_start_dry_run)
        self._tray_menu.addAction(self._tray_start_action)
        self._tray_stop_action = QAction(tr("tray.stop_monitor"), self)
        self._tray_stop_action.triggered.connect(self._on_stop_monitor)
        self._tray_stop_action.setEnabled(False)
        self._tray_menu.addAction(self._tray_stop_action)
        self._tray_menu.addSeparator()
        quit_action = QAction(tr("tray.quit"), self)
        quit_action.triggered.connect(QApplication.quit)
        self._tray_menu.addAction(quit_action)
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.setToolTip(f"stavau {__version__} — {tr('monitor.stopped')}")
        self._refresh_state_icon(None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon.show()

    def _show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_and_raise()

    def _refresh_state_icon(self, tick: Tick | None) -> None:
        """Regenerate the taskbar (window) icon and the tray icon together."""
        icon = _icon_for_tick(tick, self._settings.radius_m, bool(self._settings.device_address))
        self.setWindowIcon(icon)
        self._tray_icon.setIcon(icon)
        # Mirror the same coloured padlock in the monitor hero, if it exists.
        if hasattr(self, "_hero_icon"):
            decision = vm.icon_color(
                tick, self._settings.radius_m, bool(self._settings.device_address)
            )
            if decision == "paused":
                self._hero_icon.setPixmap(_padlock_pixmap(vm.ICON_PAUSED, paused=True, size=44))
            else:
                self._hero_icon.setPixmap(_padlock_pixmap(decision, size=44))

    # ------------------------------------------------------------- chrome

    def _build_sidebar(self, labels: list[str]) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(184)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(4)

        wordmark = QLabel("stavau")
        wordmark.setObjectName("Wordmark")
        layout.addWidget(wordmark)
        tagline = QLabel(f"v{__version__}")
        tagline.setObjectName("WordmarkTag")
        layout.addWidget(tagline)
        layout.addSpacing(10)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, i=index: self._stack.setCurrentIndex(i))
            self._nav_group.addButton(button, index)
            layout.addWidget(button)
        first = self._nav_group.button(0)
        if first is not None:
            first.setChecked(True)

        layout.addStretch(1)
        return sidebar

    def _wrap_page(self, title: str, inner: QWidget) -> QWidget:
        """Give a page a titled, padded content area inside a card."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 22, 24, 24)
        outer.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        outer.addWidget(heading)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.addWidget(inner)
        outer.addWidget(card, 1)
        return page

    # ------------------------------------------------------------- DEVICE tab

    def _build_device_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        self._device_label = QLabel()
        self._device_label.setWordWrap(True)
        self._refresh_device_label()
        layout.addWidget(self._device_label)

        top_row = QHBoxLayout()
        self._scan_button = QPushButton(tr("device.scan_button"))
        self._scan_button.setObjectName("Primary")
        self._scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_button.clicked.connect(self._on_scan_clicked)
        top_row.addWidget(self._scan_button)
        self._scan_status = QLabel("")
        self._scan_status.setObjectName("Muted")
        top_row.addWidget(self._scan_status, 1)
        layout.addLayout(top_row)

        # Indeterminate progress bar as a scan spinner (hidden when idle).
        self._scan_spinner = QProgressBar()
        self._scan_spinner.setRange(0, 0)  # busy/indeterminate animation
        self._scan_spinner.setTextVisible(False)
        self._scan_spinner.setVisible(False)
        layout.addWidget(self._scan_spinner)

        hint = QLabel(tr("device.select_hint"))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Type | Name | Est. distance | Signal | Address — the type + distance
        # columns are what actually tell you which nearby device is your phone.
        self._scan_table = QTableWidget(0, 5)
        self._scan_table.setHorizontalHeaderLabels(
            [
                tr("device.table_type"),
                tr("device.table_name"),
                tr("device.table_distance"),
                tr("device.table_signal"),
                tr("device.table_address"),
            ]
        )
        self._scan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._scan_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._scan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._scan_table.setSortingEnabled(True)  # click a header to sort asc/desc
        self._scan_table.verticalHeader().setVisible(False)
        header = self._scan_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._scan_table.itemSelectionChanged.connect(self._on_scan_row_selected)
        self._scan_table.itemDoubleClicked.connect(lambda _item: self._use_selected_device())
        layout.addWidget(self._scan_table, 1)  # stretch: fill the card

        bottom_row = QHBoxLayout()
        self._selected_note = QLabel("")
        self._selected_note.setObjectName("Muted")
        self._selected_note.setWordWrap(True)
        bottom_row.addWidget(self._selected_note, 1)
        self._use_button = QPushButton(tr("device.use_button"))
        self._use_button.setObjectName("Primary")
        self._use_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_button.setEnabled(False)
        self._use_button.clicked.connect(self._use_selected_device)
        bottom_row.addWidget(self._use_button)
        layout.addLayout(bottom_row)

        self._scan_rows: list[vm.ScanRow] = []
        # The currently-trusted address, highlighted in the table when present.
        self._chosen_address = (self._settings.device_address or "").upper()
        return widget

    def _refresh_device_label(self) -> None:
        s = self._settings
        self._device_label.setText(
            tr(
                "device.trusted_label",
                alias=s.device_alias or tr("device.alias_none"),
                address=s.device_address or tr("device.address_not_set"),
            )
        )
        self._refresh_state_icon_if_ready()

    def _refresh_state_icon_if_ready(self) -> None:
        # Guarded: called from _refresh_device_label(), which also runs once
        # during __init__ before self._tray_icon exists (initial label paint).
        # Only meaningful when idle - a running monitor gets its icon refresh
        # from _on_tick() instead.
        if hasattr(self, "_tray_icon") and self._monitor_thread is None:
            self._refresh_state_icon(None)

    def _on_scan_clicked(self) -> None:
        self._scan_button.setEnabled(False)
        self._scan_spinner.setVisible(True)
        self._scan_status.setText(tr("device.scanning"))
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
        self._scan_spinner.setVisible(False)
        self._scan_status.setText(tr("device.scan_failed", error=error))

    def _on_scan_finished(self, devices: list[object]) -> None:
        self._scan_button.setEnabled(True)
        self._scan_spinner.setVisible(False)
        rows = vm.format_scan_rows(
            devices,  # type: ignore[arg-type]
            self._settings.rssi_at_1m,
            self._settings.path_loss_exponent,
        )
        self._scan_rows = rows
        self._scan_status.setText(tr("device.scan_found", count=len(rows)))
        # Repopulate with sorting momentarily off (so rows don't reshuffle
        # mid-insert), storing each row's address on the cells so selection and
        # highlighting stay correct after the user sorts by any column.
        self._scan_table.setSortingEnabled(False)
        self._scan_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            kind = _SortItem(row.kind_label, row.kind_label.lower())
            kind.setData(_ADDRESS_ROLE, row.address.upper())
            self._scan_table.setItem(i, 0, kind)
            self._scan_table.setItem(i, 1, _SortItem(row.name, row.name.lower()))
            far = float("inf") if row.distance_m is None else row.distance_m
            self._scan_table.setItem(i, 2, _SortItem(vm.format_distance(row.distance_m), far))
            self._scan_table.setItem(i, 3, _SortItem(vm.format_rssi(row.rssi), row.rssi))
            self._scan_table.setItem(i, 4, _SortItem(row.address, row.address))
        self._scan_table.setSortingEnabled(True)
        self._highlight_chosen_row()

    def _row_address(self, view_row: int) -> str | None:
        item = self._scan_table.item(view_row, 0)
        if item is None:
            return None
        value = item.data(_ADDRESS_ROLE)
        return str(value) if value else None

    def _selected_scan_row(self) -> vm.ScanRow | None:
        selected = self._scan_table.selectionModel().selectedRows()
        if not selected:
            return None
        address = self._row_address(selected[0].row())
        if address is None:
            return None
        for row in self._scan_rows:
            if row.address.upper() == address:
                return row
        return None

    def _highlight_chosen_row(self) -> None:
        """Paint the trusted-device row a persistent colour (survives sorting)."""
        for view_row in range(self._scan_table.rowCount()):
            is_chosen = self._row_address(view_row) == self._chosen_address
            for col in range(self._scan_table.columnCount()):
                cell = self._scan_table.item(view_row, col)
                if cell is not None:
                    cell.setBackground(_CHOSEN_BRUSH if is_chosen else QBrush())

    def _on_scan_row_selected(self) -> None:
        # Selecting a row only *highlights* it and previews the choice; the
        # trusted device is committed explicitly by the "Use this device"
        # button (or a double-click), so nothing changes by accident.
        row = self._selected_scan_row()
        self._use_button.setEnabled(row is not None)
        if row is not None:
            self._selected_note.setObjectName("Muted")
            self._selected_note.setStyleSheet("")  # clear any success styling
            self._selected_note.setText(
                tr("device.selected_note", name=row.name, address=row.address)
            )

    def _use_selected_device(self) -> None:
        row = self._selected_scan_row()
        if row is None:
            return
        self._settings.device_address = row.address.upper()
        self._settings.device_alias = row.name if row.name != "<unnamed>" else row.address
        self._settings.save()
        self._chosen_address = row.address.upper()
        self._highlight_chosen_row()
        self._refresh_device_label()
        # Explicit, unmistakable success confirmation (green).
        self._selected_note.setObjectName("Success")
        alias = row.name if row.name != "<unnamed>" else row.address
        self._selected_note.setText(tr("device.chosen_confirm", name=alias, address=row.address))
        # Re-polish so the #Success stylesheet rule takes effect immediately.
        style = self._selected_note.style()
        if style is not None:
            style.unpolish(self._selected_note)
            style.polish(self._selected_note)

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
        form.addRow(tr("settings.radius_label"), radius_row)

        self._grace_spin = QDoubleSpinBox()
        self._grace_spin.setRange(3.0, 60.0)
        self._grace_spin.setValue(self._settings.grace_seconds)
        self._grace_spin.setSuffix(" s")
        form.addRow(tr("settings.grace_label"), self._grace_spin)

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
        form.addRow(tr("settings.strategy_label"), self._strategy_combo)

        self._max_locks_spin = QSpinBox()
        self._max_locks_spin.setRange(1, 100)
        self._max_locks_spin.setValue(self._settings.breaker_max_locks)
        form.addRow(tr("settings.guardrail_max_locks_label"), self._max_locks_spin)

        self._window_spin = QDoubleSpinBox()
        self._window_spin.setRange(1.0, 3600.0)
        self._window_spin.setValue(self._settings.breaker_window_seconds)
        self._window_spin.setSuffix(" s")
        form.addRow(tr("settings.guardrail_window_label"), self._window_spin)

        self._cooldown_spin = QDoubleSpinBox()
        self._cooldown_spin.setRange(1.0, 3600.0)
        self._cooldown_spin.setValue(self._settings.breaker_cooldown_seconds)
        self._cooldown_spin.setSuffix(" s")
        form.addRow(tr("settings.guardrail_cooldown_label"), self._cooldown_spin)

        self._language_combo = QComboBox()
        current_language = getattr(self._settings, "language", "auto")
        self._language_codes = ["auto", *available_languages()]
        for code in self._language_codes:
            label = tr("settings.language_auto") if code == "auto" else code
            self._language_combo.addItem(label, userData=code)
        index = self._language_combo.findData(current_language)
        if index < 0:
            index = 0
        self._language_combo.setCurrentIndex(index)
        form.addRow(tr("settings.language_label"), self._language_combo)

        layout.addLayout(form)

        self._caveat_label = QLabel()
        self._caveat_label.setWordWrap(True)
        self._caveat_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._caveat_label)
        self._refresh_caveat()

        self._settings_status = QLabel("")
        self._settings_status.setWordWrap(True)
        layout.addWidget(self._settings_status)

        save_button = QPushButton(tr("settings.save_button"))
        save_button.setObjectName("Primary")
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_button.clicked.connect(self._on_save_settings)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(save_button)
        layout.addLayout(save_row)
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
        new_language = self._language_combo.currentData()
        language_changed = new_language != getattr(self._settings, "language", "auto")
        # settings.py is a READ-ONLY hotspot; until the `language` field lands
        self._settings.language = new_language

        result = vm.validate_settings_message(self._settings)
        if not result.ok:
            self._settings_status.setText(tr("settings.not_saved", message=result.message))
            return
        self._settings.save()
        if language_changed:
            set_language(resolve_language(new_language))
            self._settings_status.setText(tr("settings.saved_language_note"))
        else:
            self._settings_status.setText(tr("settings.saved"))
        if self._monitor_thread is None:
            self._refresh_state_icon(None)

    # ------------------------------------------------------------- MONITOR tab

    def _build_monitor_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # Status hero: a large coloured padlock next to the live status text.
        hero = QHBoxLayout()
        hero.setSpacing(14)
        self._hero_icon = QLabel()
        self._hero_icon.setPixmap(_padlock_pixmap(vm.ICON_GREY, size=44))
        hero.addWidget(self._hero_icon)
        self._status_line = QLabel(tr("monitor.stopped"))
        self._status_line.setObjectName("StatusHero")
        self._status_line.setWordWrap(True)
        hero.addWidget(self._status_line, 1)
        layout.addLayout(hero)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._dry_run_button = QPushButton(tr("monitor.start_dry_run_button"))
        self._dry_run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dry_run_button.clicked.connect(self._on_start_dry_run)
        buttons.addWidget(self._dry_run_button)

        self._armed_button = QPushButton(tr("monitor.start_armed_button"))
        self._armed_button.setObjectName("Primary")
        self._armed_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._armed_button.clicked.connect(self._on_start_armed)
        buttons.addWidget(self._armed_button)

        self._stop_button = QPushButton(tr("monitor.stop_button"))
        self._stop_button.setObjectName("Danger")
        self._stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._on_stop_monitor)
        buttons.addWidget(self._stop_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        log_title = QLabel(tr("monitor.log_group"))
        log_title.setObjectName("Muted")
        layout.addWidget(log_title)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        layout.addWidget(self._log_view, 1)
        refresh_button = QPushButton(tr("monitor.refresh_log_button"))
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.clicked.connect(self._refresh_log_view)
        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        refresh_row.addWidget(refresh_button)
        layout.addLayout(refresh_row)

        self._refresh_log_view()
        return widget

    def _refresh_log_view(self) -> None:
        log = EventLog(event_log_path())
        records = log.tail(50)
        lines = [
            f"{r.timestamp}  {r.event}  " + "  ".join(f"{k}={v}" for k, v in r.detail.items())
            for r in records
        ]
        self._log_view.setPlainText("\n".join(lines) if lines else tr("monitor.log_empty"))

    def _on_start_dry_run(self) -> None:
        self._start_monitor(locker=None)

    def _on_start_armed(self) -> None:
        confirm = QMessageBox.question(
            self,
            tr("monitor.armed_confirm_title"),
            tr("monitor.armed_confirm_text"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            locker = get_locker()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self,
                tr("monitor.cannot_start_title"),
                tr("monitor.cannot_start_no_locker", error=exc),
            )
            return
        self._start_monitor(locker=locker)

    def _start_monitor(self, locker: Locker | None) -> None:
        result = vm.validate_settings_message(self._settings)
        if not result.ok:
            QMessageBox.warning(self, tr("monitor.cannot_start_title"), result.message)
            return
        self._dry_run_button.setEnabled(False)
        self._armed_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._tray_start_action.setEnabled(False)
        self._tray_stop_action.setEnabled(True)
        self._status_line.setText(tr("monitor.starting"))

        self._monitor_thread = QThread(self)
        self._monitor_worker = _MonitorWorker(self._settings, locker)
        self._monitor_worker.moveToThread(self._monitor_thread)
        self._monitor_thread.started.connect(self._monitor_worker.run)
        self._monitor_worker.tick.connect(self._on_tick)
        self._monitor_worker.stopped.connect(self._on_monitor_stopped)
        self._monitor_thread.start()

    def _on_tick(self, tick: object) -> None:
        assert isinstance(tick, Tick)
        status = vm.format_status(tick)
        self._status_line.setText(status)
        self._refresh_state_icon(tick)
        self._tray_icon.setToolTip(f"stavau {__version__} — {status}")

    def _on_monitor_stopped(self, error: str) -> None:
        self._dry_run_button.setEnabled(True)
        self._armed_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._tray_start_action.setEnabled(True)
        self._tray_stop_action.setEnabled(False)
        stopped_text = (
            tr("monitor.stopped_with_error", error=error) if error else tr("monitor.stopped")
        )
        self._status_line.setText(stopped_text)
        self._tray_icon.setToolTip(f"stavau {__version__} — {stopped_text}")
        if self._monitor_thread is not None:
            self._monitor_thread.quit()
            self._monitor_thread.wait()
        self._monitor_thread = None
        self._monitor_worker = None
        self._refresh_log_view()
        self._refresh_state_icon(None)

    def _on_stop_monitor(self) -> None:
        if self._monitor_worker is not None:
            self._monitor_worker.stop()

    # --------------------------------------------------------- CALIBRATION tab

    def _build_calibration_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        intro = QLabel(tr("calibration.tab_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        start_button = QPushButton(tr("calibration.start_button"))
        start_button.setObjectName("Primary")
        start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        start_button.clicked.connect(self._on_start_calibration)
        start_row = QHBoxLayout()
        start_row.addWidget(start_button)
        start_row.addStretch(1)
        layout.addLayout(start_row)
        self._calibration_status = QLabel(self._calibration_status_text())
        self._calibration_status.setWordWrap(True)
        layout.addWidget(self._calibration_status)
        layout.addStretch(1)
        return widget

    def _calibration_status_text(self) -> str:
        return tr(
            "calibration.current_status",
            rssi=self._settings.rssi_at_1m,
            exponent=self._settings.path_loss_exponent,
        )

    def _on_start_calibration(self) -> None:
        if not self._settings.device_address:
            QMessageBox.warning(
                self, tr("calibration.no_device_title"), tr("calibration.no_device_text")
            )
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
                self._calibration_status.setText(self._calibration_status_text())


def _load_or_default_settings() -> Settings:
    try:
        settings = Settings.load()
    except Exception:  # noqa: BLE001 - first run / corrupt config: start from defaults
        settings = Settings()
    return settings


def _apply_theme(app: QApplication) -> None:
    """Apply the stavau stylesheet, following the OS dark/light preference."""
    dark = False
    try:
        from PySide6.QtCore import Qt as _Qt

        dark = app.styleHints().colorScheme() == _Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - older Qt without colorScheme(): default to light
        dark = False
    app.setStyleSheet(build_stylesheet(DARK if dark else LIGHT))


def run_gui(settings_or_none: Settings | None = None) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    if isinstance(app, QApplication):
        _apply_theme(app)
    window = MainWindow(settings_or_none)
    window.show()
    return app.exec()
