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

ssh_cmd() {
    ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$@"
}

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
    red "✗"
fi

# Upgrade remotes from machines.json
if [ -f "$MACHINES_JSON" ]; then
    eval "$(python3 -c "
import json
data = json.load(open('$MACHINES_JSON'))
for mid, cfg in data.get('machines', {}).items():
    if 'host' in cfg:
        print(f'DEPLOY_MACHINES+=(\"{mid}\")')
        print(f'DEPLOY_HOST_{mid}=\"{cfg[\"host\"]}\"')
        print(f'DEPLOY_USER_{mid}=\"{cfg[\"user\"]}\"')
")"
    for machine_id in "${DEPLOY_MACHINES[@]:-}"; do
        host_var="DEPLOY_HOST_${machine_id}"
        user_var="DEPLOY_USER_${machine_id}"
        host="${!host_var}"
        user="${!user_var}"
        printf "    %-15s " "$machine_id"
        if ssh_cmd "${user}@${host}" "bash -lc 'uv tool upgrade ccbot'" >/dev/null 2>&1; then
            green "✓"
        else
            red "✗"
        fi
    done
fi

# --- Step 3: Restart bot ---
bold "==> Restarting bot"

restart_bot() {
    local target="$1"
    local via="$2"  # "local" or "ssh user@host"

    if [ "$via" = "local" ]; then
        tmux send-keys -t "${TMUX_SESSION}:${TMUX_WINDOW}" C-c 2>/dev/null || true
        sleep 2
        tmux send-keys -t "${TMUX_SESSION}:${TMUX_WINDOW}" "ccbot" Enter
    else
        $via "tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} C-c 2>/dev/null; sleep 2; tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} 'ccbot' Enter"
    fi
    sleep 3
    green "    Bot restarted on ${target}."
}

# Check if bot is running locally (has ccbot tmux session WITH __main__ window)
if tmux list-windows -t "$TMUX_SESSION" -F '#{window_name}' 2>/dev/null | grep -qx "$TMUX_WINDOW"; then
    echo "    Bot is local, restarting..."
    restart_bot "local" "local"
    exit 0
fi

# Check remotes
if [ -f "$MACHINES_JSON" ]; then
    for machine_id in "${DEPLOY_MACHINES[@]:-}"; do
        host_var="DEPLOY_HOST_${machine_id}"
        user_var="DEPLOY_USER_${machine_id}"
        host="${!host_var}"
        user="${!user_var}"
        if ssh_cmd "${user}@${host}" \
            "tmux list-windows -t $TMUX_SESSION -F '#{window_name}' 2>/dev/null | grep -qx $TMUX_WINDOW" 2>/dev/null; then
            echo "    Bot is on ${machine_id}, restarting..."
            restart_bot "$machine_id" "ssh_cmd ${user}@${host}"
            exit 0
        fi
    done
fi

red "    Could not find running bot on any machine."
exit 1
