import unittest
from types import SimpleNamespace
from unittest.mock import patch

from holocron import TemperatureReading, load_ui_module


ui = load_ui_module()


class UIHelperTests(unittest.TestCase):
    def test_progress_bar_clamps_values(self):
        self.assertEqual(ui.progress_bar(-10, cells=4), "░░░░")
        self.assertEqual(ui.progress_bar(50, cells=4), "██░░")
        self.assertEqual(ui.progress_bar(110, cells=4), "████")

    def test_network_rate_is_human_readable(self):
        self.assertEqual(ui.format_byte_rate(512), "512 B/s")
        self.assertEqual(ui.format_byte_rate(1536), "1.5 KB/s")
        self.assertEqual(
            ui.format_byte_rate(2 * 1024 * 1024),
            "2.0 MB/s",
        )

    def test_storage_size_is_human_readable(self):
        self.assertEqual(ui.format_bytes(512), "512 B")
        self.assertEqual(ui.format_bytes(8 * 1024**3), "8 GiB")

    def test_panel_metric_respects_its_column_width(self):
        rendered = ui.panel_metric("DOWN", 50, "1.5 MB/s", width=24)

        self.assertLessEqual(len(rendered), 24)
        self.assertIn("█", rendered)
        self.assertIn("░", rendered)

    def test_ui_state_lists_are_not_shared(self):
        first = ui.UIState(dashboard_mode=True)
        second = ui.UIState(dashboard_mode=True)

        first.cached_logs.append("message")

        self.assertEqual(second.cached_logs, [])

    def test_dashboard_panels_use_a_sixty_forty_column_split(self):
        divider_x, log_top = ui.dashboard_panel_layout(50, 120)

        self.assertEqual(log_top, max(10, int(50 * 0.56)))
        self.assertEqual(divider_x, round(120 * 0.60))
        self.assertEqual(120 - divider_x, round(120 * 0.40))

    def test_ui_exposes_panel_rendering_methods(self):
        method_names = (
            "draw_header",
            "draw_system_panel",
            "draw_container_panel",
            "draw_log_panel",
            "draw_journal_panel",
        )

        for method_name in method_names:
            self.assertTrue(callable(getattr(ui.HolocronUI, method_name)))

    def test_main_view_draws_footer(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.settings_active = False
        dashboard.state = ui.UIState(dashboard_mode=False)

        with (
            patch.object(dashboard, "draw_header"),
            patch.object(dashboard, "draw_log_panel"),
            patch.object(dashboard, "_draw_footer") as mocked_footer,
        ):
            dashboard._draw(12.5, "service", ["service"], 24, 80)

        mocked_footer.assert_called_once_with(12.5, 24, 80)

    def test_temperature_labels_are_friendly_names(self):
        self.assertEqual(
            ui.TEMPERATURE_LABELS,
            ("CPU", "NVMe", "System", "Hotspot"),
        )

    def test_header_does_not_draw_weather(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()
        dashboard.config = SimpleNamespace(
            title="JEDI ARCHIVES",
            weather_enabled=True,
        )
        dashboard.services = SimpleNamespace(
            APP_NAME="HOLOCRON",
            VERSION="0.2.0",
            system_identity=lambda: "JEDI-ARCHIVES │ LINUX 6.8.0",
            system_uptime=lambda: "01h 30m",
        )
        dashboard.state = ui.UIState(
            dashboard_mode=True,
            weather_forecast=["WED Partly cloudy  7–14°C"],
        )

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard.draw_header(120)

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertNotIn("WED Partly cloudy  7–14°C", drawn_text)

    def test_dashboard_draws_system_event_stream(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()
        dashboard.config = SimpleNamespace(simplify_messages=True)
        dashboard.services = SimpleNamespace(
            parse_log_line=lambda line, simplify: SimpleNamespace(
                timestamp="12:00:00",
                level="INFO",
                message=line,
            ),
            format_log_entry=lambda entry, width: entry.message[:width],
        )
        dashboard.state = ui.UIState(
            dashboard_mode=True,
            cached_journal=["server event"],
        )
        dashboard.journal_client = object()

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard.draw_journal_panel(70, 20, 40, 120)

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertIn("SYSTEM EVENT STREAM │ JOURNALCTL", drawn_text)
        self.assertIn("server event", drawn_text)

    def test_three_day_forecast_is_drawn_in_system_panel(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()
        dashboard.config = SimpleNamespace(weather_enabled=True)
        dashboard.state = ui.UIState(
            dashboard_mode=True,
            weather_forecast=["WED Clear 7–14°C", "THU Rain 8–15°C", "FRI Cloudy 6–12°C"],
        )

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard._draw_weather_forecast(40, 72, 20)

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertEqual(
            drawn_text,
            [
                "3 DAY FORECAST",
                "WED Clear 7–14°C",
                "THU Rain 8–15°C",
                "FRI Cloudy 6–12°C",
            ],
        )

    def test_nominal_system_alert_is_drawn_bottom_left(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard._draw_system_alerts(
                20, 30, 40, [], x=1, panel_right=38, panel_bottom=20
            )

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertEqual(drawn_text, ["SYSTEM ALERTS", "● ALL SYSTEMS NOMINAL"])

    def test_high_system_values_draw_alerts(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard._draw_system_alerts(
                95,
                20,
                94,
                [TemperatureReading("coretemp", "Package", 60)],
                x=1,
                panel_right=38,
                panel_bottom=20,
            )

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertIn("● CPU HIGH 95%", drawn_text)
        self.assertIn("● DISK LOW 6% FREE", drawn_text)

    def test_hottest_temperature_draws_alert(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()
        temperatures = [
            TemperatureReading("coretemp", "Core 0", 65),
            TemperatureReading("coretemp", "Package", 86.4),
        ]

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard._draw_system_alerts(
                20, 30, 40, temperatures, x=1, panel_right=38, panel_bottom=20
            )

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertIn("● TEMP HIGH 86°C", drawn_text)

    def test_update_count_is_drawn_above_forecast(self):
        dashboard = ui.HolocronUI.__new__(ui.HolocronUI)
        dashboard.screen = object()
        dashboard.state = ui.UIState(dashboard_mode=True, update_count=7)

        with (
            patch.object(ui.curses, "color_pair", return_value=0),
            patch.object(ui, "safe_addstr") as mocked_draw,
        ):
            dashboard._draw_update_count(40, 72, row=6, forecast_top=16)

        drawn_text = [call.args[3] for call in mocked_draw.call_args_list]
        self.assertEqual(drawn_text, ["SYSTEM UPDATES", "7 PENDING"])


if __name__ == "__main__":
    unittest.main()
