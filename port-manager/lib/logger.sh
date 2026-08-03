#!/usr/bin/env bash

logger_log() {
    local level="$1" message="$2"
    printf '%s | %-5s | %s\n' "$(iso_now)" "$level" "$message" >>"$LOG_DIR/port-manager.log"
}

logger_event() {
    local level="$1" message="$2" timestamp
    timestamp="$(iso_now)"
    logger_log "$level" "$message"
    jq -cn --arg timestamp "$timestamp" --arg level "$level" --arg message "$message" \
        '{timestamp:$timestamp,level:$level,message:$message}' >>"$STATE_DIR/events.jsonl"
    tail -n 50 "$STATE_DIR/events.jsonl" >"$STATE_DIR/events.jsonl.tmp"
    mv -f "$STATE_DIR/events.jsonl.tmp" "$STATE_DIR/events.jsonl"
}
