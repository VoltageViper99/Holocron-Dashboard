import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from holocron import (
    VERSION,
    Config,
    NetworkSampler,
    WeatherClient,
    detect_level,
    format_log_entry,
    load_ui_module,
    parse_log_line,
    package_update_count,
)
from holocron_agent import build_event_sources, merge_server_events, port_manager_status
from updater import is_newer, parse_release, version_tuple


class FakeWeatherResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        return b""


GEOCODE_RESPONSE = {
    "results": [
        {
            "latitude": -41.05,
            "longitude": 145.9,
            "name": "Burnie",
            "admin1": "Tasmania",
        }
    ]
}

FORECAST_RESPONSE = {
    "current": {
        "temperature_2m": 13,
        "apparent_temperature": 11,
        "wind_speed_10m": 17,
        "relative_humidity_2m": 74,
        "visibility": 10000,
        "weather_code": 3,
    },
    "daily": {
        "time": ["2026-07-29", "2026-07-30", "2026-07-31", "2026-08-01"],
        "weather_code": [2, 61, 3, 0],
        "temperature_2m_min": [7, 8, 6, 5],
        "temperature_2m_max": [14, 15, 12, 11],
    },
}


class LogFormattingTests(unittest.TestCase):
    def test_package_version(self):
        self.assertEqual(VERSION, "0.7.0")

    def test_release_version_parsing(self):
        self.assertEqual(version_tuple("v0.6.1"), (0, 6, 1))
        release = parse_release({
            "tag_name": "v0.6.0",
            "name": "Dashboard update",
            "body": "New settings",
            "zipball_url": "https://api.github.com/repos/VoltageViper99/Holocron-Dashboard/zipball/v0.6.0",
            "html_url": "https://github.com/VoltageViper99/Holocron-Dashboard/releases/tag/v0.6.0",
        })
        self.assertTrue(is_newer(release, "0.5.0"))

    def test_port_manager_status_preserves_extensible_applications(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "applications": [
                    {"id": "qbittorrent"},
                    {"id": "transmission"},
                ],
            }), encoding="utf-8")

            status = port_manager_status(path)

        self.assertTrue(status["available"])
        self.assertEqual(
            [application["id"] for application in status["applications"]],
            ["qbittorrent", "transmission"],
        )

    def test_missing_port_manager_status_is_machine_readable(self):
        status = port_manager_status(Path("/definitely/missing/status.json"))

        self.assertFalse(status["available"])

    def test_config_saves_gui_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            config = Config(
                gui_font_size=22,
                weather_location="Hobart",
                theme_name="Ice Blue",
                theme_primary="#73d7ff",
                cursor_hide_seconds=10,
            )

            config.save(path)

            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["gui_font_size"], 22)
            self.assertEqual(stored["weather_location"], "Hobart")
            self.assertEqual(stored["theme_name"], "Ice Blue")
            self.assertEqual(stored["cursor_hide_seconds"], 10)
            self.assertEqual(Config.from_file(path).gui_font_size, 22)

    def test_docker_timestamp_is_shortened(self):
        entry = parse_log_line(
            "2026-07-27T12:43:55.394283Z [INFO] Configuration loaded"
        )
        self.assertEqual(entry.timestamp, "12:43:55")
        self.assertEqual(entry.level, "INFO")
        self.assertEqual(entry.message, "Configuration loaded")

    def test_journal_timestamp_is_shortened(self):
        entry = parse_log_line(
            "2026-08-03 17:21:09+1000 jedi-archives sshd[42]: session opened"
        )

        self.assertEqual(entry.timestamp, "17:21:09")
        self.assertIn("sshd[42]", entry.message)

    def test_server_event_stream_merges_docker_and_journal_by_time(self):
        events = merge_server_events(
            [
                ("navidrome", "2026-08-03T17:21:11Z server ready"),
                ("prowlarr", "2026-08-03T17:21:09Z update complete"),
            ],
            ["2026-08-03 17:21:10+1000 sshd[42]: session opened"],
        )

        self.assertIn("DOCKER:prowlarr", events[0])
        self.assertIn("SYSTEM", events[1])
        self.assertIn("DOCKER:navidrome", events[2])

    def test_event_sources_include_every_container_and_system_journal(self):
        sources = build_event_sources(
            {
                "navidrome": ["2026-08-03T17:21:11Z server ready"],
                "prowlarr": ["2026-08-03T17:21:09Z update complete"],
                "qbittorrent": ["2026-08-03T17:21:12Z listening"],
            },
            ["2026-08-03 17:21:10+1000 sshd[42]: session opened"],
        )

        self.assertEqual(
            [source["id"] for source in sources],
            [
                "docker:navidrome",
                "docker:prowlarr",
                "docker:qbittorrent",
                "system:journal",
            ],
        )
        self.assertEqual(sources[-1]["name"], "SYSTEM JOURNAL")
        self.assertIn("DOCKER:navidrome", sources[0]["lines"][0])
        self.assertIn("SYSTEM", sources[-1]["lines"][0])

    def test_system_journal_remains_a_rotation_source_when_quiet(self):
        sources = build_event_sources({"navidrome": []}, [])

        self.assertEqual(sources[-1]["id"], "system:journal")
        self.assertEqual(sources[-1]["lines"], [])

    def test_error_detection(self):
        self.assertEqual(detect_level("database connection refused"), "ERROR")

    def test_success_detection(self):
        self.assertEqual(detect_level("server started successfully"), "SUCCESS")

    def test_ansi_codes_are_removed(self):
        entry = parse_log_line("\x1b[31mERROR: failed to connect\x1b[0m")
        self.assertEqual(entry.level, "ERROR")
        self.assertEqual(entry.message, "failed to connect")

    def test_formatted_line_respects_width(self):
        entry = parse_log_line("A very long informational message without timestamp")
        rendered = format_log_entry(entry, 36)
        self.assertLessEqual(len(rendered), 36)

    def test_ui_module_is_loaded_from_sibling_file(self):
        module = load_ui_module()

        self.assertEqual(Path(module.__file__).resolve(), Path("ui.py").resolve())

    @patch("holocron.json.load", side_effect=[GEOCODE_RESPONSE, FORECAST_RESPONSE])
    @patch("holocron.urlopen", return_value=FakeWeatherResponse())
    def test_four_day_weather_forecast_starts_with_today(
        self, mocked_urlopen, mocked_json_load
    ):
        weather = WeatherClient("Hobart").fetch()

        self.assertEqual(len(weather["days"]), 4)
        self.assertEqual(
            weather["days"][0],
            {"label": "TODAY", "condition": "Partly cloudy", "low": "7", "high": "14"},
        )
        self.assertEqual(weather["days"][1]["label"], "THU")
        geocode_request = mocked_urlopen.call_args_list[0].args[0]
        self.assertIn("geocoding-api.open-meteo.com", geocode_request.full_url)
        self.assertIn("Hobart", geocode_request.full_url)
        forecast_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertIn("api.open-meteo.com", forecast_request.full_url)
        self.assertIn("forecast_days=4", forecast_request.full_url)

    @patch("holocron.json.load", side_effect=[GEOCODE_RESPONSE, FORECAST_RESPONSE])
    @patch("holocron.urlopen", return_value=FakeWeatherResponse())
    def test_graphical_weather_conditions_include_full_strip_data(
        self, mocked_urlopen, mocked_json_load
    ):
        weather = WeatherClient("Burnie").fetch()

        self.assertEqual(weather["place"], "Burnie, Tasmania")
        self.assertEqual(weather["temp"], "13")
        self.assertEqual(weather["condition"], "Overcast")
        self.assertEqual(weather["visibility"], "10")
        self.assertNotIn("sunrise", weather)
        self.assertNotIn("sunset", weather)

    def test_network_sampler_calculates_current_rates(self):
        sampler = NetworkSampler.__new__(NetworkSampler)
        sampler.previous = (1_000, 2_000)
        sampler.previous_time = 10.0
        sampler.capacity_mbps = 8.0

        with (
            patch("holocron.time.monotonic", return_value=12.0),
            patch.object(
                NetworkSampler,
                "_read",
                return_value=(2_001_000, 1_002_000, ["eth0"]),
            ),
        ):
            stats = sampler.sample()

        self.assertEqual(stats.receive_bytes_per_second, 1_000_000)
        self.assertEqual(stats.transmit_bytes_per_second, 500_000)
        self.assertEqual(stats.receive_percent, 100)
        self.assertEqual(stats.transmit_percent, 50)

    @patch("holocron.shutil.which", return_value="/usr/bin/tool")
    @patch("holocron.subprocess.run")
    def test_package_update_count(self, mocked_run, mocked_which):
        apt_update = unittest.mock.Mock(returncode=0, stdout="")
        apt_list = unittest.mock.Mock(
            returncode=0,
            stdout=(
                "Listing... Done\n"
                "linux-image-generic/jammy-updates 1.2 amd64 "
                "[upgradable from: 1.1]\n"
                "python3/jammy-updates 3.11 amd64 "
                "[upgradable from: 3.10]\n"
            ),
        )
        mocked_run.side_effect = [apt_update, apt_list]

        self.assertEqual(package_update_count(), 2)
        self.assertEqual(
            mocked_run.call_args_list[0].args[0],
            ["sudo", "-n", "apt", "update"],
        )
        self.assertEqual(
            mocked_run.call_args_list[1].args[0],
            ["apt", "list", "--upgradable"],
        )

    @patch(
        "holocron.Path.read_text",
        return_value=(
            "Iface Destination Gateway Flags\n"
            "eth0 00000000 01010101 0003\n"
            "eth0 00000000 02020202 0003\n"
        ),
    )
    def test_default_route_interface_is_not_duplicated(self, mocked_read_text):
        self.assertEqual(NetworkSampler._default_route_interfaces(), ["eth0"])


if __name__ == "__main__":
    unittest.main()
