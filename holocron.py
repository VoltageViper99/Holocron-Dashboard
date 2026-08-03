#!/usr/bin/env python3
"""
Holocron — a retro green Docker log rotator for terminal dashboards.

Runs entirely with the Python standard library. It uses the Docker CLI,
Linux /proc files, and curses. Optional startup speech uses espeak-ng/espeak,
or a user-supplied audio file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


APP_NAME = "HOLOCRON"
VERSION = "0.5.0"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "holocron" / "config.json"


@dataclass
class Config:
    rotation_seconds: int = 15
    tail_lines: int = 120
    refresh_seconds: float = 1.0
    containers: list[str] | None = None
    title: str = "JEDI ARCHIVES"
    startup_phrase: str = "Holocron started, it has."
    startup_audio: str = ""
    speech_enabled: bool = True
    show_timestamps: bool = True
    log_file: str = "~/.local/state/holocron/holocron.log"
    simplify_messages: bool = True
    dashboard_mode: bool = True
    show_all_containers: bool = True
    weather_enabled: bool = True
    weather_location: str = ""
    weather_refresh_seconds: int = 900
    gui_font_size: int = 16
    update_refresh_seconds: int = 900
    journal_enabled: bool = True
    journal_tail_lines: int = 100
    journal_refresh_seconds: float = 2.0
    journal_priority: str = "info"

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read config {path}: {exc}") from exc

        allowed = {field for field in cls.__dataclass_fields__}
        clean = {key: value for key, value in data.items() if key in allowed}
        return cls(**clean)

    def save(self, path: Path) -> None:
        """Persist the complete configuration for the next dashboard launch."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    message: str


@dataclass(frozen=True)
class ContainerState:
    name: str
    state: str
    status: str
    health: str

    @property
    def running(self) -> bool:
        return self.state.lower() == "running"


@dataclass(frozen=True)
class TemperatureReading:
    """A labelled temperature reported by Linux's thermal interfaces."""

    device: str
    sensor: str
    celsius: float


@dataclass(frozen=True)
class FilesystemUsage:
    """A real filesystem suitable for the dashboard storage matrix."""

    mount: str
    used_percent: float
    free_bytes: int


LEVEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("FATAL", ("fatal", "panic", "critical")),
    ("ERROR", ("error", "failed", "failure", "exception", "traceback", "refused")),
    ("WARN", ("warn", "warning", "deprecated", "retrying")),
    ("SUCCESS", ("success", "successful", "completed", "healthy", "ready", "started", "listening")),
    ("INFO", ("info", "notice", "debug")),
)

