#!/usr/bin/env bash

load_config() {
    local config_file="$1"
    if [[ ! -r "$config_file" ]]; then
        printf 'ERROR: Configuration file is not readable: %s\n' "$config_file" >&2
        return 1
    fi
    # This is a root-owned shell configuration file installed with mode 0600.
    # shellcheck disable=SC1090
    source "$config_file"

    : "${PROTON_GATEWAY:=10.2.0.1}"
    : "${PROTON_PRIVATE_PORT:=1}"
    : "${PROTON_MAPPING_LIFETIME_SECONDS:=60}"
    : "${RENEWAL_INTERVAL_SECONDS:=45}"
    : "${QBITTORRENT_URL:=http://127.0.0.1:9091}"
    QBITTORRENT_URL="${QBITTORRENT_URL%/}"
    : "${QBITTORRENT_USERNAME:?Set QBITTORRENT_USERNAME in $config_file}"
    : "${QBITTORRENT_PASSWORD:?Set QBITTORRENT_PASSWORD in $config_file}"
    : "${STATE_DIR:=/var/lib/holocron/port-manager}"
    : "${LOG_DIR:=/var/log/holocron/port-manager}"
    : "${RUNTIME_DIR:=/run/holocron-port-manager}"
}

require_commands() {
    local command missing=0
    for command in "$@"; do
        if ! command -v "$command" >/dev/null 2>&1; then
            printf 'ERROR: Required command not found: %s\n' "$command" >&2
            missing=1
        fi
    done
    (( missing == 0 ))
}

initialise_runtime() {
    install -d -m 0755 "$STATE_DIR"
    install -d -m 0750 "$LOG_DIR"
    install -d -m 0700 "$RUNTIME_DIR"
}

acquire_instance_lock() {
    exec 9>"$RUNTIME_DIR/port-manager.lock"
    flock -n 9 || {
        printf 'ERROR: Holocron Port Manager is already running.\n' >&2
        exit 1
    }
}

valid_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

iso_now() {
    date --iso-8601=seconds
}

single_line() {
    tr '\r\n' '  ' <<<"$1" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//'
}
