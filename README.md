# Holocron Dashboard v0.5.0

Holocron now includes two interfaces:

- `holocron-gui`: the native Qt dashboard, designed specifically for a
  wall-mounted 1920×1080 TV.
- `holocron`: the original curses interface for ordinary terminals and SSH.

The graphical dashboard recreates the control-room design with five live
metric cards, a full-width current-weather strip, Docker container matrix,
rotating server event stream, network history graph, and keyboard command footer. The
GUI runs on the display client under Cage; a small JSON agent runs on the
server over SSH. No X11 or Wayland forwarding is used.

A lightweight, retro-green homelab dashboard with system information,
Docker service health, and rotating live logs.

It was built for a large terminal dashboard, tmux pane, spare monitor, or
slightly diabolical Sony Bravia guarded by LEGO Yoda.

## Features

- Dashboard-first layout with system status across the top and live logs below.
- TV-scale control-matrix layout with balanced upper and lower decks.
- Automatically cycles through running Docker containers.
- Shows running, healthy, unhealthy, and stopped container totals.
- Displays a compact service-health grid.
- Includes a full-screen Archives log view.
- Displays recent logs and refreshes them continuously.
- Rotates every 30 seconds through every running Docker container and the
  server journal, with the current service name shown above the feed.
- Formats logs into aligned timestamp, level, and message columns.
- Highlights success, warning, error, and fatal events.
- Removes ANSI control codes and noisy level prefixes.
- Records Holocron actions in a separate application log.
- Shows CPU, RAM, disk, live network usage, time, and rotation countdown.
- Shows physical filesystem capacity, temperature sensors, and kernel details.
- Shows a cached three-day local forecast in the system panel.
- IBM-style green terminal presentation.
- Optional startup announcement:
  `Holocron started, it has.`
- Keeps the server agent and terminal interface on the Python standard library.
- Adds a native PySide6/Qt interface for the dedicated TV client.
- Runs well inside tmux.

## Terminal/server requirements

- Linux
- Python 3.10+
- Docker CLI
- Permission to run `docker ps` and `docker logs`
- Optional: `espeak-ng` for synthesized startup speech

The graphical display client additionally requires Cage, PySide6, Qt's Wayland
plugin, OpenSSH, and preferably IBM Plex Mono.

On Ubuntu:

```bash
sudo apt update
sudo apt install python3 espeak-ng
```

Your user may need Docker access:

```bash
sudo usermod -aG docker "$USER"
```

Log out and back in after changing Docker group membership.

## Quick start

```bash
chmod +x holocron.py
./holocron.py --init-config
./holocron.py
```

For a dedicated display that should skip the menu:

```bash
./holocron.py --dashboard
```

Or:

```bash
python3 holocron.py
```

The project keeps its application services in `holocron.py` and splits the
curses interface by responsibility:

- `ui.py` contains the dashboard controller and the stable UI entry points.
- `ui_common.py` contains shared UI state, layout constants, and safe drawing
  helpers.
- `ui_menu.py` contains the main-menu flow.
- `ui_settings.py` contains the settings-screen renderer.

## Controls

- `A`: toggle Dashboard and Archives views
- `N`: next container
- `P`: previous container
- `Space`: pause or resume rotation
- `R`: refresh logs
- `S`: replay startup announcement
- `Q`: quit

## Configuration

The default configuration is created at:

```text
~/.config/holocron/config.json
```

Example:

```json
{
  "rotation_seconds": 15,
  "tail_lines": 120,
  "refresh_seconds": 1.0,
  "containers": null,
  "title": "JEDI ARCHIVES",
  "startup_phrase": "Holocron started, it has.",
  "startup_audio": "",
  "speech_enabled": true,
  "show_timestamps": true,
  "log_file": "~/.local/state/holocron/holocron.log",
  "simplify_messages": true,
  "dashboard_mode": true,
  "show_all_containers": true,
  "weather_enabled": true,
  "weather_location": "",
  "weather_refresh_seconds": 900,
  "gui_font_size": 16,
  "update_refresh_seconds": 900,
  "journal_enabled": true,
  "journal_tail_lines": 100,
  "journal_refresh_seconds": 2.0,
  "journal_priority": "info"
}
```

To show only selected containers:

```json
"containers": [
  "immich_server",
  "adguardhome",
  "slskd"
]
```

The names must match `docker ps --format '{{.Names}}'`.

### Local weather

