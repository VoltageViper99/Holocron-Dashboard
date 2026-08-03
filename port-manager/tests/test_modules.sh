#!/usr/bin/env bash
set -euo pipefail

TEST_ROOT="$(mktemp -d /tmp/holocron-port-manager-test.XXXXXX)"
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=../lib/utils.sh
source "$PROJECT_DIR/lib/utils.sh"
# shellcheck source=../lib/logger.sh
source "$PROJECT_DIR/lib/logger.sh"
# shellcheck source=../lib/state.sh
source "$PROJECT_DIR/lib/state.sh"
# shellcheck source=../providers/proton.sh
source "$PROJECT_DIR/providers/proton.sh"
# shellcheck source=../applications/qbittorrent.sh
source "$PROJECT_DIR/applications/qbittorrent.sh"

STATE_DIR="$TEST_ROOT/state"
LOG_DIR="$TEST_ROOT/logs"
RUNTIME_DIR="$TEST_ROOT/run"
mkdir -p "$STATE_DIR" "$LOG_DIR" "$RUNTIME_DIR"
PROTON_GATEWAY="10.2.0.1"
PROTON_PRIVATE_PORT=1
PROTON_MAPPING_LIFETIME_SECONDS=60
RENEWAL_INTERVAL_SECONDS=45
STATE_FORWARDED_PORT=""

natpmpc() {
    printf 'Mapped public port 58321 protocol %s to local port 1 lifetime 60\n' "$4"
}

mapped_port="$(provider_renew_mapping)"
[[ "$mapped_port" == "58321" ]]

QBITTORRENT_URL="http://127.0.0.1:9091"
QBITTORRENT_USERNAME="test-user"
QBITTORRENT_PASSWORD="test-password"
FAKE_QBIT_PORT=53124
curl() {
    local argument endpoint="${*: -1}"
    case "$endpoint" in
        */api/v2/auth/login)
            printf 'Ok.'
            ;;
        */api/v2/app/preferences)
            printf '{"listen_port":%s}\n' "$FAKE_QBIT_PORT"
            ;;
        */api/v2/app/setPreferences)
            for argument in "$@"; do
                if [[ "$argument" =~ listen_port[^0-9]*([0-9]+) ]]; then
                    FAKE_QBIT_PORT="${BASH_REMATCH[1]}"
                fi
            done
            ;;
        */api/v2/auth/logout)
            ;;
        *)
            return 1
            ;;
    esac
}

qbittorrent_open_session
[[ "$(qbittorrent_listening_port)" == "53124" ]]
qbittorrent_set_listening_port 58321
[[ "$(qbittorrent_listening_port)" == "58321" ]]
qbittorrent_close_session

STATE_SERVICE_STATUS="running"
STATE_PROVIDER_STATUS="connected"
STATE_FORWARDED_PORT=58321
STATE_LAST_SUCCESS="2026-08-03T18:22:00+10:00"
STATE_NEXT_RENEWAL_EPOCH=$(( $(date +%s) + 32 ))
STATE_QBIT_STATUS="connected"
STATE_QBIT_LISTENING_PORT=58321
STATE_QBIT_API_STATUS="authenticated"
STATE_QBIT_LAST_AUTH="2026-08-03T18:22:00+10:00"
STATE_QBIT_LAST_UPDATE="2026-08-03T18:22:00+10:00"
logger_event INFO "Renewal successful"
state_write

jq -e '
  .schema_version == 1 and
  .provider.forwarded_port == 58321 and
  .provider.id == "proton" and
  .provider.name == "Proton VPN" and
  .provider.protocols == ["udp", "tcp"] and
  .applications[0].id == "qbittorrent" and
  .applications[0].listening_port == 58321 and
  .recent_events[0].message == "Renewal successful"
' "$STATE_DIR/status.json" >/dev/null

echo "Port Manager module tests passed."
