#!/usr/bin/env python3
"""Small, authenticated-free GitHub release updater for the Qt client."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from re import fullmatch
from urllib.request import Request, urlopen


REPOSITORY = "VoltageViper99/Holocron-Dashboard"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
VERSION_RE = r"v?(\d+)\.(\d+)\.(\d+)"


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: tuple[int, int, int]
    name: str
    body: str
    zipball_url: str
    html_url: str


def version_tuple(value: str) -> tuple[int, int, int]:
    match = fullmatch(VERSION_RE, value.strip())
    if match is None:
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.groups())


def is_newer(release: ReleaseInfo, current: str) -> bool:
    return release.version > version_tuple(current)


def parse_release(payload: dict[str, object]) -> ReleaseInfo:
    tag_name = str(payload.get("tag_name", "")).strip()
    if not tag_name:
        raise ValueError("GitHub release has no tag name")
    zipball_url = str(payload.get("zipball_url", "")).strip()
    safe_prefixes = (
        f"https://api.github.com/repos/{REPOSITORY}/zipball/",
        f"https://github.com/{REPOSITORY}/archive/",
    )
    if not zipball_url.startswith(safe_prefixes):
        raise ValueError("GitHub release has no safe source archive")
    return ReleaseInfo(
        tag_name=tag_name,
        version=version_tuple(tag_name),
        name=str(payload.get("name") or tag_name),
        body=str(payload.get("body") or "").strip(),
        zipball_url=zipball_url,
        html_url=str(payload.get("html_url") or "https://github.com/" + REPOSITORY),
    )


def fetch_latest_release(url: str = RELEASES_API) -> ReleaseInfo:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Holocron-Dashboard-Updater",
        },
    )
    with urlopen(request, timeout=12) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid release response")
    return parse_release(payload)


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("Release archive contains an unsafe path")
        bundle.extractall(destination)


def install_release(release: ReleaseInfo, mode: str = "--client") -> str:
    """Download a release and run the matching installer with user approval."""
    if mode not in ("--client", "--server", "--all"):
        raise ValueError(f"Unsupported install mode: {mode}")
    pkexec = shutil.which("pkexec")
    sudo = shutil.which("sudo")
    if pkexec is None and sudo is None:
        raise RuntimeError("Neither pkexec nor sudo is available for installation")
    privilege_command = [pkexec] if pkexec else [sudo, "-n"]

    with tempfile.TemporaryDirectory(prefix="holocron-update-") as temporary:
        root = Path(temporary)
        archive = root / "release.zip"
        request = Request(
            release.zipball_url,
            headers={"User-Agent": "Holocron-Dashboard-Updater"},
        )
        with urlopen(request, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        extract_root = root / "source"
        extract_root.mkdir()
        _safe_extract(archive, extract_root)
        installers = list(extract_root.glob("*/install.sh"))
        if len(installers) != 1:
            raise RuntimeError("Release archive does not contain one valid installer")
        installer = installers[0]
        result = subprocess.run(
            [*privilege_command, "bash", str(installer), mode],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or "The release installer failed")
    return result.stdout.strip() or "Update installed successfully"
