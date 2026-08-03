#!/usr/bin/env bash

PROVIDER_ID="proton"
PROVIDER_NAME="Proton VPN"
PROVIDER_REQUIRED_COMMANDS="natpmpc"
STATE_PROVIDER_ID="$PROVIDER_ID"
STATE_PROVIDER_NAME="$PROVIDER_NAME"
STATE_PROVIDER_GATEWAY="${PROTON_GATEWAY:-}"
STATE_PROVIDER_MAPPING_LIFETIME_SECONDS="${PROTON_MAPPING_LIFETIME_SECONDS:-0}"
STATE_PROVIDER_PROTOCOLS_JSON='["udp","tcp"]'

_proton_map_protocol() {
    local protocol="$1" requested_public_port="$2" output port
    if ! output="$(natpmpc -a "$PROTON_PRIVATE_PORT" "$requested_public_port" \
        "$protocol" "$PROTON_MAPPING_LIFETIME_SECONDS" \
        -g "$PROTON_GATEWAY" 2>&1)"; then
        logger_log "ERROR" "natpmpc $protocol failed: $(single_line "$output")"
        return 1
    fi

    port="$(sed -nE 's/.*Mapped public port ([0-9]+).*/\1/p' <<<"$output" | tail -n 1)"
    if ! valid_port "$port"; then
        logger_log "ERROR" "Could not parse forwarded port from natpmpc output"
        return 1
    fi

    printf '%s\n' "$port"
}

provider_renew_mapping() {
    local requested_public_port udp_port tcp_port
    requested_public_port="${STATE_FORWARDED_PORT:-0}"

    udp_port="$(_proton_map_protocol udp "$requested_public_port")" || return 1
    tcp_port="$(_proton_map_protocol tcp "$udp_port")" || return 1
    if [[ "$tcp_port" != "$udp_port" ]]; then
        logger_log "ERROR" "Proton returned different UDP ($udp_port) and TCP ($tcp_port) ports"
        return 1
    fi
    printf '%s\n' "$udp_port"
}
