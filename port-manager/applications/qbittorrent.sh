#!/usr/bin/env bash

QBIT_COOKIE_FILE=""

qbittorrent_open_session() {
    QBIT_COOKIE_FILE="$(mktemp "${RUNTIME_DIR}/qbittorrent-cookie.XXXXXX")" || return 1
    chmod 600 "$QBIT_COOKIE_FILE"
    local response
    response="$(curl --silent --show-error --fail \
        --header "Referer: $QBITTORRENT_URL/" \
        --cookie-jar "$QBIT_COOKIE_FILE" \
        --data-urlencode "username=$QBITTORRENT_USERNAME" \
        --data-urlencode "password=$QBITTORRENT_PASSWORD" \
        "$QBITTORRENT_URL/api/v2/auth/login" 2>/dev/null)" || return 1
    [[ "$response" == "Ok." ]]
}

qbittorrent_listening_port() {
    curl --silent --show-error --fail \
        --header "Referer: $QBITTORRENT_URL/" \
        --cookie "$QBIT_COOKIE_FILE" \
        "$QBITTORRENT_URL/api/v2/app/preferences" 2>/dev/null \
        | jq -er '.listen_port | numbers | tostring'
}

qbittorrent_set_listening_port() {
    local port="$1"
    valid_port "$port" || return 1
    curl --silent --show-error --fail \
        --header "Referer: $QBITTORRENT_URL/" \
        --cookie "$QBIT_COOKIE_FILE" \
        --data-urlencode "json={\"listen_port\":$port}" \
        "$QBITTORRENT_URL/api/v2/app/setPreferences" \
        >/dev/null 2>&1
}

qbittorrent_close_session() {
    if [[ -n "$QBIT_COOKIE_FILE" && -f "$QBIT_COOKIE_FILE" ]]; then
        curl --silent --header "Referer: $QBITTORRENT_URL/" \
            --cookie "$QBIT_COOKIE_FILE" \
            "$QBITTORRENT_URL/api/v2/auth/logout" >/dev/null 2>&1 || true
        rm -f -- "$QBIT_COOKIE_FILE"
    fi
    QBIT_COOKIE_FILE=""
}
