# Holocron Dashboard

Holocron is a retro-green homelab dashboard for Docker services, host health,
system events, and network activity. Version **0.7.0** provides two clients:

- **holocron**: a terminal/curses dashboard for a local console, SSH session, or tmux pane.
- **holocron-gui**: a native PySide6/Qt dashboard for a dedicated 1080p display.

The graphical client can run on a separate display machine. It collects a compact
JSON snapshot from a server over non-interactive SSH, without X11 or Wayland
forwarding.

The project also includes **Holocron Port Manager**, an independent Bash service
that renews Proton VPN NAT-PMP mappings and synchronises the forwarded port with
qBittorrent.

## Architecture

    Dedicated display                 Homelab server
    +----------------------+           +----------------------+
    | holocron-gui + Cage |--- SSH --> | holocron-agent       |
    +----------------------+    JSON   | Docker + journalctl  |
                                       | Port Manager         |
                                       | Proton NAT-PMP       |
                                       | qBittorrent Web API  |
                                       +----------------------+

The terminal client may run directly on the server and does not use the SSH
agent path. Port Manager publishes its state at:

    /var/lib/holocron/port-manager/status.json

## Features

### Terminal dashboard

- Rotating Docker container and system-journal log sources.
- Container counts and health states.
- CPU, memory, disk, filesystem, temperature, uptime, and load metrics.
- Live network throughput and system identity information.
- Optional APT update counts, local weather, and startup speech.
- Dashboard and full-screen Archives views.
- Configurable container selection, refresh intervals, and log formatting.

### Graphical dashboard

- Large metric cards (CPU, RAM, disk, network, uptime) with a current-weather strip.
- CPU and memory readouts turn amber at 70% and red at 90%, on both the system
  metric cards and each service card.
- A grid of per-service status cards (Immich, Navidrome, qBittorrent, slskd,
  Lidarr, Prowlarr, FlareSolverr, AdGuard Home, Gluetun, Portainer, Uptime
  Kuma, Dozzle) showing live health, CPU, and memory.
- Network history graph and storage information.
- All configuration, including weather and theme colours, is done by editing
  the config file directly — there is no in-app settings screen.
- Windowed mode for testing and Cage mode for kiosk-style deployment.

### Port Manager

The standalone service renews Proton VPN UDP/TCP NAT-PMP mappings, authenticates
to qBittorrent's Web API, updates and verifies its listening port, writes a
versioned status document, and records operational events.

Credentials stay in /etc/holocron/port-manager.conf, installed with mode 0600.
They are not written to the status JSON or service log.

## Requirements

### Common/server requirements

- Linux
- Python 3.10 or newer
- Docker CLI and permission to run docker ps, docker stats, and docker logs
- journalctl for host event collection
- curl, jq, and natpmpc for Port Manager
- Optional espeak-ng for startup speech

### Display-client requirements

- Python 3.10 or newer
- PySide6 6.7 or newer
- Cage and Qt's Wayland plugin for kiosk mode
- OpenSSH client with key-based authentication
- Optional IBM Plex Mono or another monospace font

On Ubuntu, the server commonly needs:

    sudo apt update
    sudo apt install python3 curl jq natpmpc espeak-ng

On an Arch Linux display client:

    sudo pacman -S --needed cage pyside6 qt6-wayland openssh ttf-ibm-plex

## Installation

Run the installer from the project directory.

### Server

    sudo ./install.sh --server

This installs the server agent and shared files at /opt/holocron, with
launchers at /usr/local/bin/holocron and /usr/local/bin/holocron-agent.

Allow the server user to read Docker and the system journal:

    sudo usermod -aG docker,systemd-journal "$USER"

Log out and back in after changing group membership. Then create a configuration
and start a local terminal dashboard:

    holocron --init-config
    holocron --dashboard

### Display client

    sudo ./install.sh --client

Configure SSH key authentication and verify the agent before starting the GUI:

    ssh-copy-id USER@SERVER
    ssh -T USER@SERVER /usr/local/bin/holocron-agent | python3 -m json.tool
    holocron-gui --host USER@SERVER --windowed

For a 1920x1080 kiosk display:

    cage -- holocron-gui --host USER@SERVER

Edit start-gui-example.sh to reuse a display launcher. The SSH command uses
BatchMode=yes, so the display must authenticate without prompting.

### Both roles on one machine

    sudo ./install.sh --all

This mode also requires PySide6 to be importable by the system python3.

## Updating

The graphical dashboard has no in-app update checker. To update the display
client, replace `/opt/holocron/gui.py` (and any other changed files) with a
newer checked-out copy, or re-run:

    sudo ./install.sh --client

Update the server the same way, from a checked-out release archive:

    sudo ./install.sh --server

Restart Holocron after an update.

## Configuration

The dashboard configuration is created at:

    ~/.config/holocron/config.json

Create it with --init-config, or use --config PATH to select another file.
The safe example is config.example.json.

Important settings include:

- rotation_seconds, tail_lines, and refresh_seconds for collection and rotation.
- containers to restrict the **terminal** dashboard's log/status rotation to
  selected Docker names. The graphical dashboard's service cards are a fixed
  list defined in gui.py (SERVICE_WIDGETS), not config-driven.
