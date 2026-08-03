"""Curses user interface for Holocron.

Keeping this module separate makes the application services in ``holocron``
usable without initializing (or even importing) curses.
"""

from __future__ import annotations

import curses
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import ModuleType
from typing import TYPE_CHECKING

from ui_common import (
    ARCHIVES_MINIMUM_HEIGHT,
    DASHBOARD_MINIMUM_HEIGHT,
    DASHBOARD_SETTINGS,
    HELP_TEXT,
    MINIMUM_WIDTH,
    SETTINGS_CATEGORIES,
    TEMPERATURE_LABELS,
    UIState,
    dashboard_panel_layout,
    format_byte_rate,
    format_bytes,
    log_attr,
    panel_metric,
    progress_bar,
    safe_addstr,
    state_attr,
    trim,
)
from ui_settings import draw_dashboard_settings, draw_settings_page
from ui_menu import run_menu as run_main_menu

if TYPE_CHECKING:
    import holocron


def draw_box_line(
    window: "curses._CursesWindow",
    y: int,
    width: int,
    char: str = "─",
) -> None:
    """Draw a divider through this module's patchable safe drawing helper."""
    safe_addstr(window, y, 0, char * max(0, width - 1), curses.color_pair(2))


class HolocronUI:
    """Owns the terminal event loop, rendering, and interaction state."""

    def __init__(
        self,
        screen: "curses._CursesWindow",
        config: holocron.Config,
        services: ModuleType,
        *,
        start_in_settings: bool = False,
    ) -> None:
        self.screen = screen
        self.config = config
        self.services = services
        self.client = services.DockerClient()
        self.logger = services.setup_app_logger(config)
        self.cpu = services.CpuSampler()
        self.network = services.NetworkSampler()
        self.state = UIState(dashboard_mode=config.dashboard_mode)
        self.journal_client = (
            services.JournalClient(config.journal_priority)
            if config.journal_enabled
            else None
        )
        self.weather_client = (
            services.WeatherClient(config.weather_location)
            if config.weather_enabled
            else None
        )
        self.weather_executor = (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="holocron-weather",
            )
            if self.weather_client is not None
            else None
        )
        self.weather_future: Future[list[str]] | None = None
        self.update_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="holocron-updates",
        )
        self.update_future: Future[int | None] | None = None
        self.settings_active = start_in_settings
        self.settings_index = 0

        self.dashboard_settings = {
            "refresh_interval": max(1, round(config.refresh_seconds)),
            "default_screen": (
                "dashboard" if config.dashboard_mode else "archives"
            ),
            "show_system_metrics": True,
            "show_weather": config.weather_enabled,
            "pause_logs_on_startup": False,
        }

        self.settings_page = "categories"
        self.dashboard_setting_index = 0

    def run(self) -> None:
        self.logger.info("Holocron v%s started", self.services.VERSION)
        self.services.play_startup(self.config)
        self.logger.info("Startup announcement requested")

        try:
            while True:
                now = time.monotonic()
                self._refresh_weather(now)
                self._refresh_update_count(now)
                self._refresh_container_states(now)
                self._refresh_journal(now)
                running = self._running_container_names()
                current = self._select_current(running)
                current = self._rotate_if_due(now, running, current)
                self._refresh_logs(now, running, current)

                self.screen.erase()
                height, width = self.screen.getmaxyx()
                if self._terminal_too_small(height, width):
                    self._draw_resize_message(height)
                    if self.screen.getch() in (ord("q"), ord("Q")):
                        break
                    continue

                self._draw(now, current, running, height, width)
                self.screen.refresh()
                key = self.screen.getch()
                if self.settings_active:
                    self._handle_settings_key(key, now)
                    continue
                if self._handle_key(key, now, running, current):
                    break
        finally:
            if self.weather_executor is not None:
                self.weather_executor.shutdown(
                    wait=False,
                    cancel_futures=True,
                )
            self.update_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )
            self.logger.info("Holocron stopped")

    def _refresh_weather(self, now: float) -> None:
        if self.weather_client is None or self.weather_executor is None:
            return

        if self.weather_future is not None:
            if not self.weather_future.done():
                return
            try:
                self.state.weather_forecast = self.weather_future.result()
            except Exception as exc:
                self.state.weather_forecast = ["Forecast unavailable"]
                self.logger.warning("Could not refresh weather: %s", exc)
            self.state.last_weather_fetch = now
            self.weather_future = None

        refresh_seconds = max(60, self.config.weather_refresh_seconds)
        last_fetch = self.state.last_weather_fetch
        if last_fetch is None or now - last_fetch >= refresh_seconds:
            self.weather_future = self.weather_executor.submit(
                self.weather_client.forecast
            )

    def _refresh_update_count(self, now: float) -> None:
        if self.update_future is not None:
            if not self.update_future.done():
                return
            try:
                self.state.update_count = self.update_future.result()
            except Exception as exc:
                self.state.update_count = None
                self.logger.warning("Could not check package updates: %s", exc)
            self.state.last_update_fetch = now
            self.update_future = None

        refresh_seconds = max(60, self.config.update_refresh_seconds)
        last_fetch = self.state.last_update_fetch
        if last_fetch is None or now - last_fetch >= refresh_seconds:
            self.update_future = self.update_executor.submit(
                self.services.package_update_count
            )

    def _refresh_container_states(self, now: float) -> None:
        state = self.state
        interval = max(2.0, self.config.refresh_seconds)
        if now - state.last_state_fetch < interval:
            return

        try:
            containers = self.client.container_states(
                include_stopped=self.config.show_all_containers
            )
            if self.config.containers:
                allowed = set(self.config.containers)
                containers = [
                    container
                    for container in containers
                    if container.name in allowed
                ]
            state.containers = containers
        except Exception as exc:
            state.containers = []
            self._show_status(str(exc), now, seconds=10)
        state.last_state_fetch = now

    def _running_container_names(self) -> list[str]:
        return [
            container.name
            for container in self.state.containers
            if container.running
        ]

    def _select_current(self, containers: list[str]) -> str:
        if not containers:
            self.state.container_index = 0
            return "NO RUNNING CONTAINERS"
        self.state.container_index %= len(containers)
        return containers[self.state.container_index]

    def _rotate_if_due(
        self,
        now: float,
        containers: list[str],
        current: str,
    ) -> str:
        state = self.state
        rotation_due = now - state.last_rotation >= self.config.rotation_seconds
        if not containers or state.paused or not rotation_due:
            return current

        state.container_index = (state.container_index + 1) % len(containers)
        current = containers[state.container_index]
        state.cached_logs = []
        state.last_rotation = now
        state.last_log_fetch = 0.0
        self.logger.info("Rotated to container: %s", current)
        return current

    def _refresh_logs(
        self,
        now: float,
        containers: list[str],
        current: str,
    ) -> None:
        state = self.state
        if (
            not containers
            or now - state.last_log_fetch < self.config.refresh_seconds
        ):
            return
        try:
            state.cached_logs = self.client.logs(
                current,
                self.config.tail_lines,
                self.config.show_timestamps,
            )
        except Exception as exc:
            state.cached_logs = [f"[error] {exc}"]
        state.last_log_fetch = now

    def _refresh_journal(self, now: float) -> None:
        """Refresh host events independently from the active Docker log."""
        if self.journal_client is None:
            return
        interval = max(1.0, self.config.journal_refresh_seconds)
        if now - self.state.last_journal_fetch < interval:
            return
        self.state.cached_journal = self.journal_client.logs(
            self.config.journal_tail_lines
        )
        self.state.last_journal_fetch = now

    def _terminal_too_small(self, height: int, width: int) -> bool:
        minimum_height = (
            DASHBOARD_MINIMUM_HEIGHT
            if self.state.dashboard_mode
            else ARCHIVES_MINIMUM_HEIGHT
        )
        return height < minimum_height or width < MINIMUM_WIDTH

    def _draw_resize_message(self, height: int) -> None:
        minimum_height = (
            DASHBOARD_MINIMUM_HEIGHT
            if self.state.dashboard_mode
            else ARCHIVES_MINIMUM_HEIGHT
        )
        safe_addstr(
            self.screen,
            0,
            0,
            f"Terminal too small. Resize to at least "
            f"{MINIMUM_WIDTH}x{minimum_height}.",
            curses.color_pair(5),
        )
        self.screen.refresh()
    def _draw(
        self,
        now: float,
        current: str,
        running: list[str],
        height: int,
        width: int,
    ) -> None:
        if self.settings_active:
            if self.settings_page == "dashboard":
                draw_dashboard_settings(
                    self.screen,
                    DASHBOARD_SETTINGS,
                    self.dashboard_settings,
                    self.dashboard_setting_index,
                )
            else:
                draw_settings_page(self.screen, self.settings_index)
            return

        self.draw_header(width)

        log_panel_top: int | None = None

        if self.state.dashboard_mode:
            divider_x, log_panel_top = dashboard_panel_layout(
                height,
                width,
            )

            self.draw_system_panel(
                now,
                current,
                panel_left=1,
                panel_right=divider_x,
                panel_top=2,
                panel_bottom=log_panel_top,
            )

            self.draw_container_panel(
                now=now,
                current=current,
                panel_left=divider_x,
                panel_right=width,
                panel_top=2,
                panel_bottom=log_panel_top,
            )

        self.draw_log_panel(
            current,
            running,
            height,
            width,
            panel_top=log_panel_top,
        )
        self._draw_footer(now, height, width)

    def draw_header(self, width: int) -> None:
        """Draw node identity, the control-matrix title, clock, and uptime."""
        identity = f" {self.services.system_identity()} "
        title = f"◆ {self.config.title} · {self.services.APP_NAME} ◆"
        right_header = (
            f"{time.strftime('%H:%M:%S')} │ "
            f"UPTIME {self.services.system_uptime()} "
        )
        right_x = max(1, width - len(right_header) - 1)
        title_x = max(1, (width - len(title)) // 2)

        safe_addstr(
            self.screen,
            0,
            1,
            identity,
            curses.color_pair(6),
            max_width=max(1, title_x - 2),
        )
        safe_addstr(
            self.screen,
            0,
            title_x,
            title,
            curses.A_BOLD | curses.color_pair(1),
            max_width=max(1, right_x - title_x - 1),
        )
        safe_addstr(
            self.screen,
            0,
            right_x,
            right_header,
            curses.A_BOLD | curses.color_pair(1),
        )
        draw_box_line(self.screen, 1, width)

    def draw_system_panel(
        self,
        now: float,
        current: str,
        panel_left: int,
        panel_right: int,
        panel_top: int,
        panel_bottom: int,
    ) -> None:
        """Draw system and monitor details in the left 60% panel."""
        safe_addstr(
            self.screen,
            panel_top,
            panel_left,
            "CORE SYSTEMS",
            curses.A_BOLD | curses.color_pair(6),
        )
        panel_width = panel_right - panel_left - 1
        column_gap = 2
        system_width = max(1, (panel_width - column_gap) // 2)
        network_x = panel_left + system_width + column_gap
        network_width = max(1, panel_right - network_x - 1)
        safe_addstr(
            self.screen,
            panel_top,
            network_x,
            "DATA LINK",
            curses.A_BOLD | curses.color_pair(6),
            max_width=network_width,
        )

        cpu_percent = self.cpu.percent()
        memory_percent = self.services.memory_percent()
        disk_percent = self.services.disk_percent()
        metrics = (
            ("CPU", cpu_percent),
            ("RAM", memory_percent),
            ("DISK", disk_percent),
        )
        for row, (label, value) in enumerate(metrics, start=panel_top + 1):
            if row >= panel_bottom:
                break
            safe_addstr(
                self.screen,
                row,
                panel_left,
                panel_metric(
                    label,
                    value,
                    f"{value:5.1f}%",
                    system_width,
                ),
                curses.color_pair(1),
                max_width=system_width,
            )

        network = self.network.sample()
        network_metrics = (
            (
                "DOWN",
                network.receive_percent,
                format_byte_rate(network.receive_bytes_per_second),
            ),
            (
                "UP",
                network.transmit_percent,
                format_byte_rate(network.transmit_bytes_per_second),
            ),
        )
        for row, (label, percent, rate) in enumerate(
            network_metrics,
            start=panel_top + 1,
        ):
            if row >= panel_bottom:
                break
            safe_addstr(
                self.screen,
                row,
                network_x,
                panel_metric(label, percent, rate, network_width),
                curses.color_pair(1),
                max_width=network_width,
            )
        temperatures = self.services.temperature_summary()

        details_row = panel_top + 4
        if details_row < panel_bottom:
            safe_addstr(
                self.screen,
                details_row,
                panel_left,
                f"LOAD  {self.services.load_average()}",
                curses.color_pair(1),
                max_width=system_width,
            )

        for offset, (label, reading) in enumerate(
            zip(TEMPERATURE_LABELS, temperatures),
            start=1,
        ):
            row = details_row + offset

            if row >= panel_bottom:
                break

            safe_addstr(
                self.screen,
                row,
                panel_left,
                f"{label:<14} {reading.celsius:5.1f}°C",
                curses.color_pair(1),
                max_width=system_width,
            )

        self._draw_filesystems(
            x=panel_left,
            width=system_width,
            row=details_row + len(temperatures) + 2,
            panel_bottom=panel_bottom - 3,
        )

        self._draw_system_alerts(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            temperatures=temperatures,
            x=panel_left,
            panel_right=network_x - column_gap,
            panel_bottom=panel_bottom,
        )

        self._draw_update_count(
            x=network_x,
            panel_right=panel_right,
            row=panel_top + 4,
            forecast_top=panel_bottom - 4,
        )

        self._draw_weather_forecast(
            x=network_x,
            panel_right=panel_right,
            panel_bottom=panel_bottom,
        )

    def _draw_filesystems(
        self,
        *,
        x: int,
        width: int,
        row: int,
        panel_bottom: int,
    ) -> None:
        """Draw physical storage mounts in otherwise unused panel space."""
        if row >= panel_bottom:
            return
        safe_addstr(
            self.screen,
            row,
            x,
            "FILESYSTEMS",
            curses.A_BOLD | curses.color_pair(6),
            max_width=width,
        )
        for offset, filesystem in enumerate(
            self.services.filesystem_usage(),
            start=1,
        ):
            target_row = row + offset
            if target_row >= panel_bottom:
                break
            label = "ROOT" if filesystem.mount == "/" else filesystem.mount
            free = f"{format_bytes(filesystem.free_bytes)} FREE"
            text = panel_metric(
                trim(label.upper(), 8),
                filesystem.used_percent,
                free,
                width,
            )
            attr = (
                curses.A_BOLD | curses.color_pair(5)
                if filesystem.used_percent >= 90
                else curses.color_pair(1)
            )
            safe_addstr(
                self.screen,
                target_row,
                x,
                text,
                attr,
                max_width=width,
            )

    def _draw_system_alerts(
        self,
        cpu_percent: float,
        memory_percent: float,
        disk_percent: float,
        temperatures: list[holocron.TemperatureReading],
        x: int,
        panel_right: int,
        panel_bottom: int,
    ) -> None:
        """Draw threshold warnings at the system panel's bottom left."""
        alerts: list[str] = []
        if cpu_percent >= 90:
            alerts.append(f"CPU HIGH {cpu_percent:.0f}%")
        if memory_percent >= 90:
            alerts.append(f"RAM HIGH {memory_percent:.0f}%")
        if disk_percent >= 90:
            alerts.append(f"DISK LOW {100 - disk_percent:.0f}% FREE")
        hottest = max(
            (reading.celsius for reading in temperatures),
            default=None,
        )
        if hottest is not None and hottest >= 85:
            alerts.append(f"TEMP HIGH {hottest:.0f}°C")
        if not alerts:
            alerts.append("ALL SYSTEMS NOMINAL")

        width = max(1, panel_right - x)
        heading_row = panel_bottom - min(3, len(alerts) + 1)
        safe_addstr(
            self.screen,
            heading_row,
            x,
            "SYSTEM ALERTS",
            curses.A_BOLD | curses.color_pair(6),
            max_width=width,
        )
        alert_attr = (
            curses.color_pair(1)
            if alerts == ["ALL SYSTEMS NOMINAL"]
            else curses.A_BOLD | curses.color_pair(5)
        )
        for row, alert in enumerate(alerts[:2], start=heading_row + 1):
            if row >= panel_bottom:
                break
            safe_addstr(
                self.screen,
                row,
                x,
                f"● {alert}",
                alert_attr,
                max_width=width,
            )

    def _draw_update_count(
        self,
        x: int,
        panel_right: int,
        row: int,
        forecast_top: int,
    ) -> None:
        """Draw pending package updates between network and forecast."""
        if row + 1 >= forecast_top:
            return
        width = max(1, panel_right - x - 1)
        count = self.state.update_count
        text = "CHECKING…" if count is None else f"{count} PENDING"
        attr = (
            curses.color_pair(1)
            if count in (None, 0)
            else curses.A_BOLD | curses.color_pair(4)
        )
        safe_addstr(
            self.screen,
            row,
            x,
            "SYSTEM UPDATES",
            curses.A_BOLD | curses.color_pair(6),
            max_width=width,
        )
        safe_addstr(
            self.screen,
            row + 1,
            x,
            text,
            attr,
            max_width=width,
        )

    def _draw_weather_forecast(
        self,
        x: int,
        panel_right: int,
        panel_bottom: int,
    ) -> None:
        """Draw the three-day forecast at the system panel's bottom right."""
        if not self.config.weather_enabled:
            return

        width = max(1, panel_right - x - 1)
        heading_row = panel_bottom - 4
        safe_addstr(
            self.screen,
            heading_row,
            x,
            "3 DAY FORECAST",
            curses.A_BOLD | curses.color_pair(6),
            max_width=width,
        )
        for row, forecast in enumerate(
            self.state.weather_forecast[:3],
            start=heading_row + 1,
        ):
            if row >= panel_bottom:
                break
            safe_addstr(
                self.screen,
                row,
                x,
                forecast,
                curses.color_pair(1),
                max_width=width,
            )

    def draw_container_panel(
        self,
        now: float,
        current: str,
        panel_left: int,
        panel_right: int,
        panel_top: int,
        panel_bottom: int,
    ) -> None:
        """Draw Docker services in the right 40% panel."""
        for row in range(panel_top, panel_bottom):
            safe_addstr(
                self.screen,
                row,
                panel_left,
                "│",
                curses.color_pair(2),
            )

        content_left = panel_left + 2
        safe_addstr(
            self.screen,
            panel_top,
            content_left,
            "CONTAINER MATRIX",
            curses.A_BOLD | curses.color_pair(6),
            max_width=panel_right - content_left - 1,
        )
        content_width = panel_right - content_left - 1
        summary_right = panel_right
        monitor_x: int | None = None
        if content_width >= 62:
            monitor_x = content_left + content_width // 2
            summary_right = monitor_x - 1

        self._draw_container_summary(
            row=panel_top + 1,
            x=content_left,
            panel_right=summary_right,
        )
        if monitor_x is not None:
            self._draw_monitor(
                now,
                current,
                x=monitor_x,
                panel_right=panel_right,
                panel_top=panel_top,
                panel_bottom=min(panel_bottom, panel_top + 5),
            )
        safe_addstr(
            self.screen,
            panel_top + 5,
            content_left,
            "SERVICES",
            curses.A_BOLD | curses.color_pair(6),
            max_width=panel_right - content_left - 1,
        )
        self._draw_services(
            current=current,
            panel_left=content_left,
            panel_right=panel_right,
            panel_top=panel_top + 6,
            panel_bottom=panel_bottom,
        )

    def _draw_container_summary(
        self,
        row: int,
        x: int,
        panel_right: int,
    ) -> None:
        states = self.state.containers
        running = sum(item.running for item in states)
        healthy = sum(
            item.health in ("HEALTHY", "RUNNING")
            for item in states
        )
        unhealthy = sum(item.health == "UNHEALTHY" for item in states)
        stopped = len(states) - running

        summary_attr = curses.color_pair(5) if unhealthy else curses.color_pair(1)
        safe_addstr(
            self.screen,
            row,
            x,
            f"TOTAL       {len(states):>3}  RUNNING {running:>3}",
            summary_attr,
            max_width=panel_right - x - 1,
        )
        safe_addstr(
            self.screen,
            row + 1,
            x,
            f"HEALTHY     {healthy:>3}  UNHEALTHY {unhealthy:>3}",
            summary_attr,
            max_width=panel_right - x - 1,
        )
        safe_addstr(
            self.screen,
            row + 2,
            x,
            f"STOPPED     {stopped:>3}",
            summary_attr,
            max_width=panel_right - x - 1,
        )

    def _draw_monitor(
        self,
        now: float,
        current: str,
        x: int,
        panel_right: int,
        panel_top: int,
        panel_bottom: int,
    ) -> None:
        state = self.state
        remaining = max(
            0,
            int(self.config.rotation_seconds - (now - state.last_rotation)),
        )
        safe_addstr(
            self.screen,
            panel_top,
            x,
            "MONITOR",
            curses.A_BOLD | curses.color_pair(6),
            max_width=panel_right - x - 1,
        )
        mode = "PAUSED" if state.paused else "ROTATING"
        mode_attr = curses.color_pair(4) if state.paused else curses.color_pair(1)
        rows = (
            (f"ACTIVE      {current}", curses.color_pair(1)),
            (f"MODE        {mode}", mode_attr),
            (f"NEXT        {remaining:>2}s", curses.color_pair(1)),
            (
                f"REFRESH     {self.config.refresh_seconds:g}s",
                curses.color_pair(1),
            ),
        )
        for row, (text, attr) in enumerate(rows, start=panel_top + 1):
            if row >= panel_bottom:
                break
            safe_addstr(
                self.screen,
                row,
                x,
                text,
                attr,
                max_width=panel_right - x - 1,
            )

    def _draw_services(
        self,
        current: str,
        panel_left: int,
        panel_right: int,
        panel_top: int,
        panel_bottom: int,
    ) -> None:
        service_rows = max(0, panel_bottom - panel_top)
        panel_width = panel_right - panel_left
        service_columns = 2 if panel_width >= 60 else 1
        cell_width = max(20, panel_width // service_columns)
        visible_count = service_rows * service_columns
        for index, container in enumerate(
            self.state.containers[:visible_count]
        ):
            row = panel_top + (index // service_columns)
            x = panel_left + (index % service_columns) * cell_width
            marker = "▶" if container.name == current else (
                "●" if container.running else "■"
            )
            label = f"{marker} {container.name:<18} {container.health}"
            attr = state_attr(container)
            if container.name == current:
                attr |= curses.A_REVERSE
            safe_addstr(
                self.screen,
                row,
                x,
                label,
                attr,
                max_width=min(cell_width - 1, panel_right - x - 1),
            )

    def draw_log_panel(
        self,
        current: str,
        running: list[str],
        height: int,
        width: int,
        panel_top: int | None = None,
    ) -> None:
        """Draw Docker logs and, in dashboard mode, the host event stream."""
        position = self._position(running)
        if self.state.dashboard_mode:
            if panel_top is None:
                panel_top = max(10, int(height * 0.56))
            draw_box_line(self.screen, panel_top, width)
            split_x = round(width * 0.58)
            for row in range(panel_top + 1, height - 2):
                safe_addstr(
                    self.screen,
                    row,
                    split_x,
                    "│",
                    curses.color_pair(2),
                )
            safe_addstr(
                self.screen,
                panel_top + 1,
                1,
                f"DOCKER TELEMETRY │ {current} │ {position}",
                curses.A_BOLD | curses.color_pair(2),
                max_width=split_x - 2,
            )
            body_top = panel_top + 2
            body_height = max(1, (height - 3) - body_top)
            self._draw_log_lines(
                self.state.cached_logs,
                body_top,
                body_height,
                x=1,
                width=split_x - 2,
            )
            self.draw_journal_panel(
                panel_left=split_x,
                panel_top=panel_top,
                height=height,
                width=width,
            )
            return
        else:
            mode = "PAUSED" if self.state.paused else "ROTATING"
            safe_addstr(
                self.screen,
                2,
                1,
                f"ARCHIVES │ {current} │ {position} │ {mode}",
                curses.A_BOLD | curses.color_pair(2),
            )
            draw_box_line(self.screen, 3, width)
            body_top = 4

        body_height = max(1, (height - 3) - body_top)
        self._draw_log_lines(
            self.state.cached_logs,
            body_top,
            body_height,
            x=1,
            width=width - 2,
        )

    def _draw_log_lines(
        self,
        lines: list[str],
        body_top: int,
        body_height: int,
        *,
        x: int,
        width: int,
    ) -> None:
        """Render a clipped tail of normalized log entries."""
        visible = lines[-body_height:]
        for row, line in enumerate(visible, start=body_top):
            entry = self.services.parse_log_line(
                line,
                simplify=self.config.simplify_messages,
            )
            safe_addstr(
                self.screen,
                row,
                x,
                self.services.format_log_entry(entry, width),
                log_attr(entry.level),
                max_width=width,
            )

    def draw_journal_panel(
        self,
        panel_left: int,
        panel_top: int,
        height: int,
        width: int,
    ) -> None:
        """Draw recent host journal events beside the Docker stream."""
        x = panel_left + 2
        content_width = max(1, width - x - 1)
        safe_addstr(
            self.screen,
            panel_top + 1,
            x,
            "SYSTEM EVENT STREAM │ JOURNALCTL",
            curses.A_BOLD | curses.color_pair(2),
            max_width=content_width,
        )
        body_top = panel_top + 2
        body_height = max(1, (height - 3) - body_top)
        lines = self.state.cached_journal
        if self.journal_client is None:
            lines = ["System journal stream disabled"]
        elif not lines:
            lines = ["Waiting for system events…"]
        self._draw_log_lines(
            lines,
            body_top,
            body_height,
            x=x,
            width=content_width,
        )

    def _draw_footer(self, now: float, height: int, width: int) -> None:
        draw_box_line(self.screen, height - 2, width)
        help_text = HELP_TEXT
        if self.state.status_message and now < self.state.status_expiry:
            help_text = f" {self.state.status_message} "
        safe_addstr(
            self.screen,
            height - 1,
            0,
            help_text,
            curses.color_pair(1),
        )

    def _position(self, containers: list[str]) -> str:
        if not containers:
            return "0/0"
        return f"{self.state.container_index + 1}/{len(containers)}"
    def _handle_key(
            self,
            key: int,
            now: float,
            containers: list[str],
            current: str,
        ) -> bool:
            state = self.state
            if key in (ord("q"), ord("Q")):
                return True
            if key in (ord("a"), ord("A")):
                state.dashboard_mode = not state.dashboard_mode
                view = "dashboard" if state.dashboard_mode else "archives"
                self.logger.info("View changed to %s", view)
            elif key in (ord("n"), ord("N")) and containers:
                state.container_index = (state.container_index + 1) % len(containers)
                self._reset_rotation(now)
                self.logger.info(
                    "Selected next container: %s",
                    containers[state.container_index],
                )
            elif key in (ord("p"), ord("P")) and containers:
                state.container_index = (state.container_index - 1) % len(containers)
                self._reset_rotation(now)
                self.logger.info(
                    "Selected previous container: %s",
                    containers[state.container_index],
                )
            elif key == ord(" "):
                state.paused = not state.paused
                state.last_rotation = now
                mode = "paused" if state.paused else "resumed"
                self.logger.info("Rotation %s", mode)
            elif key in (ord("r"), ord("R")):
                state.last_log_fetch = 0.0
                state.last_state_fetch = 0.0
                self._show_status("Dashboard refreshed", now)
                self.logger.info("Manual refresh requested for %s", current)
            elif key in (ord("s"), ord("S")):
                self.services.play_startup(self.config)
                self._show_status("Startup announcement replayed", now)
                self.logger.info("Startup announcement replayed")
            return False

    def _handle_settings_key(
        self,
        key: int,
        now: float,
    ) -> bool:
        if self.settings_page == "dashboard":
            if key in (ord("q"), ord("Q"), 27):
                self.settings_page = "categories"
                return False

            self.handle_dashboard_settings_key(key)
            return False

        if key in (
            ord("q"),
            ord("Q"),
            27,
            curses.KEY_BACKSPACE,
        ):
            self.settings_active = False
            self._show_status("Settings closed", now)
            self.logger.info("Settings closed")
            return True

        category_count = len(SETTINGS_CATEGORIES)
        if key in (
            curses.KEY_UP,
            ord("k"),
            ord("K"),
        ):
            self.settings_index = (
                self.settings_index - 1
            ) % category_count

        elif key in (
            curses.KEY_DOWN,
            ord("j"),
            ord("J"),
        ):
            self.settings_index = (
                self.settings_index + 1
            ) % category_count

        elif key in (curses.KEY_ENTER, 10, 13):
            category = SETTINGS_CATEGORIES[self.settings_index]

            if category == "Dashboard":
                self.settings_page = "dashboard"
                self.dashboard_setting_index = 0

            else:
                self._show_status(
                    f"{category} settings are not available yet",
                    now,
                )

        return False

    def handle_dashboard_settings_key(self, key: int) -> None:
        """Handle keyboard input for Dashboard settings."""

        setting = DASHBOARD_SETTINGS[self.dashboard_setting_index]
        setting_key = setting["key"]
        setting_type = setting["type"]

        if key in (curses.KEY_UP, ord("k"), ord("K")):
            self.dashboard_setting_index = (
                self.dashboard_setting_index - 1
            ) % len(DASHBOARD_SETTINGS)

        elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
            self.dashboard_setting_index = (
                self.dashboard_setting_index + 1
            ) % len(DASHBOARD_SETTINGS)

        elif key in (
            curses.KEY_LEFT,
            ord("h"),
            ord("H"),
            curses.KEY_RIGHT,
            ord("l"),
            ord("L"),
            10,
            13,
            ord(" "),
        ):
            if setting_type == "boolean":
                current = self.dashboard_settings[setting_key]
                self.dashboard_settings[setting_key] = not current

            elif setting_type == "choice":
                choices = setting["choices"]
                current = self.dashboard_settings[setting_key]
                current_index = choices.index(current)

                direction = -1 if key in (curses.KEY_LEFT, ord("h"), ord("H")) else 1
                new_index = (current_index + direction) % len(choices)

                self.dashboard_settings[setting_key] = choices[new_index]

            elif setting_type == "number":
                current = self.dashboard_settings[setting_key]

                direction = -1 if key in (curses.KEY_LEFT, ord("h"), ord("H")) else 1
                new_value = current + direction

                minimum = setting["minimum"]
                maximum = setting["maximum"]

                self.dashboard_settings[setting_key] = max(
                    minimum,
                    min(maximum, new_value),
                )

    def _reset_rotation(self, now: float) -> None:
        self.state.last_rotation = now
        self.state.last_log_fetch = 0.0

    def _show_status(
        self,
        message: str,
        now: float,
        seconds: float = 2.0,
    ) -> None:
        self.state.status_message = message
        self.state.status_expiry = now + seconds


def configure_curses(screen: "curses._CursesWindow", refresh_seconds: float) -> None:
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_WHITE, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_RED, -1)
    curses.init_pair(6, curses.COLOR_CYAN, -1)
    curses.init_pair(7, curses.COLOR_MAGENTA, -1)
    screen.nodelay(True)
    screen.timeout(max(50, int(refresh_seconds * 1000)))

def run_ui(
    screen: "curses._CursesWindow",
    config: holocron.Config,
    services: ModuleType,
) -> None:
    configure_curses(screen, config.refresh_seconds)
    HolocronUI(screen, config, services).run()

MENU_ITEMS = (
    "Dashboard",
    "Docker",
    "Settings",
    "Exit",
)


def draw_menu(
    screen: "curses._CursesWindow",
    selected: int,
) -> None:
    screen.erase()
    height, width = screen.getmaxyx()

    title = "HOLOCRON CORE"
    subtitle = "JEDI ARCHIVES CONTROL TERMINAL"

    safe_addstr(
        screen,
        2,
        max(0, (width - len(title)) // 2),
        title,
        curses.color_pair(1) | curses.A_BOLD,
    )

    safe_addstr(
        screen,
        4,
        max(0, (width - len(subtitle)) // 2),
        subtitle,
        curses.color_pair(3),
    )

    menu_width = max(len(item) for item in MENU_ITEMS) + 8
    start_y = max(7, (height - len(MENU_ITEMS)) // 2)

    for index, item in enumerate(MENU_ITEMS):
        marker = ">" if index == selected else " "
        text = f"{marker}  {item.upper()}"

        attr = curses.color_pair(1)

        if index == selected:
            attr |= curses.A_BOLD | curses.A_REVERSE

        safe_addstr(
            screen,
            start_y + index * 2,
            max(0, (width - menu_width) // 2),
            text.ljust(menu_width),
            attr,
        )

    help_text = "↑/↓ Navigate   Enter Select   Q Exit"

    safe_addstr(
        screen,
        height - 2,
        max(0, (width - len(help_text)) // 2),
        help_text,
        curses.color_pair(3),
    )

    screen.refresh()


def show_placeholder(
    screen: "curses._CursesWindow",
    title: str,
) -> None:
    screen.nodelay(False)
    screen.erase()

    height, width = screen.getmaxyx()

    heading = title.upper()
    message = "This module is not yet available."
    return_text = "Press any key to return to the main menu."

    safe_addstr(
        screen,
        max(1, height // 2 - 2),
        max(0, (width - len(heading)) // 2),
        heading,
        curses.color_pair(1) | curses.A_BOLD,
    )

    safe_addstr(
        screen,
        height // 2,
        max(0, (width - len(message)) // 2),
        message,
        curses.color_pair(3),
    )

    safe_addstr(
        screen,
        height // 2 + 2,
        max(0, (width - len(return_text)) // 2),
        return_text,
        curses.color_pair(3),
    )

    screen.refresh()
    screen.getch()

    screen.nodelay(True)
    screen.timeout(100)


def run_menu(
    screen: "curses._CursesWindow",
    config: holocron.Config,
    services: ModuleType,
) -> None:
    configure_curses(screen, 0.1)

    selected = 0

    while True:
        draw_menu(screen, selected)
        key = screen.getch()

        if key == -1:
            continue

        if key in (curses.KEY_UP, ord("k"), ord("K")):
            selected = (selected - 1) % len(MENU_ITEMS)

        elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
            selected = (selected + 1) % len(MENU_ITEMS)

        elif key in (10, 13, curses.KEY_ENTER):
            choice = MENU_ITEMS[selected]

            if choice == "Dashboard":
                screen.erase()
                screen.refresh()

                HolocronUI(
                    screen,
                    config,
                    services,
                ).run()

                configure_curses(screen, 0.1)

            elif choice == "Docker":
                show_placeholder(screen, "Docker Control")

            elif choice == "Settings":
                app = HolocronUI(
                    screen, 
                    config, 
                    services,
                    start_in_settings=True,
                )
                app.run()

            elif choice == "Exit":
                return

        elif key in (ord("q"), ord("Q"), 27):
            return


def start_ui(config: holocron.Config, services: ModuleType) -> None:
    """Initialize curses and run the existing dashboard directly."""
    curses.wrapper(run_ui, config, services)


def start_menu(config: holocron.Config, services: ModuleType) -> None:
    """Initialize curses and run the Holocron main menu."""
    curses.wrapper(run_main_menu, config, services)
