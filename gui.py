#!/usr/bin/env python3
"""Native 1920x1080 Holocron dashboard for a Cage display client."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from PySide6.QtCore import QEvent, QTimer, Qt, QRectF, QPointF, Signal
    from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPolygonF
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
        QColorDialog, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
        QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
        QProgressBar, QTableWidget, QTableWidgetItem,
        QSpinBox, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised on the TV client
    raise SystemExit("PySide6 is required: sudo pacman -S pyside6") from exc

import holocron
import updater

GREEN = "#66ff66"
DIM = "#2c8f42"
BLACK = "#020504"
PANEL = "#030806"
WARN = "#ffd166"
RED = "#ff5f56"
FONT = "IBM Plex Mono"
EVENT_ROTATION_SECONDS = 30

THEME_PRESETS = {
    "Holocron Green": {
        "background": "#020504", "panel": "#030806", "primary": "#66ff66",
        "dim": "#2c8f42", "warning": "#ffd166", "error": "#ff5f56",
    },
    "Amber Terminal": {
        "background": "#080604", "panel": "#100b05", "primary": "#ffb000",
        "dim": "#9a6910", "warning": "#ffe08a", "error": "#ff665c",
    },
    "Ice Blue": {
        "background": "#03070a", "panel": "#061117", "primary": "#73d7ff",
        "dim": "#267d9e", "warning": "#ffd166", "error": "#ff6b7a",
    },
    "Monochrome": {
        "background": "#050505", "panel": "#101010", "primary": "#eeeeee",
        "dim": "#777777", "warning": "#dddddd", "error": "#aaaaaa",
    },
}


def apply_theme_values(colors: dict[str, str]) -> None:
    """Update the module palette used by stylesheets and custom paint events."""
    global BLACK, PANEL, GREEN, DIM, WARN, RED
    BLACK = colors["background"]
    PANEL = colors["panel"]
    GREEN = colors["primary"]
    DIM = colors["dim"]
    WARN = colors["warning"]
    RED = colors["error"]


def fmt_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return "0B"


def fmt_rate(value: float) -> str:
    return f"{fmt_bytes(value)}/s"


class Panel(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 12, 18, 12)
        self.layout.setSpacing(8)
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("panelTitle")
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout.addWidget(self.title_label)


class Glyph(QWidget):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.setFixedSize(56, 56)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(GREEN), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = QRectF(8, 8, 40, 40)
        if self.kind == "cpu":
            p.drawRect(r.adjusted(5, 5, -5, -5))
            for n in range(5):
                o = 12 + n * 8
                p.drawLine(o, 3, o, 10); p.drawLine(o, 46, o, 53)
                p.drawLine(3, o, 10, o); p.drawLine(46, o, 53, o)
        elif self.kind == "ram":
            p.drawRect(QRectF(6, 17, 44, 24))
            for n in range(5): p.drawLine(13 + n * 7, 17, 13 + n * 7, 10)
            p.drawRect(QRectF(14, 24, 28, 10))
        elif self.kind == "disk":
            p.drawRoundedRect(QRectF(8, 10, 40, 36), 4, 4)
            p.drawLine(13, 30, 43, 30); p.drawEllipse(QPointF(39, 38), 2, 2)
        elif self.kind == "network":
            for n, h in enumerate((22, 35, 28, 43)):
                p.drawRect(QRectF(7 + n * 12, 49 - h, 5, h))
        else:
            p.drawEllipse(r); p.drawLine(28, 28, 28, 14); p.drawLine(28, 28, 39, 34)
        p.end()


class MetricCard(Panel):
    def __init__(self, title: str, kind: str) -> None:
        super().__init__(title)
        row = QHBoxLayout(); row.setSpacing(14)
        row.addWidget(Glyph(kind))
        values = QVBoxLayout(); values.setSpacing(2)
        self.primary = QLabel("—"); self.primary.setObjectName("metricValue")
        self.secondary = QLabel("—"); self.secondary.setObjectName("metricSecondary")
        values.addWidget(self.primary); values.addWidget(self.secondary)
        row.addLayout(values); row.addStretch()
        self.layout.addLayout(row)
        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.setTextVisible(False)
        self.layout.addWidget(self.bar)

    def update_value(self, percent: float, primary: str, secondary: str) -> None:
        self.primary.setText(primary); self.secondary.setText(secondary)
        self.bar.setValue(max(0, min(100, round(percent))))


class WeatherGlyph(QWidget):
    """Large line-art symbol that occupies only the weather strip's left edge."""

    def __init__(self) -> None:
        super().__init__()
        self.condition = ""
        self.setFixedSize(96, 82)

    def set_condition(self, condition: str) -> None:
        self.condition = condition.lower()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(GREEN), 4))
        p.setBrush(QColor(PANEL))
        rainy = any(
            word in self.condition
            for word in ("rain", "drizzle", "shower", "storm")
        )
        clear = any(word in self.condition for word in ("clear", "sunny"))
        if clear:
            p.drawEllipse(QRectF(29, 17, 38, 38))
            for x1, y1, x2, y2 in (
                (48, 3, 48, 12), (48, 60, 48, 69),
                (12, 36, 22, 36), (74, 36, 84, 36),
                (22, 10, 29, 17), (67, 55, 74, 62),
                (22, 62, 29, 55), (67, 17, 74, 10),
            ):
                p.drawLine(x1, y1, x2, y2)
        else:
            p.drawEllipse(QRectF(10, 34, 32, 28))
            p.drawEllipse(QRectF(29, 20, 42, 42))
            p.drawEllipse(QRectF(61, 34, 27, 28))
            p.drawLine(23, 62, 75, 62)
            if rainy:
                for x in (31, 48, 65):
                    p.drawLine(x, 68, x - 4, 78)
        p.end()