- dashboard_mode and show_all_containers for the initial view and service list.
- weather_enabled, weather_location, and weather_refresh_seconds.
- journal_enabled, journal_tail_lines, and journal_priority.
- log_file for Holocron's local application log.
- gui_font_size for the Qt display.
- speech_enabled, startup_phrase, and startup_audio.

Port Manager currently supports Proton VPN through the `proton` provider
module. Select it in `/etc/holocron/port-manager.conf` with:

    VPN_PROVIDER="proton"

Provider modules use a shared renewal interface, so additional VPN providers
can be added under `port-manager/providers/` without changing qBittorrent or
dashboard code. A provider must be implemented and tested before it can be
selected; changing the setting alone does not add support for a provider.

Example container selection:

    "containers": ["immich_server", "adguardhome", "slskd"]

Leave weather_location empty for automatic detection, or set a city, postcode,
or airport code. Weather is fetched asynchronously and cached. All of this,
along with theme colours (theme_background, theme_panel, theme_primary,
theme_dim, theme_warning, theme_error) and cursor_hide_enabled /
cursor_hide_seconds, is configured by editing the config file directly and
restarting Holocron — the graphical dashboard has no settings screen.

## Command-line options

    holocron --init-config       Create the default config and exit
    holocron --dashboard         Open the live dashboard without the menu
    holocron --no-speech         Disable startup speech for this run
    holocron --config PATH       Use a specific dashboard config
    holocron --version           Print the application version

The agent accepts --config PATH and prints one compact JSON snapshot:

    holocron-agent --config ~/.config/holocron/config.json | python3 -m json.tool

The GUI accepts --host USER@SERVER, --agent-command COMMAND, --config PATH,
and --windowed.

## Controls

### Terminal client

| Key | Action |
| --- | --- |
| A | Toggle Dashboard and Archives views |
| N / P | Next / previous container or source |
| Space | Pause or resume rotation |
| R | Refresh logs |
| S | Replay the startup announcement |
| Q | Quit |

### Graphical client

| Key | Action |
| --- | --- |
| Space | Pause or resume collection |
| R | Request an immediate snapshot |
| F11 | Toggle full screen |
| Q / Escape | Quit |

## Port Manager setup

Install the service on the server:

    sudo apt install curl jq natpmpc
    cd port-manager
    sudo ./install.sh

The installer creates /etc/holocron/port-manager.conf only if it does not
already exist. Edit it and set the qBittorrent Web UI values:

    sudoedit /etc/holocron/port-manager.conf

At minimum:

    QBITTORRENT_URL="http://127.0.0.1:9091"
    QBITTORRENT_USERNAME="your-user"
    QBITTORRENT_PASSWORD="your-password"

The file remains root-owned with mode 0600. Disable qBittorrent's UPnP/NAT-PMP
setting so it does not compete with the Proton mapping, then enable the service:

    sudo systemctl enable --now holocron-port-manager
    systemctl status holocron-port-manager

Inspect the public status and service log:

    jq . /var/lib/holocron/port-manager/status.json
    tail -f /var/log/holocron/port-manager/port-manager.log

The status document contains service, provider, application, and recent-event
information. It does not contain the qBittorrent username or password.

## Logs and runtime data

Holocron's local application log defaults to:

    ~/.local/state/holocron/holocron.log

Port Manager writes runtime data outside the repository:

    /var/lib/holocron/port-manager/status.json
    /var/log/holocron/port-manager/port-manager.log
    /run/holocron-port-manager/

Runtime logs, generated status/events files, local credentials, cookies, and
qBittorrent configuration data are excluded by .gitignore. Only example
configuration files belong in source control.

## Startup audio

To use a local recording:

    "startup_audio": "/home/your-user/.local/share/holocron/startup.wav"

Holocron tries ffplay, mpv, paplay, and aplay, then falls back to espeak-ng.
Set speech_enabled to false, or pass --no-speech, to disable announcements.

## tmux

The included example creates a two-pane dashboard session:

    ./start-dashboard-example.sh
    tmux attach -t dashboard

It starts btop beside holocron --dashboard. Edit the script if your tmux layout
or command names differ.

## Development and tests

The terminal client and server agent use only the Python standard library.
PySide6 is required for the graphical client and is listed in requirements.txt.

Run the automated tests:

    python3 -m unittest discover -v
    bash port-manager/tests/test_modules.sh

Check shell syntax:

    bash -n install.sh port-manager/*.sh port-manager/lib/*.sh
    bash -n port-manager/providers/*.sh port-manager/applications/*.sh

## Repository layout

    holocron.py                         Shared services and terminal entry point
    ui.py, ui_common.py                 Terminal dashboard implementation
    ui_menu.py, ui_settings.py          Terminal menus and settings
    gui.py                              PySide6 graphical dashboard
    updater.py                          GitHub release helpers (unused by gui.py; kept for tests)
    holocron_agent.py                   Server-side JSON collector
    install.sh                          Main server/client installer
    port-manager/                       Proton/qBittorrent service
    config.example.json                 Safe dashboard config example
    port-manager/port-manager.conf.example
                                        Safe Port Manager config example
    test_holocron.py, test_ui.py        Python tests
    port-manager/tests/                 Port Manager module tests

## License

Holocron Dashboard is released under the MIT License. See [LICENSE](LICENSE)
for the complete terms.