TIMESTAMP_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(?P<message>.*)$"
)
JOURNAL_TIMESTAMP_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<time>\d{2}:\d{2}:\d{2})(?:[.,]\d+)?(?:[+-]\d{4})?\s+"
    r"(?P<message>.*)$"
)
PREFIX_RE = re.compile(r"^\s*[\[<(]?(?:trace|debug|info|notice|warn(?:ing)?|error|fatal|critical)[\])>: -]+", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def setup_app_logger(config: Config) -> logging.Logger:
    logger = logging.getLogger("holocron")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    path = Path(os.path.expanduser(config.log_file))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()

    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


def detect_level(message: str) -> str:
    lower = message.lower()
    for level, words in LEVEL_PATTERNS:
        if any(word in lower for word in words):
            return level
    return "INFO"


def simplify_log_message(message: str) -> str:
    message = ANSI_RE.sub("", message).strip()
    message = PREFIX_RE.sub("", message).strip()
    message = re.sub(r"\s+", " ", message)
    return message or "(empty log message)"


def parse_log_line(line: str, simplify: bool = True) -> LogEntry:
    clean = ANSI_RE.sub("", line).strip()
    timestamp = "--:--:--"
    message = clean

    match = TIMESTAMP_RE.match(clean)
    if match:
        raw_stamp = match.group("stamp")
        message = match.group("message")
        try:
            normalized = raw_stamp.replace("Z", "+00:00")
            timestamp = datetime.fromisoformat(normalized).strftime("%H:%M:%S")
        except ValueError:
            timestamp = raw_stamp[11:19]
    else:
        journal_match = JOURNAL_TIMESTAMP_RE.match(clean)
        if journal_match:
            timestamp = journal_match.group("time")
            message = journal_match.group("message")
        elif clean:
            timestamp = time.strftime("%H:%M:%S")

    level = detect_level(message)
    if simplify:
        message = simplify_log_message(message)
    return LogEntry(timestamp=timestamp, level=level, message=message)


def format_log_entry(entry: LogEntry, width: int) -> str:
    prefix = f"{entry.timestamp} │ {entry.level:<7} │ "
    return prefix + trim(entry.message, max(0, width - len(prefix) - 1))


class DockerClient:
    def __init__(self) -> None:
        if not shutil.which("docker"):
            raise RuntimeError("Docker CLI was not found in PATH.")

    @staticmethod
    def _run(args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", *args],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def containers(self) -> list[str]:
        result = self._run(["ps", "--format", "{{.Names}}"])
        if result.returncode != 0:
            message = result.stderr.strip() or "docker ps failed"
            raise RuntimeError(message)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def container_states(self, include_stopped: bool = True) -> list[ContainerState]:
        args = ["ps"]
        if include_stopped:
            args.append("-a")
        args.extend(["--format", "{{.Names}}\t{{.State}}\t{{.Status}}"] )
        result = self._run(args)
        if result.returncode != 0:
            message = result.stderr.strip() or "docker ps failed"
            raise RuntimeError(message)

        states: list[ContainerState] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            name, state, status = (part.strip() for part in parts)
            lower = status.lower()
            if "(unhealthy)" in lower:
                health = "UNHEALTHY"
            elif "(healthy)" in lower:
                health = "HEALTHY"
            elif state.lower() == "running":
                health = "RUNNING"
            else:
                health = "STOPPED"
            states.append(ContainerState(name=name, state=state, status=status, health=health))
        return states

    def logs(self, container: str, tail: int, timestamps: bool) -> list[str]:
        args = ["logs", "--tail", str(tail)]
        if timestamps:
            args.append("--timestamps")
        args.append(container)
        result = self._run(args, timeout=15.0)

        # Docker often writes application logs to stderr.
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        lines = output.splitlines()

        if result.returncode != 0 and not lines:
            lines = [f"[docker error] Could not read logs for {container}"]
        return lines


class JournalClient:
    """Read recent host events without requiring elevated privileges."""

    VALID_PRIORITIES = {
        "emerg",
        "alert",
        "crit",
        "err",
        "warning",
        "notice",
        "info",
        "debug",
    }

    def __init__(self, priority: str = "info") -> None:
        self.available = shutil.which("journalctl") is not None
        self.priority = (
            priority.lower()
            if priority.lower() in self.VALID_PRIORITIES
            else "info"
        )

    def logs(self, tail: int) -> list[str]:
        if not self.available:
            return ["journalctl is not available on this host"]

        try:
            result = subprocess.run(
                [
                    "journalctl",
                    "--no-pager",
                    "--quiet",
                    "--output=short-iso",
                    "--priority",
                    f"{self.priority}..emerg",
                    "--lines",
                    str(max(1, tail)),
                ],
                text=True,
                capture_output=True,
                timeout=8.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"[warning] Could not read system journal: {exc}"]

        if result.returncode != 0:
            message = result.stderr.strip() or "journalctl failed"
            return [f"[warning] {message}"]
        return [line for line in result.stdout.splitlines() if line.strip()]


class WeatherClient:
    """Fetch a compact three-day forecast from wttr.in."""

    def __init__(self, location: str = "") -> None:
        self.location = location.strip()

    def forecast(self) -> list[str]:
        payload = self._payload()

        days = payload.get("weather", [])[:3]
        if not days:
            raise RuntimeError("Weather service returned no forecast")

        forecast: list[str] = []
        for day in days:
            date = datetime.strptime(day["date"], "%Y-%m-%d")
            hourly = day.get("hourly", [])
            midday = hourly[min(4, len(hourly) - 1)] if hourly else {}
            descriptions = midday.get("weatherDesc", [])
            description = descriptions[0].get("value", "") if descriptions else ""
            condition = re.sub(r"\s+", " ", description).strip() or "Unknown"
            low = day.get("mintempC", "?")
            high = day.get("maxtempC", "?")
            forecast.append(
                f"{date.strftime('%a').upper():<3} "
                f"{condition:<14.14} {low}–{high}°C"
            )
        return forecast

    def _payload(self) -> dict[str, object]:
        location = quote(self.location, safe="")
        url = f"https://wttr.in/{location}?format=j1&m"
        request = Request(
            url,
            headers={"User-Agent": f"Holocron/{VERSION}"},
        )
        with urlopen(request, timeout=5.0) as response:
            return json.load(response)

    def conditions(self) -> dict[str, str]:
        """Return the current conditions used by the graphical weather strip."""
        payload = self._payload()
        current = (payload.get("current_condition") or [{}])[0]
        nearest = (payload.get("nearest_area") or [{}])[0]
        today = (payload.get("weather") or [{}])[0]
        astronomy = (today.get("astronomy") or [{}])[0]

        def text(items: object, fallback: str = "—") -> str:
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return str(items[0].get("value", fallback))
            return fallback

        place = text(nearest.get("areaName"), self.location or "Local weather")
        region = text(nearest.get("region"), "")
        if region and region.lower() not in place.lower():
            place = f"{place}, {region}"
        return {
            "place": place,
            "condition": text(current.get("weatherDesc"), "Unknown"),
            "temp": str(current.get("temp_C", "—")),
            "feels": str(current.get("FeelsLikeC", "—")),
            "wind": str(current.get("windspeedKmph", "—")),
            "humidity": str(current.get("humidity", "—")),
            "visibility": str(current.get("visibility", "—")),
            "sunrise": str(astronomy.get("sunrise", "—")),
            "sunset": str(astronomy.get("sunset", "—")),
        }


def package_update_count() -> int | None:
    """Refresh APT metadata when permitted and count Ubuntu updates."""
    if not shutil.which("apt"):
        return None

    if shutil.which("sudo"):
        try:
            subprocess.run(
                ["sudo", "-n", "apt", "update"],
                text=True,
                capture_output=True,
                timeout=120.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    try:
        result = subprocess.run(
            ["apt", "list", "--upgradable"],
            text=True,
            capture_output=True,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return sum(
        1
        for line in result.stdout.splitlines()
        if "/" in line and "[upgradable from:" in line
    )


@dataclass(frozen=True)
class NetworkStats:
    receive_bytes_per_second: float
    transmit_bytes_per_second: float
    receive_percent: float
    transmit_percent: float
    capacity_mbps: float


class NetworkSampler:
    """Sample aggregate non-loopback network traffic from Linux procfs."""

    def __init__(self) -> None:
        received, transmitted, interfaces = self._read()
        self.previous = (received, transmitted)
        self.previous_time = time.monotonic()
        self.capacity_mbps = self._detect_capacity(interfaces)

    @staticmethod
    def _read() -> tuple[int, int, list[str]]:
        counters: dict[str, tuple[int, int]] = {}
        try:
            lines = Path("/proc/net/dev").read_text().splitlines()[2:]
            for line in lines:
                name, values = line.split(":", 1)
                name = name.strip()
                if name == "lo":
                    continue
                fields = values.split()
                if len(fields) < 9:
                    continue
                counters[name] = (int(fields[0]), int(fields[8]))
        except (OSError, ValueError):
            return 0, 0, []

        interfaces = NetworkSampler._default_route_interfaces()
        interfaces = [
            name
            for name in interfaces
            if name in counters and name != "lo"
        ]
        if not interfaces:
            interfaces = [
                name
                for name in counters
                if name != "lo"
                and (Path("/sys/class/net") / name / "device").exists()
            ]
        if not interfaces:
            virtual_prefixes = ("br-", "docker", "veth", "virbr")
            interfaces = [
                name
                for name in counters
                if name != "lo"
                and not name.startswith(virtual_prefixes)
            ]

        received = sum(counters[name][0] for name in interfaces)
        transmitted = sum(counters[name][1] for name in interfaces)
        return received, transmitted, interfaces

    @staticmethod
    def _default_route_interfaces() -> list[str]:
        interfaces: list[str] = []
        try:
            lines = Path("/proc/net/route").read_text().splitlines()[1:]
            for line in lines:
                fields = line.split()
                if len(fields) < 4:
                    continue
                interface, destination, _, raw_flags = fields[:4]
                if destination == "00000000" and int(raw_flags, 16) & 0x2:
                    interfaces.append(interface)
        except (OSError, ValueError):
            return []
        # A machine can have multiple default-route entries for the same
        # interface. Count that interface once so its traffic is not inflated.
        return list(dict.fromkeys(interfaces))

    @staticmethod
    def _detect_capacity(interfaces: list[str]) -> float:
        speeds: list[float] = []
        for interface in interfaces:
            try:
                speed = float(
                    (
                        Path("/sys/class/net")
                        / interface
                        / "speed"
                    ).read_text().strip()
                )
                if speed > 0:
                    speeds.append(speed)
            except (OSError, ValueError):
                continue
        return max(speeds, default=1000.0)

    def sample(self) -> NetworkStats:
        now = time.monotonic()
        received, transmitted, _ = self._read()
        elapsed = now - self.previous_time

        if elapsed <= 0:
            receive_rate = 0.0
            transmit_rate = 0.0
        else:
            receive_rate = max(
                0.0,
                (received - self.previous[0]) / elapsed,
            )
            transmit_rate = max(
                0.0,
                (transmitted - self.previous[1]) / elapsed,
            )

        self.previous = (received, transmitted)
        self.previous_time = now
        capacity_bytes_per_second = self.capacity_mbps * 1_000_000 / 8

        def utilization(rate: float) -> float:
            if capacity_bytes_per_second <= 0:
                return 0.0
            return max(
                0.0,
                min(100.0, rate * 100 / capacity_bytes_per_second),
            )

        return NetworkStats(
            receive_bytes_per_second=receive_rate,
            transmit_bytes_per_second=transmit_rate,
            receive_percent=utilization(receive_rate),
            transmit_percent=utilization(transmit_rate),
            capacity_mbps=self.capacity_mbps,
        )


class CpuSampler:
    def __init__(self) -> None:
        self.previous = self._read()

    @staticmethod
    def _read() -> tuple[int, int]:
        try:
            fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            values = [int(item) for item in fields]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            return total, idle
        except (OSError, ValueError, IndexError):
            return 0, 0

    def percent(self) -> float:
        current = self._read()
        total_delta = current[0] - self.previous[0]
        idle_delta = current[1] - self.previous[1]
        self.previous = current
        if total_delta <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta)))


def memory_percent() -> float:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        if total <= 0:
            return 0.0
        return 100.0 * (1.0 - available / total)
    except (OSError, ValueError):
        return 0.0


def disk_percent(path: str = "/") -> float:
    try:
        usage = shutil.disk_usage(path)
        return 100.0 * usage.used / usage.total if usage.total else 0.0
    except OSError:
        return 0.0


def filesystem_usage(limit: int = 4) -> list[FilesystemUsage]:
    """Return important physical mounts while excluding virtual filesystems."""
    mounts: list[str] = ["/"]
    seen_devices: set[str] = set()
    try:
        for line in Path("/proc/self/mounts").read_text().splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            device, mount, filesystem = fields[:3]
            if not device.startswith("/dev/") or device in seen_devices:
                continue
            seen_devices.add(device)
            if filesystem in {"swap", "squashfs"}:
                continue
            if mount != "/" and not mount.startswith(("/home", "/srv", "/mnt", "/media")):
                continue
            mounts.append(mount.replace("\\040", " "))
    except OSError:
        pass

    ordered = sorted(set(mounts), key=lambda item: (item != "/", item))
    result: list[FilesystemUsage] = []
    for mount in ordered:
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        percent = 100.0 * usage.used / usage.total if usage.total else 0.0
        result.append(FilesystemUsage(mount, percent, usage.free))
        if len(result) >= max(1, limit):
            break
    return result


def temperature_summary() -> list[TemperatureReading]:
    """Return valid, labelled readings from Linux thermal and hwmon devices."""
    candidates = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    candidates += sorted(Path("/sys/class/hwmon").glob("hwmon*/temp*_input"))
    readings: list[TemperatureReading] = []
    for path in candidates:
        try:
            value = float(path.read_text().strip())
            if value > 1000:
                value /= 1000.0
            if 0 < value < 150:
                if path.parent.name.startswith("thermal_zone"):
                    type_path = path.parent / "type"
                    device = (
                        type_path.read_text().strip()
                        if type_path.exists()
                        else path.parent.name
                    )
                    sensor = "temp"
                else:
                    name_path = path.parent / "name"
                    device = (
                        name_path.read_text().strip()
                        if name_path.exists()
                        else path.parent.name
                    )
                    sensor_number = path.stem.removeprefix("temp").removesuffix("_input")
                    label_path = path.parent / f"temp{sensor_number}_label"
                    sensor = (
                        label_path.read_text().strip()
                        if label_path.exists()
                        else f"temp{sensor_number}"
                    )
                readings.append(TemperatureReading(device, sensor, value))
        except (OSError, ValueError):
            continue
    return readings


def cpu_temperature() -> float | None:
    """Return the hottest available sensor reading for compatibility."""
    readings = temperature_summary()
    return max((reading.celsius for reading in readings), default=None)


def system_uptime() -> str:
    try:
        seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return "UNKNOWN"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours:02d}h"
    return f"{hours:02d}h {minutes:02d}m"


