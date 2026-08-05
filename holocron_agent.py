#!/usr/bin/env python3
"""Emit one JSON snapshot of the server for the graphical Holocron client."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holocron


PORT_MANAGER_STATUS_PATH = Path("/var/lib/holocron/port-manager/status.json")


def port_manager_status(path: Path = PORT_MANAGER_STATUS_PATH) -> dict[str, object]:
    """Read the Port Manager's public contract without invoking its service."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "error": "status file not available"}
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "error": f"status file unreadable: {exc}"}
    if not isinstance(data, dict):
        return {"available": False, "error": "invalid status document"}
    data["available"] = True
    return data


def merge_server_events(
    docker_logs: list[tuple[str, str]],
    journal: list[str],
) -> list[str]:
    """Build one chronological feed using remote-server sources only."""
    events: list[tuple[str, int, str]] = []
    sequence = 0
    for container, line in docker_logs:
        entry = holocron.parse_log_line(line)
        rendered = (
            f"{entry.timestamp} │ DOCKER:{container:<14.14} │ "
            f"{entry.level:<7} │ {entry.message}"
        )
        events.append((entry.timestamp, sequence, rendered))
        sequence += 1
    for line in journal:
        entry = holocron.parse_log_line(line)
        rendered = (
            f"{entry.timestamp} │ SYSTEM{'':<15} │ "
            f"{entry.level:<7} │ {entry.message}"
        )
        events.append((entry.timestamp, sequence, rendered))
        sequence += 1
    events.sort(key=lambda event: (event[0], event[1]))
    return [event[2] for event in events]


def build_event_sources(
    docker_logs: dict[str, list[str]],
    journal: list[str],
) -> list[dict[str, object]]:
    """Return one server-only event page for every live log source."""
    sources: list[dict[str, object]] = []
    for container, lines in docker_logs.items():
        rendered: list[str] = []
        for line in lines:
            entry = holocron.parse_log_line(line)
            rendered.append(
                f"{entry.timestamp} │ DOCKER:{container:<14.14} │ "
                f"{entry.level:<7} │ {entry.message}"
            )
        sources.append({
            "id": f"docker:{container}",
            "name": container,
            "kind": "DOCKER",
            "lines": rendered,
        })

    rendered_journal: list[str] = []
    for line in journal:
        entry = holocron.parse_log_line(line)
        rendered_journal.append(
            f"{entry.timestamp} │ SYSTEM{'':<15} │ "
            f"{entry.level:<7} │ {entry.message}"
        )
    sources.append({
        "id": "system:journal",
        "name": "SYSTEM JOURNAL",
        "kind": "SYSTEM",
        "lines": rendered_journal,
    })
    return sources


def _memory() -> dict[str, float]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    return {"percent": 100 * used / total if total else 0, "used": used, "total": total}


def _disk() -> dict[str, float]:
    try:
        usage = shutil.disk_usage("/")
        return {
            "percent": 100 * usage.used / usage.total if usage.total else 0,
            "used": usage.used,
            "total": usage.total,
        }
    except OSError:
        return {"percent": 0, "used": 0, "total": 0}


def _container_stats(client: holocron.DockerClient) -> dict[str, dict[str, str]]:
    result = client._run(["stats", "--no-stream", "--format", "{{json .}}"], timeout=15)
    stats: dict[str, dict[str, str]] = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("Name", "")
            if name:
                stats[name] = {
                    "cpu": row.get("CPUPerc", "—"),
                    "memory": row.get("MemUsage", "—").split(" / ", 1)[0],
                    "mem_percent": row.get("MemPerc", "—"),
                }
    return stats


def _network_identity(interfaces: list[str]) -> dict[str, str]:
    interface = interfaces[0] if interfaces else "—"
    address = "—"
    gateway = "—"
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface],
            text=True, capture_output=True, timeout=3, check=False,
        )
        if result.returncode == 0:
            fields = result.stdout.split()
            if "inet" in fields:
                address = fields[fields.index("inet") + 1].split("/", 1)[0]
        route = subprocess.run(
            ["ip", "route", "show", "default"], text=True,
            capture_output=True, timeout=3, check=False,
        )
        route_fields = route.stdout.split()
        if "via" in route_fields:
            gateway = route_fields[route_fields.index("via") + 1]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return {"interface": interface, "address": address, "gateway": gateway}


def collect(config: holocron.Config) -> dict[str, object]:
    cpu = holocron.CpuSampler()
    network = holocron.NetworkSampler()
    time.sleep(0.25)
    cpu_percent = cpu.percent()
    net = network.sample()
    received, transmitted, interfaces = network._read()

    containers: list[dict[str, object]] = []
    docker_logs_by_service: dict[str, list[str]] = {}
    try:
        docker = holocron.DockerClient()
        states = docker.container_states(config.show_all_containers)
        stats = _container_stats(docker)
        for state in states:
            row = asdict(state)
            row.update(stats.get(state.name, {"cpu": "—", "memory": "—", "mem_percent": "—"}))
            containers.append(row)
        running = [state.name for state in states if state.running]
        tail_per_container = max(13, min(60, config.tail_lines))
        for container in running:
            docker_logs_by_service[container] = docker.logs(
                container,
                tail_per_container,
                config.show_timestamps,
            )
    except Exception as exc:
        docker_logs_by_service = {"docker": [f"[docker error] {exc}"]}

    journal = (
        holocron.JournalClient(config.journal_priority).logs(min(config.journal_tail_lines, 60))
        if config.journal_enabled else []
    )
    docker_logs = [
        (container, line)
        for container, lines in docker_logs_by_service.items()
        for line in lines
    ]
    event_stream = merge_server_events(docker_logs, journal)
    event_sources = build_event_sources(docker_logs_by_service, journal)
    temperatures = holocron.temperature_summary()
    return {
        "generated_at": time.time(),
        "host": socket.gethostname().split(".", 1)[0],
        "kernel": platform.release().split("-", 1)[0],
        "cpu": {"percent": cpu_percent, "temperature": max((t.celsius for t in temperatures), default=None)},
        "memory": _memory(),
        "disk": _disk(),
        "network": {
            "receive_rate": net.receive_bytes_per_second,
            "transmit_rate": net.transmit_bytes_per_second,
            "received_total": received,
            "transmitted_total": transmitted,
            **_network_identity(interfaces),
        },
        "uptime": holocron.system_uptime(),
        "load": holocron.load_average(),
        "containers": containers,
        "docker_logs": [line for _container, line in docker_logs],
        "journal": journal,
        "event_stream": event_stream,
        "event_sources": event_sources,
        "port_manager": port_manager_status(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=holocron.DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    try:
        print(json.dumps(collect(holocron.Config.from_file(args.config)), separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