class WeatherBar(Panel):
    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(); row.setSpacing(48)
        self.icon = WeatherGlyph()
        self.place = QLabel("BURNIE, AUSTRALIA\nLoading weather…")
        self.place.setObjectName("weatherText")
        self.temp = QLabel("—°C"); self.temp.setObjectName("weatherTemp")
        self.detail = QLabel("Wind: —\nHumidity: —\nVisibility: —")
        self.sun = QLabel("Sunrise: —\nSunset: —")
        for widget in (self.icon, self.place, self.temp, self.detail, self.sun):
            row.addWidget(widget)
        row.setStretch(0, 0); row.setStretch(1, 3); row.setStretch(2, 2)
        row.setStretch(3, 2); row.setStretch(4, 2)
        self.layout.addLayout(row)

    def set_weather(self, weather: dict[str, str]) -> None:
        condition = weather.get("condition", "Unavailable")
        self.icon.set_condition(condition)
        self.place.setText(
            f"{weather.get('place', 'BURNIE, AUSTRALIA').upper()}\n{condition}"
        )
        self.temp.setText(f"{weather.get('temp', '—')}°C<br><small>Feels like {weather.get('feels', '—')}°C</small>")
        self.temp.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setText(f"Wind: {weather.get('wind', '—')} km/h\nHumidity: {weather.get('humidity', '—')}%\nVisibility: {weather.get('visibility', '—')} km")
        self.sun.setText(f"Sunrise: {weather.get('sunrise', '—')}\nSunset: {weather.get('sunset', '—')}")

    def set_loading(self, location: str) -> None:
        """Make a location change visible while its fresh data is fetched."""
        place = location.strip() or "AUTO-DETECT"
        self.icon.set_condition("")
        self.place.setText(f"{place.upper()}\nUpdating weather…")


class ContainerPanel(Panel):
    def __init__(self) -> None:
        super().__init__("DOCKER CONTAINERS")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["CONTAINER", "STATUS", "UPTIME", "CPU", "MEMORY"])
        self.table.verticalHeader().hide(); self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        for col in range(5): header.setSectionResizeMode(col, header.ResizeMode.Stretch)
        self.layout.addWidget(self.table)

    def set_rows(self, rows: list[dict[str, object]]) -> None:
        self.table.setRowCount(min(8, len(rows)))
        for r, row in enumerate(rows[:8]):
            running = str(row.get("state", "")).lower() == "running"
            status = "●  UP" if running else "●  DOWN"
            uptime = str(row.get("status", "—")).replace("Up ", "", 1).split(" (")[0]
            values = (row.get("name", "—"), status, uptime, row.get("cpu", "—"), row.get("memory", "—"))
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setForeground(QColor(GREEN if running else RED))
                self.table.setItem(r, c, item)


class LogPanel(Panel):
    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.current_service = QLabel("CURRENT SERVICE: WAITING FOR SERVER DATA")
        self.current_service.setObjectName("currentService")
        self.current_service.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.current_service)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(80); self.text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.layout.addWidget(self.text)

    def set_lines(self, lines: list[str]) -> None:
        self.text.setPlainText("\n".join(lines[-13:]))
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def set_source_status(
        self,
        kind: str,
        name: str,
        position: int,
        total: int,
        seconds: int,
    ) -> None:
        label = name if kind == "SYSTEM" else f"{name}  ·  DOCKER"
        self.current_service.setText(
            f"CURRENT SERVICE: {label.upper()}     "
            f"SOURCE {position}/{total}     NEXT: {seconds:02d}s"
        )


class NetworkGraph(QWidget):
    def __init__(self) -> None:
        super().__init__(); self.values: deque[float] = deque([0.0] * 80, maxlen=80)
        self.setMinimumHeight(90)

    def add_value(self, value: float) -> None:
        self.values.append(max(0.0, value)); self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BLACK)); p.setPen(QPen(QColor(DIM), 1)); p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        vals = list(self.values); peak = max(max(vals), 1.0); w = max(1, self.width() - 10); h = max(1, self.height() - 12)
        points = [QPointF(5 + i * w / (len(vals) - 1), 6 + h * (1 - value / peak)) for i, value in enumerate(vals)]
        p.setPen(QPen(QColor(GREEN), 2)); p.drawPolyline(QPolygonF(points)); p.end()


class NetworkPanel(Panel):
    def __init__(self) -> None:
        super().__init__("NETWORK STATISTICS")
        row = QHBoxLayout(); row.setSpacing(24)
        self.totals = QLabel("Total Received: —\nTotal Transmitted: —")
        self.graph = NetworkGraph()
        self.identity = QLabel("Interface: —\nIP Address: —\nGateway: —")
        row.addWidget(self.totals, 2); row.addWidget(self.graph, 4); row.addWidget(self.identity, 2)
        self.layout.addLayout(row)

    def set_network(self, data: dict[str, object]) -> None:
        self.totals.setText(f"Total Received: {fmt_bytes(float(data.get('received_total', 0)))}\nTotal Transmitted: {fmt_bytes(float(data.get('transmitted_total', 0)))}")
        self.identity.setText(f"Interface: {data.get('interface', '—')}\nIP Address: {data.get('address', '—')}\nGateway: {data.get('gateway', '—')}")
        self.graph.add_value(float(data.get("receive_rate", 0)))


