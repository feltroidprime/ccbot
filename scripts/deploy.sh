#!/usr/bin/env bash
set -euo pipefail

# Deploy ccbot: push → upgrade all machines → restart bot
# Usage: ./scripts/deploy.sh

MACHINES_JSON="$HOME/.ccbot/machines.json"
REMOTE="fork"
BRANCH="main"
TMUX_SESSION="ccbot"
TMUX_WINDOW="__main__"

# --- Colors ---
green() { printf '\033[32m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }

# --- Step 1: Push ---
bold "==> Pushing to ${REMOTE}/${BRANCH}"
git push "$REMOTE" "$BRANCH"
green "    Pushed."

# --- Step 2: Upgrade all machines ---
bold "==> Upgrading ccbot on all machines"

# Upgrade local
printf "    %-15s " "local"
if uv tool upgrade ccbot >/dev/null 2>&1; then
    green "✓"
else
    red "✗ (uv tool upgrade ccbot failed)"
fi

# Upgrade remotes from machines.json
if [ -f "$MACHINES_JSON" ]; then
    # Parse remote machines (those with a "host" key)
    for machine_id in $(python3 -c "
import json, sys
data = json.load(open('$MACHINES_JSON'))
for mid, cfg in data.get('machines', {}).items():
    if 'host' in cfg:
        print(mid)
"); do
        host=$(python3 -c "
import json; data = json.load(open('$MACHINES_JSON'))
print(data['machines']['$machine_id']['host'])
")
        user=$(python3 -c "
import json; data = json.load(open('$MACHINES_JSON'))
print(data['machines']['$machine_id']['user'])
")
        printf "    %-15s " "$machine_id"
        if ssh -o BatchMode=yes -o ConnectTimeout=5 "${user}@${host}" \
            "bash -lc 'uv tool upgrade ccbot'" >/dev/null 2>&1; then
            green "✓"
        else
            red "✗"
        fi
    done
else
    echo "    No machines.json found, skipping remotes"
fi

# --- Step 3: Restart bot ---
bold "==> Restarting bot"

# Check if bot is running locally
if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "    Bot is local, restarting..."
    tmux send-keys -t "${TMUX_SESSION}:${TMUX_WINDOW}" C-c
    sleep 2
    tmux send-keys -t "${TMUX_SESSION}:${TMUX_WINDOW}" "ccbot" Enter
    sleep 3
    green "    Bot restarted locally."
    exit 0
fi

# Check remotes
if [ -f "$MACHINES_JSON" ]; then
    for machine_id in $(python3 -c "
import json
data = json.load(open('$MACHINES_JSON'))
for mid, cfg in data.get('machines', {}).items():
    if 'host' in cfg:
        print(mid)
"); do
        host=$(python3 -c "
import json; data = json.load(open('$MACHINES_JSON'))
print(data['machines']['$machine_id']['host'])
")
        user=$(python3 -c "
import json; data = json.load(open('$MACHINES_JSON'))
print(data['machines']['$machine_id']['user'])
")
        # Check if this remote has the ccbot tmux session
        if ssh -o BatchMode=yes -o ConnectTimeout=5 "${user}@${host}" \
            "tmux has-session -t $TMUX_SESSION 2>/dev/null" 2>/dev/null; then
            echo "    Bot is on ${machine_id}, restarting..."
            ssh "${user}@${host}" "tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} C-c; sleep 2; tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} 'ccbot' Enter"
            sleep 3
            green "    Bot restarted on ${machine_id}."
            exit 0
        fi
    done
fi

red "    Could not find running bot on any machine."
exit 1
