#!/usr/bin/env bash

STATE_SERVICE_STATUS="starting"
STATE_PROVIDER_ID="unknown"
STATE_PROVIDER_NAME="Unknown provider"
STATE_PROVIDER_STATUS="unknown"
STATE_PROVIDER_GATEWAY=""
STATE_PROVIDER_MAPPING_LIFETIME_SECONDS=0
STATE_PROVIDER_PROTOCOLS_JSON='[]'
STATE_FORWARDED_PORT=""
STATE_LAST_SUCCESS=""
STATE_NEXT_RENEWAL_EPOCH=0
STATE_ERROR=""
STATE_QBIT_STATUS="unknown"
STATE_QBIT_LISTENING_PORT=""
STATE_QBIT_API_STATUS="not_authenticated"
STATE_QBIT_LAST_AUTH=""
STATE_QBIT_LAST_UPDATE=""
STATE_QBIT_ERROR=""

state_write() {
    local now remaining events status_tmp
    now="$(date +%s)"
    remaining=$(( STATE_NEXT_RENEWAL_EPOCH > now ? STATE_NEXT_RENEWAL_EPOCH - now : 0 ))
    status_tmp="$STATE_DIR/status.json.tmp"
    events='[]'
    if [[ -s "$STATE_DIR/events.jsonl" ]]; then
        events="$(tail -n 12 "$STATE_DIR/events.jsonl" | jq -s 'reverse')" || events='[]'
    fi

    jq -n \
        --arg generated_at "$(iso_now)" \
        --arg service_status "$STATE_SERVICE_STATUS" \
        --argjson pid "$$" \
        --argjson renewal_interval "$RENEWAL_INTERVAL_SECONDS" \
        --argjson seconds_until "$remaining" \
        --arg last_success "$STATE_LAST_SUCCESS" \
        --arg error "$STATE_ERROR" \
        --arg provider_id "$STATE_PROVIDER_ID" \
        --arg provider_name "$STATE_PROVIDER_NAME" \
        --arg provider_status "$STATE_PROVIDER_STATUS" \
        --arg gateway "$STATE_PROVIDER_GATEWAY" \
        --arg forwarded_port "$STATE_FORWARDED_PORT" \
        --argjson lifetime "$STATE_PROVIDER_MAPPING_LIFETIME_SECONDS" \
        --argjson protocols "$STATE_PROVIDER_PROTOCOLS_JSON" \
        --arg qbit_status "$STATE_QBIT_STATUS" \
        --arg qbit_port "$STATE_QBIT_LISTENING_PORT" \
        --arg api_status "$STATE_QBIT_API_STATUS" \
        --arg last_auth "$STATE_QBIT_LAST_AUTH" \
        --arg last_update "$STATE_QBIT_LAST_UPDATE" \
        --arg qbit_error "$STATE_QBIT_ERROR" \
        --argjson events "$events" \
        '{
          schema_version:1,
          application:"holocron-port-manager",
          version:"0.1.0",
          generated_at:$generated_at,
          service:{
            status:$service_status,
            pid:$pid,
            renewal_interval_seconds:$renewal_interval,
            seconds_until_renewal:$seconds_until,
            last_successful_renewal:($last_success | if length>0 then . else null end),
            error:($error | if length>0 then . else null end)
          },
          provider:{
            id:$provider_id,
            name:$provider_name,
            status:$provider_status,
            gateway:$gateway,
            forwarded_port:($forwarded_port | tonumber? // null),
            mapping_lifetime_seconds:$lifetime,
            protocols:$protocols
          },
          applications:[{
            id:"qbittorrent",
            name:"qBittorrent",
            enabled:true,
            status:$qbit_status,
            listening_port:($qbit_port | tonumber? // null),
            api:{status:$api_status,last_authenticated_at:($last_auth | if length>0 then . else null end)},
            last_update:($last_update | if length>0 then . else null end),
            error:($qbit_error | if length>0 then . else null end)
          }],
          recent_events:$events
        }' >"$status_tmp" && mv -f "$status_tmp" "$STATE_DIR/status.json"
}