class SettingsDialog(QDialog):
    """Keyboard-friendly editor for settings that can be changed in Qt."""

    update_requested = Signal()

    def __init__(self, config: holocron.Config, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Holocron Settings")
        self.setModal(True)
        self.setMinimumSize(900, 640)
        layout = QVBoxLayout(self)
        title = QLabel("HOLOCRON SETTINGS")
        title.setObjectName("settingsTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(config), "Dashboard")
        tabs.addTab(self._weather_tab(config), "Weather & Audio")
        tabs.addTab(self._system_tab(config), "System Sources")
        tabs.addTab(self._theme_tab(config), "Theme & Cursor")
        tabs.addTab(self._updates_tab(config), "Updates")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(32, 24, 32, 24)
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(16)
        return form

    @staticmethod
    def _check(value: bool) -> QCheckBox:
        box = QCheckBox()
        box.setChecked(value)
        return box

    @staticmethod
    def _spin(value: int, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    @staticmethod
    def _double(value: float, minimum: float, maximum: float, suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def _general_tab(self, config: holocron.Config) -> QWidget:
        tab = QWidget(); form = self._form(); tab.setLayout(form)
        self.title = QLineEdit(config.title)
        self.font_size = self._spin(config.gui_font_size, 12, 32, " px")
        self.rotation_seconds = self._spin(config.rotation_seconds, 5, 3600, " s")
        self.tail_lines = self._spin(config.tail_lines, 10, 5000, " lines")
        self.refresh_seconds = self._double(config.refresh_seconds, 0.5, 60.0, " s")
        self.containers = QLineEdit(", ".join(config.containers or []))
        self.containers.setPlaceholderText("Blank = all containers")
        self.dashboard_mode = self._check(config.dashboard_mode)
        self.show_all_containers = self._check(config.show_all_containers)
        self.show_timestamps = self._check(config.show_timestamps)
        self.simplify_messages = self._check(config.simplify_messages)
        form.addRow("Dashboard title", self.title)
        form.addRow("Dashboard font size", self.font_size)
        form.addRow("Source rotation", self.rotation_seconds)
        form.addRow("Log lines per source", self.tail_lines)
        form.addRow("Refresh interval", self.refresh_seconds)
        form.addRow("Containers", self.containers)
        form.addRow("Start in Dashboard view", self.dashboard_mode)
        form.addRow("Show stopped containers", self.show_all_containers)
        form.addRow("Show timestamps", self.show_timestamps)
        form.addRow("Simplify log messages", self.simplify_messages)
        return tab

    def _weather_tab(self, config: holocron.Config) -> QWidget:
        tab = QWidget(); form = self._form(); tab.setLayout(form)
        self.weather_enabled = self._check(config.weather_enabled)
        self.weather_location = QLineEdit(config.weather_location)
        self.weather_location.setPlaceholderText("e.g. Burnie, Hobart, 7320")
        self.weather_refresh_seconds = self._spin(config.weather_refresh_seconds, 60, 86400, " s")
        self.speech_enabled = self._check(config.speech_enabled)
        self.startup_phrase = QLineEdit(config.startup_phrase)
        self.startup_audio = QLineEdit(config.startup_audio)
        self.startup_audio.setPlaceholderText("Optional WAV or audio file")
        form.addRow("Weather enabled", self.weather_enabled)
        form.addRow("Weather location", self.weather_location)
        form.addRow("Weather refresh", self.weather_refresh_seconds)
        form.addRow("Startup speech", self.speech_enabled)
        form.addRow("Startup phrase", self.startup_phrase)
        form.addRow("Startup audio", self.startup_audio)
        return tab

    def _system_tab(self, config: holocron.Config) -> QWidget:
        tab = QWidget(); form = self._form(); tab.setLayout(form)
        self.journal_enabled = self._check(config.journal_enabled)
        self.journal_tail_lines = self._spin(config.journal_tail_lines, 10, 5000, " lines")
        self.journal_refresh_seconds = self._double(config.journal_refresh_seconds, 0.5, 60.0, " s")
        self.journal_priority = QComboBox()
        self.journal_priority.addItems(["debug", "info", "notice", "warning", "err"])
        self.journal_priority.setCurrentText(config.journal_priority)
        self.update_refresh_seconds = self._spin(config.update_refresh_seconds, 60, 86400, " s")
        self.log_file = QLineEdit(config.log_file)
        form.addRow("System journal", self.journal_enabled)
        form.addRow("Journal lines", self.journal_tail_lines)
        form.addRow("Journal refresh", self.journal_refresh_seconds)
        form.addRow("Minimum journal priority", self.journal_priority)
        form.addRow("APT update check", self.update_refresh_seconds)
        form.addRow("Application log file", self.log_file)
        hint = QLabel("The log file path takes effect after restarting Holocron.")
        hint.setObjectName("settingsHint")
        form.addRow("", hint)
        return tab

    def _theme_tab(self, config: holocron.Config) -> QWidget:
        tab = QWidget(); form = self._form(); tab.setLayout(form)
        self.theme_name = QComboBox()
        self.theme_name.addItems(list(THEME_PRESETS) + ["Custom"])
        self.theme_name.setCurrentText(config.theme_name)
        if self.theme_name.currentText() != config.theme_name:
            self.theme_name.setCurrentText("Custom")
        self.theme_colors = {
            "background": config.theme_background,
            "panel": config.theme_panel,
            "primary": config.theme_primary,
            "dim": config.theme_dim,
            "warning": config.theme_warning,
            "error": config.theme_error,
        }
        self.theme_buttons: dict[str, QPushButton] = {}
        labels = {
            "background": "Background",
            "panel": "Panels",
            "primary": "Primary text",
            "dim": "Secondary text",
            "warning": "Warnings",
            "error": "Errors",
        }
        self.theme_name.currentTextChanged.connect(self._preset_changed)
        form.addRow("Theme preset", self.theme_name)
        for key, label in labels.items():
            button = QPushButton()
            button.clicked.connect(lambda checked=False, name=key: self._choose_color(name))
            self.theme_buttons[key] = button
            form.addRow(label, button)
            self._paint_color_button(key)
        self.cursor_hide_enabled = self._check(config.cursor_hide_enabled)
        self.cursor_hide_seconds = self._spin(config.cursor_hide_seconds, 1, 3600, " s")
        form.addRow("Hide cursor when inactive", self.cursor_hide_enabled)
        form.addRow("Cursor inactivity timeout", self.cursor_hide_seconds)
        hint = QLabel("The cursor is hidden only in fullscreen mode and returns when you move the mouse or open a dialog.")
        hint.setWordWrap(True); hint.setObjectName("settingsHint")
        form.addRow("", hint)
        return tab

    def _paint_color_button(self, name: str) -> None:
        color = self.theme_colors[name]
        self.theme_buttons[name].setText(color.upper())
        self.theme_buttons[name].setStyleSheet(
            f"background:{color}; color:{BLACK}; border:1px solid {GREEN}; padding:10px;"
        )

    def _preset_changed(self, name: str) -> None:
        if name not in THEME_PRESETS:
            return
        self.theme_colors = dict(THEME_PRESETS[name])
        for key in self.theme_buttons:
            self._paint_color_button(key)

    def _choose_color(self, name: str) -> None:
        color = QColorDialog.getColor(QColor(self.theme_colors[name]), self, f"Choose {name} colour")
        if not color.isValid():
            return
        self.theme_colors[name] = color.name().lower()
        self.theme_name.setCurrentText("Custom")
        self._paint_color_button(name)

    def _updates_tab(self, config: holocron.Config) -> QWidget:
        del config
        tab = QWidget(); form = self._form(); tab.setLayout(form)
        current = QLabel(f"Installed version: {holocron.VERSION}")
        repository = QLabel(updater.REPOSITORY)
        check = QPushButton("Check GitHub Releases")
        check.clicked.connect(self.update_requested.emit)
        hint = QLabel("Updates apply to this display client. The server agent must be updated separately.")
        hint.setWordWrap(True); hint.setObjectName("settingsHint")
        form.addRow("Current version", current)
        form.addRow("Repository", repository)
        form.addRow("Release check", check)
        form.addRow("", hint)
        return tab

    def apply_to(self, config: holocron.Config) -> None:
        config.title = self.title.text().strip() or config.title
        config.gui_font_size = self.font_size.value()
        config.rotation_seconds = self.rotation_seconds.value()
        config.tail_lines = self.tail_lines.value()
        config.refresh_seconds = self.refresh_seconds.value()
        names = [name.strip() for name in self.containers.text().split(",") if name.strip()]
        config.containers = names or None
        config.dashboard_mode = self.dashboard_mode.isChecked()
        config.show_all_containers = self.show_all_containers.isChecked()
        config.show_timestamps = self.show_timestamps.isChecked()
        config.simplify_messages = self.simplify_messages.isChecked()
        config.weather_enabled = self.weather_enabled.isChecked()
        config.weather_location = self.weather_location.text().strip()
        config.weather_refresh_seconds = self.weather_refresh_seconds.value()
        config.speech_enabled = self.speech_enabled.isChecked()
        config.startup_phrase = self.startup_phrase.text()
        config.startup_audio = self.startup_audio.text().strip()
        config.journal_enabled = self.journal_enabled.isChecked()
        config.journal_tail_lines = self.journal_tail_lines.value()
        config.journal_refresh_seconds = self.journal_refresh_seconds.value()
        config.journal_priority = self.journal_priority.currentText()
        config.update_refresh_seconds = self.update_refresh_seconds.value()
        config.log_file = self.log_file.text().strip() or config.log_file
        config.theme_name = self.theme_name.currentText()
        config.theme_background = self.theme_colors["background"]
        config.theme_panel = self.theme_colors["panel"]
        config.theme_primary = self.theme_colors["primary"]
        config.theme_dim = self.theme_colors["dim"]
        config.theme_warning = self.theme_colors["warning"]
        config.theme_error = self.theme_colors["error"]
        config.cursor_hide_enabled = self.cursor_hide_enabled.isChecked()
        config.cursor_hide_seconds = self.cursor_hide_seconds.value()


class UpdateDialog(QDialog):
    """Check and install a published GitHub release for this display client."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Holocron Updates")
        self.setModal(True)
        self.setMinimumSize(760, 520)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="holocron-update")
        self.future: Future[object] | None = None
        self.release: updater.ReleaseInfo | None = None
        layout = QVBoxLayout(self)
        title = QLabel("HOLOCRON RELEASE UPDATES")
        title.setObjectName("settingsTitle"); title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        self.status = QLabel("Checking GitHub releases…")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check Again")
        self.install_button = QPushButton("Download and Install")
        self.install_button.setEnabled(False)
        close_button = QPushButton("Close")
        self.check_button.clicked.connect(self.check)
        self.install_button.clicked.connect(self.install)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(self.check_button); buttons.addWidget(self.install_button); buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(150)
        self.check()

    def check(self) -> None:
        if self.future is not None and not self.future.done():
            return
        self.release = None
        self.install_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.status.setText("Checking GitHub releases…")
        self.future = self.executor.submit(updater.fetch_latest_release)

    def install(self) -> None:
        if self.release is None:
            return
        answer = QMessageBox.question(
            self,
            "Install Holocron update?",
            f"Download and install Holocron {self.release.tag_name} on this display client?\n\n"
            "The system will ask for administrator permission. Restart Holocron after installation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.install_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.status.setText(f"Installing {self.release.tag_name}…")
        self.future = self.executor.submit(updater.install_release, self.release, "--client")

    def poll(self) -> None:
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        try:
            result = future.result()
            if isinstance(result, updater.ReleaseInfo):
                self.release = result
                current = f"v{holocron.VERSION}"
                if updater.is_newer(result, holocron.VERSION):
                    self.status.setText(f"Update available: {current} → {result.tag_name}")
                    self.install_button.setEnabled(True)
                else:
                    self.status.setText(f"You are running the latest release ({current}).")
                self.details.setPlainText(result.body or "No release notes were provided.")
            else:
                self.status.setText("Update installed. Restart Holocron to use it.")
                self.details.setPlainText(str(result))
        except Exception as exc:
            self.status.setText("Update check failed")
            self.details.setPlainText(str(exc))
        self.check_button.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


class PortSummary(Panel):
    """One compact, TV-readable value on the Port Manager page."""

    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.value = QLabel("—")
        self.value.setObjectName("portValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel("")
        self.detail.setObjectName("metricSecondary")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addStretch()
        self.layout.addWidget(self.value)
        self.layout.addWidget(self.detail)
        self.layout.addStretch()


class PortManagerPage(QWidget):
    """Read-only projection of the Port Manager's versioned status JSON."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(16)

        title = QLabel("⬡  HOLOCRON PORT MANAGER  ⬡")
        title.setObjectName("mainTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        summaries = QHBoxLayout()
        summaries.setSpacing(24)
        self.service = PortSummary("STATUS")
        self.vpn = PortSummary("VPN")
        self.port = PortSummary("FORWARDED PORT")
        self.renewal = PortSummary("RENEWAL TIMER")
        self.last_success = PortSummary("LAST SUCCESSFUL RENEWAL")
        for card in (self.service, self.vpn, self.port, self.renewal, self.last_success):
            summaries.addWidget(card)
        outer.addLayout(summaries, 4)

        applications = Panel("MANAGED APPLICATIONS")
        self.application_table = QTableWidget(0, 5)
        self.application_table.setHorizontalHeaderLabels(
            ["APPLICATION", "STATUS", "LISTENING PORT", "WEB API", "LAST UPDATE"]
        )
        self.application_table.verticalHeader().hide()
        self.application_table.setShowGrid(False)
        self.application_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.application_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.application_table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, header.ResizeMode.Stretch)
        applications.layout.addWidget(self.application_table)
        outer.addWidget(applications, 5)

        events = Panel("RECENT EVENTS")
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.events.setMaximumBlockCount(50)
        events.layout.addWidget(self.events)
        outer.addWidget(events, 6)

        footer = Panel()
        footer_text = QLabel("[D] Dashboard     [P] Port Manager     [Q] Quit")
        footer_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.layout.addWidget(footer_text)
        outer.addWidget(footer, 1)

    @staticmethod
    def _short_time(value: object, include_date: bool = False) -> str:
        text = str(value or "")
        if not text:
            return "—"
        text = text.replace("T", " ")
        return text[:16] if include_date else text[11:16]

    def set_unavailable(self, message: str = "STATUS FILE NOT AVAILABLE") -> None:
        self.service.value.setText("OFFLINE")
        self.service.detail.setText(message.upper())
        for card in (self.vpn, self.port, self.renewal, self.last_success):
            card.value.setText("—")
            card.detail.setText("")
        self.application_table.setRowCount(0)
        self.events.setPlainText("Waiting for /var/lib/holocron/port-manager/status.json")

    def set_status(self, data: dict[str, object]) -> None:
        if not data.get("available", True):
            self.set_unavailable(str(data.get("error", "status file not available")))
            return

        service = data.get("service", {})
        provider = data.get("provider", {})
        if not isinstance(service, dict) or not isinstance(provider, dict):
            self.set_unavailable("invalid status data")
            return

        self.service.value.setText(str(service.get("status", "unknown")).upper())
        self.service.detail.setText(f"Port Manager v{data.get('version', '—')}")
        self.vpn.value.setText(str(provider.get("status", "unknown")).upper())
        self.vpn.detail.setText(str(provider.get("name", "VPN")))
        forwarded = provider.get("forwarded_port")
        self.port.value.setText(str(forwarded) if forwarded is not None else "—")
        remaining = int(service.get("seconds_until_renewal", 0) or 0)
        self.renewal.value.setText(f"{remaining} seconds")
        self.renewal.detail.setText(
            f"Every {service.get('renewal_interval_seconds', 45)} seconds"
        )
        self.last_success.value.setText(
            self._short_time(service.get("last_successful_renewal"), include_date=True)
        )

        applications = data.get("applications", [])
        if not isinstance(applications, list):
            applications = []
        self.application_table.setRowCount(len(applications))
        for row, application in enumerate(applications):
            if not isinstance(application, dict):
                continue
            api = application.get("api", {})
            api_status = api.get("status", "—") if isinstance(api, dict) else "—"
            values = (
                application.get("name", application.get("id", "—")),
                str(application.get("status", "unknown")).upper(),
                application.get("listening_port", "—"),
                str(api_status).replace("_", " ").upper(),
                self._short_time(application.get("last_update")),
            )
            healthy = str(application.get("status", "")).lower() == "connected"
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value if value is not None else "—"))
                item.setForeground(QColor(GREEN if healthy else WARN))
                self.application_table.setItem(row, column, item)

        events = data.get("recent_events", [])
        lines: list[str] = []
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict):
                    lines.append(
                        f"{self._short_time(event.get('timestamp'))}  "
                        f"{event.get('message', '')}"
                    )
        self.events.setPlainText("\n".join(lines) if lines else "No events recorded yet.")


class Dashboard(QMainWindow):
    def __init__(self, config: holocron.Config, host: str, agent_command: str, config_path: Path = holocron.DEFAULT_CONFIG_PATH) -> None:
        super().__init__(); self.config = config; self.host = host; self.agent_command = agent_command
        self.config_path = config_path
        apply_theme_values(self._config_theme())
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="holocron-gui")
        self.snapshot_future: Future[dict[str, object]] | None = None
        self.weather_future: Future[tuple[int, dict[str, str]]] | None = None
        self.weather_generation = 0
        self.last_weather = 0.0; self.paused = False; self.last_ok = 0.0
        self.last_snapshot_submit = 0.0; self.force_refresh = True
        self.event_sources: list[dict[str, object]] = []
        self.event_source_index = 0
        self.last_event_rotation = time.monotonic()
        self.last_cursor_activity = time.monotonic()
        self.cursor_hidden = False
        self.setMouseTracking(True)
        self.setWindowTitle("Holocron Dashboard"); self.resize(1920, 1080)
        self._build(); self._style()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(250)
        self.clock_timer = QTimer(self); self.clock_timer.timeout.connect(self.update_clock); self.clock_timer.start(1000)
        self.cursor_timer = QTimer(self); self.cursor_timer.timeout.connect(self._cursor_tick); self.cursor_timer.start(250)
        QApplication.instance().installEventFilter(self)
        self.update_clock(); self.tick()

    def _config_theme(self) -> dict[str, str]:
        return {
            "background": self.config.theme_background,
            "panel": self.config.theme_panel,
            "primary": self.config.theme_primary,
            "dim": self.config.theme_dim,
            "warning": self.config.theme_warning,
            "error": self.config.theme_error,
        }

    def _build(self) -> None:
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        root = QWidget()
        self.pages.addWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(24, 18, 24, 14); outer.setSpacing(12)
        header = QHBoxLayout()
        self.identity = QLabel(f"HOLOCRON OS v{holocron.VERSION}")
        title = QLabel("⬡  HOLOCRON DASHBOARD  ⬡\nJEDI ARCHIVES — SYSTEM MONITOR")
        title.setObjectName("mainTitle"); title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock = QLabel(); self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.identity, 2); header.addWidget(title, 5); header.addWidget(self.clock, 2); outer.addLayout(header)
        cards = QHBoxLayout(); cards.setSpacing(24)
        self.cpu = MetricCard("CPU", "cpu"); self.ram = MetricCard("RAM", "ram"); self.disk = MetricCard("DISK", "disk")
        self.net = MetricCard("NETWORK", "network"); self.uptime = MetricCard("UPTIME", "uptime")
        for card in (self.cpu, self.ram, self.disk, self.net, self.uptime): cards.addWidget(card)
        outer.addLayout(cards, 18)
        self.weather = WeatherBar(); outer.addWidget(self.weather, 12)
        middle = QHBoxLayout(); middle.setSpacing(24)
        self.containers = ContainerPanel()
        self.logs = LogPanel("SERVER EVENT STREAM  ·  30 SECOND SERVICE SCAN")
        middle.addWidget(self.containers); middle.addWidget(self.logs); outer.addLayout(middle, 35)
        self.network_panel = NetworkPanel(); outer.addWidget(self.network_panel, 15)
        footer = Panel(); row = QHBoxLayout()
        row.addWidget(QLabel("[P] Port Manager     [S] Settings     [SPACE] Pause/Resume     [R] Refresh     [F11] Fullscreen     [Q] Quit"), 1)
        self.connection = QLabel("CONNECTING"); self.connection.setAlignment(Qt.AlignmentFlag.AlignRight); row.addWidget(self.connection)
        footer.layout.addLayout(row)
        quote = QLabel("Do or do not. There is no try. — Yoda"); quote.setAlignment(Qt.AlignmentFlag.AlignCenter); footer.layout.addWidget(quote)
        outer.addWidget(footer, 9)
        self.port_manager = PortManagerPage()
        self.port_manager.set_unavailable()
        self.pages.addWidget(self.port_manager)

    def _style(self) -> None:
        base = max(12, min(32, int(self.config.gui_font_size)))
        panel_title = round(base * 1.19)
        metric = round(base * 1.25)
        main_title = round(base * 1.75)
        weather_temp = round(base * 2)
        port_value = round(base * 1.45)
        self.setStyleSheet(f"""
            * {{ color:{GREEN}; font-family:'{FONT}','DejaVu Sans Mono',monospace; font-size:{base}px; }}
            QMainWindow, QWidget {{ background:{BLACK}; }}
            QFrame#panel {{ background:{PANEL}; border:1px solid {DIM}; }}
            QDialog {{ background:{PANEL}; border:2px solid {GREEN}; }}
            QLabel#mainTitle {{ font-size:{main_title}px; font-weight:800; letter-spacing:2px; }}
            QLabel#panelTitle {{ border:0; font-size:{panel_title}px; font-weight:700; }}
            QLabel#currentService {{ border:1px solid {DIM}; padding:6px; font-size:{panel_title}px; font-weight:800; }}
            QLabel#metricValue {{ border:0; font-size:{metric}px; font-weight:700; }}
            QLabel#metricSecondary, QLabel#weatherText {{ border:0; font-size:{base}px; }}
            QLabel#weatherTemp {{ border:0; font-size:{weather_temp}px; font-weight:700; }}
            QLabel#portValue {{ border:0; font-size:{port_value}px; font-weight:800; }}
            QLabel#settingsTitle {{ font-size:{main_title}px; font-weight:800; padding:14px; }}
            QLabel#settingsHint {{ color:{DIM}; padding:8px; }}
            QLineEdit, QSpinBox {{ background:{BLACK}; border:1px solid {DIM}; padding:10px; min-height:28px; }}
            QPushButton {{ background:{BLACK}; border:1px solid {GREEN}; padding:10px 26px; min-width:120px; }}
            QPushButton:focus {{ background:{DIM}; color:{BLACK}; }}
            QProgressBar {{ background:#071009; border:1px solid {DIM}; height:12px; }}
            QProgressBar::chunk {{ background:{GREEN}; }}
            QTableWidget, QPlainTextEdit {{ background:{BLACK}; border:0; selection-background-color:{DIM}; }}
            QHeaderView::section {{ background:{PANEL}; color:{GREEN}; border:0; padding:3px; font-weight:700; }}
            QTableWidget::item {{ padding:2px; }}
            QScrollBar {{ width:0; height:0; }}
        """)

    def update_clock(self) -> None:
        now = time.localtime(); self.clock.setText(time.strftime("Time: %H:%M:%S\nDate: %d-%m-%Y", now))

    def _show_cursor(self) -> None:
        self.last_cursor_activity = time.monotonic()
        if self.cursor_hidden:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.cursor_hidden = False

    def _cursor_tick(self) -> None:
        if (
            self.config.cursor_hide_enabled
            and self.isFullScreen()
            and not self.cursor_hidden
            and QApplication.activeModalWidget() is None
            and time.monotonic() - self.last_cursor_activity >= self.config.cursor_hide_seconds
        ):
            self.setCursor(QCursor(Qt.CursorShape.BlankCursor))
            self.cursor_hidden = True

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
            if QApplication.activeModalWidget() is None and self.isActiveWindow():
                self._show_cursor()
        return super().eventFilter(watched, event)

    def _snapshot(self) -> dict[str, object]:
        if self.host:
            command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", self.host, self.agent_command]
        else:
            command = [sys.executable, str(Path(__file__).with_name("holocron_agent.py"))]
        result = subprocess.run(command, text=True, capture_output=True, timeout=25, check=False)
        if result.returncode != 0: raise RuntimeError(result.stderr.strip() or "snapshot command failed")
        data = json.loads(result.stdout)
        if "error" in data: raise RuntimeError(str(data["error"]))
        return data

    @staticmethod
    def _weather(location: str, generation: int) -> tuple[int, dict[str, str]]:
        client = holocron.WeatherClient(location)
        return generation, client.conditions()

    def refresh_weather(self) -> None:
        """Start a fresh request and invalidate any result for an old location."""
        self.weather_generation += 1
        location = self.config.weather_location.strip()
        self.weather.set_loading(location)
        if self.weather_future is not None:
            self.weather_future.cancel()
        generation = self.weather_generation
        self.weather_future = self.executor.submit(
            self._weather,
            location,
            generation,
        )

    def show_settings(self) -> None:
        self._show_cursor()
        dialog = SettingsDialog(self.config, self)
        dialog.update_requested.connect(self.show_update_dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        old_location = self.config.weather_location.strip()
        old_weather_enabled = self.config.weather_enabled
        dialog.apply_to(self.config)
        try:
            self.config.save(self.config_path)
        except OSError as exc:
            QMessageBox.critical(self, "Settings not saved", str(exc))
            return

        apply_theme_values(self._config_theme())
        self._style()
        for widget in self.findChildren(QWidget):
            widget.update()
        self.force_refresh = True
        if self.config.weather_enabled and (
            self.config.weather_location != old_location
            or not old_weather_enabled
        ):
            self.last_weather = 0.0
            self.refresh_weather()
        elif not self.config.weather_enabled and self.weather_future is not None:
            self.weather_future.cancel()
        self.connection.setText("SETTINGS SAVED")

    def show_update_dialog(self) -> None:
        self._show_cursor()
        dialog = UpdateDialog(self)
        dialog.exec()
        self._show_cursor()

    def tick(self) -> None:
        if self.snapshot_future and self.snapshot_future.done():
            try:
                self.apply_snapshot(self.snapshot_future.result()); self.last_ok = time.monotonic(); self.connection.setText("LINK: ONLINE")
            except Exception as exc:
                self.connection.setText(f"LINK: {str(exc)[:42]}")
            self.snapshot_future = None
        refresh_due = time.monotonic() - self.last_snapshot_submit >= max(2.0, self.config.refresh_seconds)
        if not self.paused and self.snapshot_future is None and (refresh_due or self.force_refresh):
            self.snapshot_future = self.executor.submit(self._snapshot)
            self.last_snapshot_submit = time.monotonic(); self.force_refresh = False
        if self.weather_future and self.weather_future.done():
            try:
                generation, weather = self.weather_future.result()
                if generation == self.weather_generation:
                    self.weather.set_weather(weather)
            except Exception as exc:
                self.weather.place.setText(
                    f"☁  {(self.config.weather_location or 'AUTO-DETECT').upper()}\n"
                    f"Weather unavailable: {str(exc)[:40]}"
                )
            self.weather_future = None; self.last_weather = time.monotonic()
        if self.config.weather_enabled and self.weather_future is None and time.monotonic() - self.last_weather > max(60, self.config.weather_refresh_seconds):
            self.refresh_weather()
        self._tick_event_rotation()

    def _tick_event_rotation(self) -> None:
        if not self.event_sources:
            return
        elapsed = time.monotonic() - self.last_event_rotation
        if not self.paused and elapsed >= EVENT_ROTATION_SECONDS:
            steps = int(elapsed // EVENT_ROTATION_SECONDS)
            self.event_source_index = (
                self.event_source_index + steps
            ) % len(self.event_sources)
            self.last_event_rotation += steps * EVENT_ROTATION_SECONDS
            self._render_event_source()
            elapsed = time.monotonic() - self.last_event_rotation
        remaining = max(0, EVENT_ROTATION_SECONDS - int(elapsed))
        self._update_event_source_status(remaining)

    def _set_event_sources(self, sources: list[dict[str, object]]) -> None:
        current_id = None
        if self.event_sources:
            current_id = self.event_sources[self.event_source_index].get("id")
        self.event_sources = sources
        if not sources:
            self.event_source_index = 0
            self.logs.current_service.setText("CURRENT SERVICE: NO SERVER LOG SOURCES")
            self.logs.set_lines([])
            return
        ids = [source.get("id") for source in sources]
        self.event_source_index = ids.index(current_id) if current_id in ids else 0
        if current_id is None or current_id not in ids:
            self.last_event_rotation = time.monotonic()
        self._render_event_source()

    def _render_event_source(self) -> None:
        if not self.event_sources:
            return
        source = self.event_sources[self.event_source_index]
        lines = source.get("lines", [])
        self.logs.set_lines(lines if isinstance(lines, list) else [])
        elapsed = time.monotonic() - self.last_event_rotation
        self._update_event_source_status(
            max(0, EVENT_ROTATION_SECONDS - int(elapsed))
        )

    def _update_event_source_status(self, remaining: int) -> None:
        if not self.event_sources:
            return
        source = self.event_sources[self.event_source_index]
        self.logs.set_source_status(
            str(source.get("kind", "SERVICE")),
            str(source.get("name", "UNKNOWN")),
            self.event_source_index + 1,
            len(self.event_sources),
            remaining,
        )

    def apply_snapshot(self, data: dict[str, object]) -> None:
        cpu = data.get("cpu", {}); ram = data.get("memory", {}); disk = data.get("disk", {}); net = data.get("network", {})
        temp = cpu.get("temperature")
        self.cpu.update_value(float(cpu.get("percent", 0)), f"{float(cpu.get('percent', 0)):.1f}%", f"Temp: {temp:.0f}°C" if isinstance(temp, (int, float)) else "Temp: —")
        self.ram.update_value(float(ram.get("percent", 0)), f"{float(ram.get('percent', 0)):.1f}%", f"{fmt_bytes(float(ram.get('used', 0)))} / {fmt_bytes(float(ram.get('total', 0)))}")
        self.disk.update_value(float(disk.get("percent", 0)), f"{float(disk.get('percent', 0)):.1f}%", f"{fmt_bytes(float(disk.get('used', 0)))} / {fmt_bytes(float(disk.get('total', 0)))}")
        rate = max(float(net.get("receive_rate", 0)), float(net.get("transmit_rate", 0)))
        self.net.update_value(min(100, rate / 1_250_000), f"↑ {fmt_rate(float(net.get('transmit_rate', 0)))}", f"↓ {fmt_rate(float(net.get('receive_rate', 0)))}")
        load = str(data.get("load", "—")).split()[0]; self.uptime.update_value(50, str(data.get("uptime", "—")), f"Load: {load}")
        self.containers.set_rows(data.get("containers", []))
        event_sources = data.get("event_sources")
        if isinstance(event_sources, list):
            self._set_event_sources(event_sources)
        else:
            event_stream = data.get("event_stream")
            if not isinstance(event_stream, list):
                event_stream = list(data.get("docker_logs", [])) + list(
                    data.get("journal", [])
                )
            self._set_event_sources([{
                "id": "legacy:combined",
                "name": "COMBINED SERVER EVENTS",
                "kind": "SYSTEM",
                "lines": event_stream,
            }])
        self.network_panel.set_network(net)
        port_manager = data.get("port_manager")
        if isinstance(port_manager, dict):
            self.port_manager.set_status(port_manager)
        else:
            self.port_manager.set_unavailable()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Q, Qt.Key.Key_Escape): self.close()
        elif event.key() == Qt.Key.Key_P: self.pages.setCurrentIndex(1)
        elif event.key() == Qt.Key.Key_D: self.pages.setCurrentIndex(0)
        elif event.key() == Qt.Key.Key_Space: self.paused = not self.paused; self.connection.setText("PAUSED" if self.paused else "LINK: ONLINE")
        elif event.key() == Qt.Key.Key_R:
            self.force_refresh = True
            if self.config.weather_enabled:
                self.refresh_weather()
        elif event.key() == Qt.Key.Key_S: self.show_settings()
        elif event.key() == Qt.Key.Key_F11: self.showNormal() if self.isFullScreen() else self.showFullScreen()
        else: super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        QApplication.instance().removeEventFilter(self)
        self.timer.stop(); self.cursor_timer.stop(); self.executor.shutdown(wait=False, cancel_futures=True); super().closeEvent(event)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="", help="SSH destination, for example tj@jedi-archives")
    parser.add_argument(
        "--agent-command",
        default="/usr/local/bin/holocron-agent",
        help="Absolute command installed on the server",
    )
    parser.add_argument("--config", type=Path, default=holocron.DEFAULT_CONFIG_PATH)
    parser.add_argument("--windowed", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv); app.setApplicationName("Holocron Dashboard")
    window = Dashboard(holocron.Config.from_file(args.config), args.host, args.agent_command, args.config)
    window.show() if args.windowed else window.showFullScreen()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
