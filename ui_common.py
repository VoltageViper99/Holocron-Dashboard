"""Shared state, layout constants, and safe drawing helpers for the UI."""

from __future__ import annotations

import curses
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import holocron


MINIMUM_WIDTH = 60
DASHBOARD_MINIMUM_HEIGHT = 18
ARCHIVES_MINIMUM_HEIGHT = 10
HELP_TEXT = (
    " [A] Dashboard/Archives  [N]ext [P]rev  [Space] Pause  "
    "[R]efresh  [S]peak  [Q]uit "
)
TEMPERATURE_LABELS = ("CPU", "NVMe", "System", "Hotspot")
SETTINGS_CATEGORIES = (
    "Dashboard",
    "Monitoring",
    "Appearance",
    "Startup",
    "Audio",
    "Integrations",
    "About",
)

DASHBOARD_SETTINGS = [
    {
        "key": "refresh_interval",
        "label": "Refresh Interval: ",
        "type": "number",
        "minimum": 1,
        "maximum": 60,
        "suffix": "seconds",
    },
    {
        "key": "default_screen",
        "label": "Default Screen: ",
        "type": "choice",
        "choices": ["dashboard", "archives"],
    },
    {
        "key": "show_system_metrics",
        "label": "Show System Metrics: ",
        "type": "boolean",
    },
    {
        "key": "show_weather",
        "label": "Show Weather: ",
        "type": "boolean",
    },
    {
        "key": "pause_logs_on_startup",
        "label": "Pause Logs On Startup: ",
        "type": "boolean",
    },
]

@dataclass
class UIState:
    """Mutable state owned by the terminal interface."""

    dashboard_mode: bool
    paused: bool = False
    container_index: int = 0
    last_rotation: float = field(default_factory=time.monotonic)
    last_log_fetch: float = 0.0
    last_state_fetch: float = 0.0
    cached_logs: list[str] = field(default_factory=list)
    cached_journal: list[str] = field(default_factory=list)
    last_journal_fetch: float = 0.0
    containers: list[holocron.ContainerState] = field(default_factory=list)
    status_message: str = ""
    status_expiry: float = 0.0
    weather_forecast: list[str] = field(default_factory=lambda: ["Loading forecast…"])
    last_weather_fetch: float | None = None
    update_count: int | None = None
    last_update_fetch: float | None = None


def progress_bar(value: float, cells: int = 12) -> str:
    value = max(0.0, min(100.0, value))
    filled = round(cells * value / 100.0)
    return "█" * filled + "░" * (cells - filled)


def format_byte_rate(bytes_per_second: float) -> str:
    value = max(0.0, bytes_per_second)
    units = ("B/s", "KB/s", "MB/s", "GB/s")
    for unit in units[:-1]:
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"


def format_bytes(byte_count: float) -> str:
    value = max(0.0, byte_count)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units[:-1]:
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"


def panel_metric(label: str, percent: float, display_value: str, width: int) -> str:
    bar_cells = max(1, min(12, width - len(display_value) - 9))
    return f"{label:<5} [{progress_bar(percent, bar_cells)}] {display_value}"


def dashboard_panel_layout(height: int, width: int) -> tuple[int, int]:
    return round(width * 0.60), max(10, int(height * 0.56))


def trim(text: str, width: int) -> str:
    if width <= 0:
        return ""
    clean = text.replace("\t", "    ")
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 1)] + "…"


def safe_addstr(window: "curses._CursesWindow", y: int, x: int, text: str, attr: int = 0, max_width: int | None = None) -> None:
    """Draw clipped text and ignore curses' bottom-right corner error."""
    try:
        height, width = window.getmaxyx()
        if 0 <= y < height and 0 <= x < width:
            available_width = width - x - 1
            if max_width is not None:
                available_width = min(available_width, max_width)
            window.addstr(y, x, trim(text, available_width), attr)
    except curses.error:
        pass


def draw_box_line(window: "curses._CursesWindow", y: int, width: int, char: str = "─") -> None:
    safe_addstr(window, y, 0, char * max(0, width - 1), curses.color_pair(2))


def state_attr(state: "holocron.ContainerState") -> int:
    if state.health == "UNHEALTHY":
        return curses.color_pair(5) | curses.A_BOLD
    if state.running:
        return curses.color_pair(1) | curses.A_BOLD
    return curses.color_pair(4)


def log_attr(level: str) -> int:
    if level in ("FATAL", "ERROR"):
        return curses.color_pair(5) | curses.A_BOLD
    if level == "WARN":
        return curses.color_pair(4)
    if level == "SUCCESS":
        return curses.color_pair(6) | curses.A_BOLD
    return curses.color_pair(1)
