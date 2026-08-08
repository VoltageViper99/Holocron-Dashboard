#!/usr/bin/env python3
"""Native 1920x1080 Holocron dashboard for a Cage display client."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from PySide6.QtCore import QEvent, QTimer, Qt, QRectF, QPointF
    from PySide6.QtGui import QColor, QCursor, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QMainWindow, QProgressBar, QVBoxLayout, QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised on the TV client
    raise SystemExit("PySide6 is required: sudo pacman -S pyside6") from exc

import holocron

GREEN = "#66ff66"
DIM = "#2c8f42"
BLACK = "#020504"
PANEL = "#030806"
WARN = "#ffd166"
RED = "#ff5f56"
FONT = "IBM Plex Mono"

SERVICE_WIDGETS = [
    ("Immich", "immich"), ("Navidrome", "navidrome"), ("qBittorrent", "qbittorrent"),
    ("slskd", "slskd"), ("Lidarr", "lidarr"), ("Prowlarr", "prowlarr"),
    ("FlareSolverr", "flaresolverr"), ("AdGuard Home", "adguardhome"),
    ("Gluetun", "gluetun"), ("Portainer", "portainer"),
    ("Uptime Kuma", "uptimekuma"), ("Dozzle", "dozzle"),
]

THRESHOLD_WARN = 70.0
THRESHOLD_CRITICAL = 90.0


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


def parse_percent(text: object) -> float | None:
    try:
        return float(str(text).strip().rstrip("%"))
    except ValueError:
        return None


def threshold_color(value: float | None) -> str:
    if value is None:
        return GREEN
    if value >= THRESHOLD_CRITICAL:
        return RED
    if value >= THRESHOLD_WARN:
        return WARN
    return GREEN


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

    def update_value(self, percent: float, primary: str, secondary: str, threshold: bool = False) -> None:
        self.primary.setText(primary); self.secondary.setText(secondary)
        self.bar.setValue(max(0, min(100, round(percent))))
        if threshold:
            color = threshold_color(percent)
            self.primary.setStyleSheet(f"color:{color};")
            self.bar.setStyleSheet(f"QProgressBar::chunk {{ background:{color}; }}")
        else:
            self.primary.setStyleSheet("")
            self.bar.setStyleSheet("")


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


class ForecastTile(QWidget):
    """One day of the forecast strip; the first tile is styled as 'today'."""

    def __init__(self, today: bool = False) -> None:
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0); column.setSpacing(2)
        self.label = QLabel("—"); self.label.setObjectName("forecastLabel" if not today else "forecastLabelToday")
        self.condition = QLabel("—"); self.condition.setObjectName("forecastCondition")
        self.range = QLabel("—"); self.range.setObjectName("forecastRange")
        for widget in (self.label, self.condition, self.range):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(widget)

    def set_day(self, day: dict[str, str]) -> None:
        self.label.setText(day.get("label", "—"))
        self.condition.setText(day.get("condition", "—"))
        self.range.setText(f"{day.get('low', '?')}–{day.get('high', '?')}°C")


class WeatherBar(Panel):
    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(); row.setSpacing(48)
        self.icon = WeatherGlyph()
        self.place = QLabel("Loading weather…")
        self.place.setObjectName("weatherText")
        self.temp = QLabel("—°C"); self.temp.setObjectName("weatherTemp")
        self.detail = QLabel("Wind: —\nHumidity: —\nVisibility: —")
        for widget in (self.icon, self.place, self.temp, self.detail):
            row.addWidget(widget)
        row.setStretch(0, 0); row.setStretch(1, 3); row.setStretch(2, 2)
        row.setStretch(3, 2)
        self.layout.addLayout(row)

        forecast_row = QHBoxLayout(); forecast_row.setSpacing(24)
        self.forecast_tiles = [ForecastTile(today=(i == 0)) for i in range(4)]
        for tile in self.forecast_tiles:
            forecast_row.addWidget(tile)
        self.layout.addLayout(forecast_row)

    def set_weather(self, weather: dict[str, object]) -> None:
        condition = str(weather.get("condition", "Unavailable"))
        self.icon.set_condition(condition)
        self.place.setText(f"{weather.get('place', 'Local weather')}\n{condition}")
        self.temp.setText(f"{weather.get('temp', '—')}°C<br><small>Feels like {weather.get('feels', '—')}°C</small>")
        self.temp.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setText(f"Wind: {weather.get('wind', '—')} km/h\nHumidity: {weather.get('humidity', '—')}%\nVisibility: {weather.get('visibility', '—')} km")
        days = weather.get("days") or []
        for tile, day in zip(self.forecast_tiles, days):
            tile.set_day(day)

    def set_loading(self, location: str) -> None:
        """Make a location change visible while its fresh data is fetched."""
        place = location.strip() or "AUTO-DETECT"
        self.icon.set_condition("")
        self.place.setText(f"{place.upper()}\nUpdating weather…")


def _normalize_service_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


class ServiceCard(Panel):
    def __init__(self, title: str) -> None:
        super().__init__(title)
        self.layout.setContentsMargins(14, 6, 14, 6)
        self.layout.setSpacing(2)
        self.status = QLabel("●  UNKNOWN")
        self.status.setObjectName("metricValue")
        self.detail = QLabel("CPU: —    MEM: —")
        self.detail.setObjectName("metricSecondary")
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.layout.addWidget(self.status)
        self.layout.addWidget(self.detail)

    def set_offline(self) -> None:
        self.status.setText("●  NOT FOUND")
        self.status.setStyleSheet(f"color:{DIM};")
        self.detail.setText("CPU: —    MEM: —")

    def set_state(self, container: dict[str, object]) -> None:
        health = str(container.get("health", "")).upper()
        running = health in ("RUNNING", "HEALTHY")
        color = RED if not running else (WARN if health == "UNHEALTHY" else GREEN)
        label = health if health else ("UP" if running else "DOWN")
        self.status.setText(f"●  {label}")
        self.status.setStyleSheet(f"color:{color};")
        cpu_text = container.get("cpu", "—")
        mem_text = container.get("memory", "—")
        cpu_color = threshold_color(parse_percent(cpu_text))
        mem_color = threshold_color(parse_percent(container.get("mem_percent", "—")))
        self.detail.setText(
            f'<span style="color:{cpu_color};">CPU: {cpu_text}</span>'
            f"&nbsp;&nbsp;&nbsp;&nbsp;"
            f'<span style="color:{mem_color};">MEM: {mem_text}</span>'
        )


class ServiceGrid(QWidget):
    def __init__(self) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setSpacing(24)
        self.cards: dict[str, ServiceCard] = {}
        columns = 4
        for index, (label, key) in enumerate(SERVICE_WIDGETS):
            card = ServiceCard(label.upper())
            self.cards[key] = card
            grid.addWidget(card, index // columns, index % columns)

    def set_containers(self, containers: list[dict[str, object]]) -> None:
        by_normalized = {
            _normalize_service_name(str(c.get("name", ""))): c for c in containers
        }
        for key, card in self.cards.items():
            match = next(
                (c for norm, c in by_normalized.items() if key in norm),
                None,
            )
            card.set_state(match) if match else card.set_offline()


class Dashboard(QMainWindow):
    def __init__(self, config: holocron.Config, host: str, agent_command: str) -> None:
        super().__init__(); self.config = config; self.host = host; self.agent_command = agent_command
        apply_theme_values(self._config_theme())
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="holocron-gui")
        self.snapshot_future: Future[dict[str, object]] | None = None
        self.weather_future: Future[tuple[int, dict[str, str]]] | None = None
        self.weather_generation = 0
        self.last_weather = 0.0; self.paused = False
        self.last_snapshot_submit = 0.0; self.force_refresh = True
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
        root = QWidget()
        self.setCentralWidget(root)
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
        self.services = ServiceGrid(); outer.addWidget(self.services, 35)

    def _style(self) -> None:
        base = max(12, min(32, int(self.config.gui_font_size)))
        panel_title = round(base * 1.19)
        metric = round(base * 1.25)
        main_title = round(base * 1.75)
        weather_temp = round(base * 2)
        self.setStyleSheet(f"""
            * {{ color:{GREEN}; font-family:'{FONT}','DejaVu Sans Mono',monospace; font-size:{base}px; }}
            QMainWindow, QWidget {{ background:{BLACK}; }}
            QFrame#panel {{ background:{PANEL}; border:1px solid {DIM}; }}
            QLabel#mainTitle {{ font-size:{main_title}px; font-weight:800; letter-spacing:2px; }}
            QLabel#panelTitle {{ border:0; font-size:{panel_title}px; font-weight:700; }}
            QLabel#metricValue {{ border:0; font-size:{metric}px; font-weight:700; }}
            QLabel#metricSecondary, QLabel#weatherText {{ border:0; font-size:{base + 2}px; }}
            QLabel#weatherTemp {{ border:0; font-size:{weather_temp}px; font-weight:700; }}
            QLabel#forecastLabel, QLabel#forecastLabelToday {{ border:0; font-size:{base}px; font-weight:700; }}
            QLabel#forecastLabelToday {{ color:{GREEN}; text-decoration: underline; }}
            QLabel#forecastCondition, QLabel#forecastRange {{ border:0; font-size:{round(base * 0.85) + 2}px; }}
            QProgressBar {{ background:#071009; border:1px solid {DIM}; height:12px; }}
            QProgressBar::chunk {{ background:{GREEN}; }}
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
    def _weather(location: str, generation: int) -> tuple[int, dict[str, object]]:
        client = holocron.WeatherClient(location)
        return generation, client.fetch()

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

    def tick(self) -> None:
        if self.snapshot_future and self.snapshot_future.done():
            try:
                self.apply_snapshot(self.snapshot_future.result())
            except Exception as exc:
                print(f"snapshot error: {exc}", file=sys.stderr)
            self.snapshot_future = None
        refresh_due = time.monotonic() - self.last_snapshot_submit >= max(1.0, self.config.refresh_seconds)
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

    def apply_snapshot(self, data: dict[str, object]) -> None:
        cpu = data.get("cpu", {}); ram = data.get("memory", {}); disk = data.get("disk", {}); net = data.get("network", {})
        temp = cpu.get("temperature")
        self.cpu.update_value(float(cpu.get("percent", 0)), f"{float(cpu.get('percent', 0)):.1f}%", f"Temp: {temp:.0f}°C" if isinstance(temp, (int, float)) else "Temp: —", threshold=True)
        self.ram.update_value(float(ram.get("percent", 0)), f"{float(ram.get('percent', 0)):.1f}%", f"{fmt_bytes(float(ram.get('used', 0)))} / {fmt_bytes(float(ram.get('total', 0)))}", threshold=True)
        self.disk.update_value(float(disk.get("percent", 0)), f"{float(disk.get('percent', 0)):.1f}%", f"{fmt_bytes(float(disk.get('used', 0)))} / {fmt_bytes(float(disk.get('total', 0)))}")
        rate = max(float(net.get("receive_rate", 0)), float(net.get("transmit_rate", 0)))
        self.net.update_value(min(100, rate / 1_250_000), f"↑ {fmt_rate(float(net.get('transmit_rate', 0)))}", f"↓ {fmt_rate(float(net.get('receive_rate', 0)))}")
        load = str(data.get("load", "—")).split()[0]; self.uptime.update_value(50, str(data.get("uptime", "—")), f"Load: {load}")
        self.services.set_containers(data.get("containers", []))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Q, Qt.Key.Key_Escape): self.close()
        elif event.key() == Qt.Key.Key_Space: self.paused = not self.paused
        elif event.key() == Qt.Key.Key_R:
            self.force_refresh = True
            if self.config.weather_enabled:
                self.refresh_weather()
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
    window = Dashboard(holocron.Config.from_file(args.config), args.host, args.agent_command)
    window.show() if args.windowed else window.showFullScreen()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
