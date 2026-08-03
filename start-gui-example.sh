#!/usr/bin/env bash
# Run this on the dedicated Arch display client from a TTY.

set -euo pipefail

SERVER="tj@jedi-archives"

exec cage -- holocron-gui --host "$SERVER"