Weather forecasts are fetched asynchronously from [wttr.in](https://github.com/chubin/wttr.in)
and cached, so they do not block dashboard refreshes. Leave `weather_location`
empty to use IP-based location detection, or set a city, postcode, or airport
code:

```json
"weather_location": "Burnie"
```

Set `weather_enabled` to `false` to disable the network request and hide
the forecast from the system panel. `weather_refresh_seconds` controls the cache interval
and has a minimum of 60 seconds.

### Graphical settings

Press `S` in the graphical dashboard to open the TV-friendly settings menu.
The menu changes the dashboard font size and weather location. Choose **Save**
to apply the font immediately, fetch the new location immediately, and write
the choices to `~/.config/holocron/config.json` for future Cage launches.
Press `R` to refresh both server data and weather immediately.


## Dashboard layout

Holocron now starts in a dashboard view designed for a dedicated homelab
monitor. The upper section stays calm and readable while the lower section
scans through live Docker and server journal sources every 30 seconds.

The dashboard includes:

- CPU, RAM, disk, temperature, load average, and uptime
- Threshold alerts for CPU, RAM, disk space, and temperature
- Pending Ubuntu package update count (APT)
- Docker totals and container health
- A service grid for quick fault spotting
- Active log source, rotation state, and countdown
- One fixed scrolling feed rotating through every running Docker service and
  the server `journalctl` stream every 30 seconds
- A prominent current-service name, source position, and next-source countdown
- A storage matrix for `/`, `/home`, `/srv`, `/mnt`, and `/media` mounts

Press `A` to switch to the full-screen **Archives** log view. Set
`dashboard_mode` to `false` if you prefer Archives as the startup view. Set
`show_all_containers` to `false` to hide stopped containers from the service
grid.

Those character dimensions apply only to the legacy terminal view. The native
GUI is laid out for the TV's 1920×1080 pixel output.

### Dedicated 1080p TV client with Cage and Qt

On the Ubuntu server, install the collector and ensure the SSH user can read
Docker and system journal data:

```bash
sudo ./install.sh --server
sudo usermod -aG docker,systemd-journal "$USER"
holocron-agent | python3 -m json.tool
```

Log out and back in after changing groups. The last command should print one
JSON snapshot.

On the Arch display client:

```bash
sudo pacman -S --needed cage pyside6 qt6-wayland openssh ttf-ibm-plex
sudo ./install.sh --client
ssh-copy-id tj@jedi-archives
cage -- holocron-gui --host tj@jedi-archives
```

Replace `tj@jedi-archives` with the correct SSH destination. Key authentication
is required for unattended use. Foot is not involved in the graphical launch.
The GUI targets 1920×1080 directly and scales with Qt if the output differs.

## Holocron Port Manager

v0.5.0 includes a standalone Bash service under `port-manager/`. It maintains
Proton VPN NAT-PMP UDP and TCP mappings every 45 seconds, updates only
qBittorrent through its authenticated Web API, reads the preference back to
verify the new listening port, and records its work in a log.

The service does not import or call dashboard code. Its only public contract is:

```text
/var/lib/holocron/port-manager/status.json
```

The server agent reads that JSON and the graphical dashboard displays it on a
separate page. The JSON contains a generic `applications` array, so future
managed applications appear as new rows without qBittorrent-specific dashboard
changes.

Install on the Ubuntu server:

```bash
sudo apt install curl jq natpmpc
sudo bash install.sh --server
cd port-manager
sudo bash install.sh
sudoedit /etc/holocron/port-manager.conf
sudo systemctl enable --now holocron-port-manager
```

Set the existing qBittorrent Web UI URL, username, and password in the config.
The config is installed with mode `0600`; credentials are never exposed in the
status JSON or log. qBittorrent's own UPnP/NAT-PMP option should be disabled so
it does not compete with the managed Proton mapping.

Verify the service and public state:

```bash
systemctl status holocron-port-manager
jq . /var/lib/holocron/port-manager/status.json
tail -f /var/log/holocron/port-manager/port-manager.log
```

Update the display client with `sudo bash install.sh --client`. Its existing
Cage launch command does not change.

Verify the complete remote data path from the display client with:

```bash
ssh -T tj@jedi-archives /usr/local/bin/holocron-agent | python3 -m json.tool
```

If that command does not print a JSON object, fix the reported SSH or server
agent error before starting Cage.

To test it without taking over the display:

```bash
holocron-gui --host tj@jedi-archives --windowed
```

The equivalent reusable launcher is `start-gui-example.sh`.

Graphical controls:

- `P`: open Port Manager
- `D`: return to Dashboard
- `Space`: pause or resume data collection
- `R`: request an immediate snapshot
- `F11`: toggle full screen
- `Q` or `Escape`: quit

The event stream is collected entirely on the Ubuntu server and never includes
logs from the display client. Each running Docker container is shown separately,
followed by the server system journal. It uses the server user's journal permissions. If
it only shows a limited selection on Ubuntu, add the server user to `systemd-journal`
and log out and back in:

```bash
sudo usermod -aG systemd-journal "$USER"
```

## Log presentation

Docker output is displayed in a consistent format:

```text
22:14:03 │ INFO    │ Configuration loaded
22:14:04 │ SUCCESS │ Service listening on port 8080
22:14:05 │ WARN    │ Retrying database connection
22:14:06 │ ERROR   │ Connection refused
```

Set `simplify_messages` to `false` to preserve original prefixes and spacing.
Docker log timestamps are shortened to `HH:MM:SS` for readability.

Holocron's own actions, such as container rotation, pause/resume, manual refresh,
and shutdown, are written to:

```text
~/.local/state/holocron/holocron.log
```

The path can be changed with `log_file`.

## Using your own voice recording

Record your own line and set:

```json
"startup_audio": "/home/your-user/.local/share/holocron/startup.wav"
```

Holocron tries `ffplay`, `mpv`, `paplay`, and `aplay`, in that order.
When a valid audio file is configured, it is preferred over text-to-speech.

The included synthesized speech is deliberately generic rather than an
imitation of any actor or character voice.

## tmux

Run Holocron inside a tmux pane:

```bash
tmux new-window -n holocron '/opt/holocron/holocron.py'
```

Or add this command to your existing dashboard startup script:

```bash
tmux send-keys -t dashboard:0.2 '/opt/holocron/holocron.py' C-m
```

Adjust the target pane to match your layout.

## Install system-wide

```bash
sudo ./install.sh
holocron --init-config
holocron
```

This installs the executable to `/opt/holocron/holocron.py` and creates
`/usr/local/bin/holocron`.

## Troubleshooting

### Permission denied when accessing Docker

Check:

```bash
docker ps
```

If that fails, fix Docker permissions before running Holocron.

### No startup speech

Check:

```bash
command -v espeak-ng
```

Then test:

```bash
espeak-ng "Holocron started, it has."
```

### Garbled terminal

Use a UTF-8 locale and a terminal with Unicode support. Resize the terminal to
at least 50 columns by 10 rows.
# Holocron-Dashboard
