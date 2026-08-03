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


class FakeWeatherResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit):
        return b""


FORECAST_RESPONSE = {
    "current_condition": [{
        "temp_C": "13", "FeelsLikeC": "11", "windspeedKmph": "17",
        "humidity": "74", "visibility": "10",
        "weatherDesc": [{"value": "Mostly cloudy"}],
    }],
    "nearest_area": [{
        "areaName": [{"value": "Burnie"}],
        "region": [{"value": "Tasmania"}],
    }],
    "weather": [
        {
            "date": "2026-07-29",
            "mintempC": "7",
            "maxtempC": "14",
            "astronomy": [{"sunrise": "07:26 AM", "sunset": "05:25 PM"}],
            "hourly": [
                {"weatherDesc": [{"value": "Partly cloudy"}]}
            ] * 8,
        },
        {
            "date": "2026-07-30",
            "mintempC": "8",
            "maxtempC": "15",
            "hourly": [
                {"weatherDesc": [{"value": "Light rain"}]}
            ] * 8,
        },
        {
            "date": "2026-07-31",
            "mintempC": "6",
            "maxtempC": "12",
            "hourly": [
                {"weatherDesc": [{"value": "Cloudy"}]}
            ] * 8,
        },
    ]
}


class LogFormattingTests(unittest.TestCase):
    def test_package_version(self):
        self.assertEqual(VERSION, "0.5.0")

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
            config = Config(gui_font_size=22, weather_location="Hobart")

            config.save(path)

            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["gui_font_size"], 22)
            self.assertEqual(stored["weather_location"], "Hobart")
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

    @patch("holocron.json.load", return_value=FORECAST_RESPONSE)
    @patch("holocron.urlopen", return_value=FakeWeatherResponse())
    def test_three_day_weather_forecast_is_compact(
        self, mocked_urlopen, mocked_json_load
    ):
        forecast = WeatherClient("Hobart").forecast()

        self.assertEqual(len(forecast), 3)
        self.assertEqual(forecast[0], "WED Partly cloudy  7–14°C")
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("/Hobart?", request.full_url)
        self.assertIn("format=j1", request.full_url)

    @patch("holocron.json.load", return_value=FORECAST_RESPONSE)
    @patch("holocron.urlopen", return_value=FakeWeatherResponse())
    def test_graphical_weather_conditions_include_full_strip_data(
        self, mocked_urlopen, mocked_json_load
    ):
        conditions = WeatherClient("Burnie").conditions()

        self.assertEqual(conditions["place"], "Burnie, Tasmania")
        self.assertEqual(conditions["temp"], "13")
        self.assertEqual(conditions["condition"], "Mostly cloudy")
        self.assertEqual(conditions["sunrise"], "07:26 AM")

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
