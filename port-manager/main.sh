#!/usr/bin/env bash
set -uo pipefail

# Holocron Port Manager orchestrator. Provider, application, logging, and
# persistence details deliberately live in their own modules.

readonly APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/utils.sh
source "$APP_DIR/lib/utils.sh"
# shellcheck source=lib/logger.sh
source "$APP_DIR/lib/logger.sh"
# shellcheck source=lib/state.sh
source "$APP_DIR/lib/state.sh"
# shellcheck source=providers/proton.sh
source "$APP_DIR/providers/proton.sh"
# shellcheck source=applications/qbittorrent.sh
source "$APP_DIR/applications/qbittorrent.sh"

CONFIG_FILE="${HOLOCRON_PORT_MANAGER_CONFIG:-/etc/holocron/port-manager.conf}"

main() {
    load_config "$CONFIG_FILE" || exit 1
    require_commands curl jq natpmpc flock date tail mktemp mv sed tr install sleep rm || exit 1
    initialise_runtime || exit 1
    acquire_instance_lock

    trap 'handle_shutdown' INT TERM EXIT
    logger_event "INFO" "Port Manager started"

    local next_renewal=0
    local now forwarded_port previous_port current_port
    local renewal_ok

    while true; do
        now="$(date +%s)"
        if (( now >= next_renewal )); then
            logger_log "INFO" "Starting Proton NAT-PMP renewal"
            renewal_ok=false
            STATE_SERVICE_STATUS="running"
            STATE_PROVIDER_STATUS="connecting"
            STATE_ERROR=""

            if forwarded_port="$(proton_renew_mapping)"; then
                renewal_ok=true
                STATE_PROVIDER_STATUS="connected"
                previous_port="$STATE_FORWARDED_PORT"
                STATE_FORWARDED_PORT="$forwarded_port"
                STATE_LAST_SUCCESS="$(iso_now)"

                if [[ -n "$previous_port" && "$previous_port" != "$forwarded_port" ]]; then
                    logger_event "INFO" "Port changed $previous_port → $forwarded_port"
                elif [[ -z "$previous_port" ]]; then
                    logger_event "INFO" "Forwarded port acquired: $forwarded_port"
                fi

                if qbittorrent_open_session; then
                    logger_log "INFO" "Authenticated with qBittorrent Web API"
                    STATE_QBIT_API_STATUS="authenticated"
                    STATE_QBIT_LAST_AUTH="$(iso_now)"
                    if current_port="$(qbittorrent_listening_port)"; then
                        STATE_QBIT_LISTENING_PORT="$current_port"
                        if [[ "$current_port" != "$forwarded_port" ]]; then
                            if qbittorrent_set_listening_port "$forwarded_port"; then
                                if current_port="$(qbittorrent_listening_port)" && [[ "$current_port" == "$forwarded_port" ]]; then
                                    STATE_QBIT_STATUS="connected"
                                    STATE_QBIT_LISTENING_PORT="$current_port"
                                    STATE_QBIT_LAST_UPDATE="$(iso_now)"
                                    STATE_QBIT_ERROR=""
                                    logger_event "INFO" "Updated qBittorrent listening port to $forwarded_port"
                                else
                                    STATE_QBIT_STATUS="error"
                                    STATE_QBIT_ERROR="qBittorrent did not accept port $forwarded_port"
                                    logger_event "ERROR" "$STATE_QBIT_ERROR"
                                fi
                            else
                                STATE_QBIT_STATUS="error"
                                STATE_QBIT_ERROR="qBittorrent port update failed"
                                logger_event "ERROR" "$STATE_QBIT_ERROR"
                            fi
                        else
                            STATE_QBIT_STATUS="connected"
                            STATE_QBIT_ERROR=""
                            logger_log "INFO" "qBittorrent already uses listening port $forwarded_port"
                        fi
                    else
                        STATE_QBIT_STATUS="error"
                        STATE_QBIT_ERROR="Could not read qBittorrent preferences"
                        logger_event "ERROR" "$STATE_QBIT_ERROR"
                    fi
                    qbittorrent_close_session
                else
                    qbittorrent_close_session
                    STATE_QBIT_STATUS="disconnected"
                    STATE_QBIT_API_STATUS="authentication_failed"
                    STATE_QBIT_ERROR="qBittorrent Web API authentication failed"
                    logger_event "ERROR" "$STATE_QBIT_ERROR"
                fi

                logger_event "INFO" "Proton NAT-PMP renewal successful"
            else
                STATE_PROVIDER_STATUS="disconnected"
                STATE_ERROR="Proton NAT-PMP renewal failed"
                logger_event "ERROR" "$STATE_ERROR"
            fi

            next_renewal=$(( now + RENEWAL_INTERVAL_SECONDS ))
            STATE_NEXT_RENEWAL_EPOCH="$next_renewal"
            $renewal_ok || STATE_SERVICE_STATUS="degraded"
        fi

        state_write
        sleep 1
    done
}

handle_shutdown() {
    local exit_code=$?
    trap - INT TERM EXIT
    STATE_SERVICE_STATUS="stopped"
    STATE_NEXT_RENEWAL_EPOCH=0
    state_write 2>/dev/null || true
    logger_event "INFO" "Port Manager stopped" 2>/dev/null || true
    exit "$exit_code"
}

main "$@"
