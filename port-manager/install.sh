#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/holocron-port-manager"

if [[ $EUID -ne 0 ]]; then
    echo "Run with: sudo bash install.sh" >&2
    exit 1
fi

for command in curl jq natpmpc; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing dependency: $command" >&2
        echo "Ubuntu: sudo apt install curl jq natpmpc" >&2
        exit 1
    }
done

install -d -m 0755 "$INSTALL_DIR" "$INSTALL_DIR/providers" \
    "$INSTALL_DIR/applications" "$INSTALL_DIR/lib"
install -m 0755 "$SOURCE_DIR/main.sh" "$INSTALL_DIR/main.sh"
install -m 0644 "$SOURCE_DIR/providers/proton.sh" "$INSTALL_DIR/providers/proton.sh"
install -m 0644 "$SOURCE_DIR/applications/qbittorrent.sh" "$INSTALL_DIR/applications/qbittorrent.sh"
install -m 0644 "$SOURCE_DIR/lib/"*.sh "$INSTALL_DIR/lib/"

install -d -m 0755 /etc/holocron
if [[ ! -e /etc/holocron/port-manager.conf ]]; then
    install -m 0600 "$SOURCE_DIR/port-manager.conf.example" /etc/holocron/port-manager.conf
    echo "Created /etc/holocron/port-manager.conf — add the qBittorrent credentials before starting."
else
    chmod 0600 /etc/holocron/port-manager.conf
    echo "Preserved existing /etc/holocron/port-manager.conf"
fi

install -d -m 0755 /var/lib/holocron/port-manager
install -d -m 0750 /var/log/holocron/port-manager
install -m 0644 "$SOURCE_DIR/systemd/holocron-port-manager.service" \
    /etc/systemd/system/holocron-port-manager.service
systemctl daemon-reload

echo "Installed Holocron Port Manager."
echo "Next: edit /etc/holocron/port-manager.conf"
echo "Then: sudo systemctl enable --now holocron-port-manager"
