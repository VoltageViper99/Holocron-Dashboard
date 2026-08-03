#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/holocron"
BIN_LINK="/usr/local/bin/holocron"
MODE="${1:---all}"

REQUIRED_FILES=(
  holocron.py
  holocron_agent.py
  gui.py
  updater.py
  ui.py
  ui_common.py
  ui_menu.py
  ui_settings.py
)

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo:"
  echo "  sudo ./install.sh"
  exit 1
fi

echo "Installing Holocron from: $SOURCE_DIR"

for file in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "$SOURCE_DIR/$file" ]]; then
    echo "ERROR: Required file is missing: $SOURCE_DIR/$file" >&2
    echo "Extract the complete ZIP, then run install.sh from inside that folder." >&2
    exit 1
  fi
done

if [[ "$MODE" != "--server" && "$MODE" != "--client" && "$MODE" != "--all" ]]; then
  echo "Usage: sudo ./install.sh [--server|--client|--all]" >&2
  exit 1
fi

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$SOURCE_DIR/holocron.py" "$INSTALL_DIR/holocron.py"
install -m 0755 "$SOURCE_DIR/holocron_agent.py" "$INSTALL_DIR/holocron_agent.py"
install -m 0755 "$SOURCE_DIR/gui.py" "$INSTALL_DIR/gui.py"
install -m 0644 "$SOURCE_DIR/updater.py" "$INSTALL_DIR/updater.py"
install -m 0644 \
  "$SOURCE_DIR/ui.py" \
  "$SOURCE_DIR/ui_common.py" \
  "$SOURCE_DIR/ui_menu.py" \
  "$SOURCE_DIR/ui_settings.py" \
  "$INSTALL_DIR/"
ln -sfn "$INSTALL_DIR/holocron.py" "$BIN_LINK"
ln -sfn "$INSTALL_DIR/holocron_agent.py" "/usr/local/bin/holocron-agent"

if [[ "$MODE" != "--server" ]]; then
  if ! python3 -c 'import PySide6' 2>/dev/null; then
    echo "ERROR: PySide6 is not available to python3." >&2
    echo "On Arch Linux, install it with: sudo pacman -S --needed pyside6" >&2
    exit 1
  fi
  ln -sfn "$INSTALL_DIR/gui.py" "/usr/local/bin/holocron-gui"
fi

if [[ ! -x /usr/local/bin/holocron-agent || ! -f "$INSTALL_DIR/holocron_agent.py" ]]; then
  echo "ERROR: Server agent installation verification failed." >&2
  exit 1
fi

echo "Installed Holocron successfully."
echo "  Server agent: /usr/local/bin/holocron-agent"
if [[ "$MODE" != "--server" ]]; then
  echo "  GUI launcher: /usr/local/bin/holocron-gui"
  echo "  GUI program:  /opt/holocron/gui.py"
fi
