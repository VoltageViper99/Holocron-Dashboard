#!/usr/bin/env bash
# Example: launch Holocron in a dedicated tmux dashboard session.

set -euo pipefail

SESSION="dashboard"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists."
  exit 0
fi

tmux new-session -d -s "$SESSION" -n "noc" "btop"
tmux split-window -h -t "$SESSION:0" "holocron --dashboard"
tmux select-layout -t "$SESSION:0" even-horizontal

echo "Dashboard created. Attach with:"
echo "  tmux attach -t $SESSION"