def system_identity() -> str:
    """Return a compact hostname and kernel description for the header."""
    hostname = platform.node().split(".", 1)[0] or "UNKNOWN"
    kernel = platform.release().split("-", 1)[0]
    return f"{hostname.upper()} │ LINUX {kernel}"


def load_average() -> str:
    try:
        one, five, fifteen = os.getloadavg()
        return f"{one:.2f} {five:.2f} {fifteen:.2f}"
    except OSError:
        return "N/A"


def play_startup(config: Config) -> None:
    """Play a user-provided audio file, or use a generic system TTS voice."""
    if not config.speech_enabled:
        return

    audio = Path(os.path.expanduser(config.startup_audio)) if config.startup_audio else None
    if audio and audio.exists():
        players = [
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio)],
            ["mpv", "--no-video", "--really-quiet", str(audio)],
            ["paplay", str(audio)],
            ["aplay", str(audio)],
        ]
        for command in players:
            if shutil.which(command[0]):
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return

    # This deliberately uses a generic synthesized voice, not an imitation.
    for binary in ("espeak-ng", "espeak"):
        if shutil.which(binary):
            subprocess.Popen(
                [binary, "-s", "125", "-p", "35", config.startup_phrase],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return


def trim(text: str, width: int) -> str:
    if width <= 0:
        return ""
    clean = text.replace("\t", "    ")
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 1)] + "…"


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = Config()
    path.write_text(
        json.dumps(config.__dict__, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retro green Docker log rotator for terminal dashboards."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Config path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a default config file and exit.",
    )
    parser.add_argument(
        "--no-speech",
        action="store_true",
        help="Disable the startup announcement for this run.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Open the live dashboard immediately, bypassing the menu.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser.parse_args()


def load_ui_module() -> ModuleType:
    """Load the UI from the file installed beside this entry point."""
    module_name = "_holocron_ui"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    ui_path = Path(__file__).resolve().with_name("ui.py")
    spec = importlib.util.spec_from_file_location(module_name, ui_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load UI module: {ui_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def main() -> int:
    args = parse_args()

    if args.init_config:
        if args.config.exists():
            print(f"Config already exists: {args.config}")
            return 1
        write_default_config(args.config)
        print(f"Created {args.config}")
        return 0

    try:
        config = Config.from_file(args.config)
        if args.no_speech:
            config.speech_enabled = False
        # Load lazily so configuration and log helpers remain usable on
        # systems where curses is unavailable. Resolve from this file rather
        # than relying on the process or editor's Python search path.
        ui = load_ui_module()
        if args.dashboard:
            ui.start_ui(config, sys.modules[__name__])
        else:
            ui.start_menu(config, sys.modules[__name__])
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"holocron: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
