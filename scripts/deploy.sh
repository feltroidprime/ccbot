#!/usr/bin/env bash
set -euo pipefail

# Deploy ccbot: push → upgrade all machines → restart bot
# Usage: ./scripts/deploy.sh
#
# First run: creates ~/.ccbot/deploy.conf with bot host.
# All machine info comes from `tailscale status` at runtime.
# No Tailscale hostnames are stored in the repo.

DEPLOY_CONF="$HOME/.ccbot/deploy.conf"
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

# --- Discover machines from Tailscale ---
get_tailscale_machines() {
    # Returns lines of: hostname ip is_self
    python3 -c "
import json, subprocess, sys
r = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True)
if r.returncode != 0:
    sys.exit(1)
data = json.loads(r.stdout)
self_node = data.get('Self', {})
if self_node:
    name = self_node.get('DNSName', '').rstrip('.')
    ip = self_node.get('TailscaleIPs', [''])[0]
    if name: print(f'{name} {ip} self')
for peer in data.get('Peer', {}).values():
    if not peer.get('Online', False):
        continue
    name = peer.get('DNSName', '').rstrip('.')
    ip = peer.get('TailscaleIPs', [''])[0]
    if name: print(f'{name} {ip} peer')
" 2>/dev/null
}

# --- First-run setup ---
if [ ! -f "$DEPLOY_CONF" ]; then
    bold "==> First-time deploy setup"
    echo "    Scanning Tailscale network..."

    machines=()
    while IFS=' ' read -r hostname ip role; do
        machines+=("$hostname")
        echo "    ${#machines[@]}) $hostname $([ "$role" = "self" ] && echo "(this machine)" || echo "")"
    done < <(get_tailscale_machines)

    if [ ${#machines[@]} -eq 0 ]; then
        red "    No Tailscale peers found. Is Tailscale running?"
        exit 1
    fi

    echo ""
    read -rp "    Which machine runs the bot? [1-${#machines[@]}]: " choice
    bot_host="${machines[$((choice - 1))]}"

    read -rp "    SSH user for remote machines [$(whoami)]: " ssh_user
    ssh_user="${ssh_user:-$(whoami)}"

    mkdir -p "$(dirname "$DEPLOY_CONF")"
    cat > "$DEPLOY_CONF" <<CONF
BOT_HOST=$bot_host
SSH_USER=$ssh_user
CONF
    green "    Saved to $DEPLOY_CONF"
    echo ""
fi

# shellcheck source=/dev/null
source "$DEPLOY_CONF"

# --- Step 1: Push ---
bold "==> Pushing to ${REMOTE}/${BRANCH}"
git push "$REMOTE" "$BRANCH"
green "    Pushed."

# --- Step 2: Upgrade all machines ---
bold "==> Upgrading ccbot on all machines"

# Get self hostname
self_host=""
while IFS=' ' read -r hostname ip role; do
    if [ "$role" = "self" ]; then
        self_host="$hostname"
    fi
done < <(get_tailscale_machines)

# Upgrade local
printf "    %-20s " "${self_host:-local}"
if uv tool upgrade ccbot >/dev/null 2>&1; then
    green "✓"
else
    red "✗"
fi

# Upgrade all online peers (skip self, bot host handled separately, skip phones)
while IFS=' ' read -r hostname ip role; do
    [ "$role" = "self" ] && continue
    [ "$hostname" = "$BOT_HOST" ] && continue
    # Skip devices unlikely to have SSH (phones, tablets)
    case "$hostname" in iphone*|ipad*|pixel*|android*) continue;; esac
    printf "    %-20s " "$hostname"
    if ssh_cmd "${SSH_USER}@${hostname}" "bash -lc 'uv tool upgrade ccbot'" >/dev/null 2>&1; then
        green "✓"
    else
        red "✗"
    fi
done < <(get_tailscale_machines)

# Ensure bot host is upgraded (in case it was skipped or timed out above)
if [ "$BOT_HOST" != "$self_host" ]; then
    printf "    %-20s " "$BOT_HOST (bot)"
    if ssh_cmd "${SSH_USER}@${BOT_HOST}" "bash -lc 'uv tool upgrade ccbot'" >/dev/null 2>&1; then
        green "✓"
    else
        red "✗"
    fi
fi

# --- Step 3: Restart bot ---
bold "==> Restarting bot"

restart_local() {
    tmux send-keys -t "${TMUX_SESSION}:${TMUX_WINDOW}" C-c 2>/dev/null || true
    sleep 2
    tmux send-keys -t "${TMUX_SESSION}:${TMUX_WINDOW}" "ccbot" Enter
    sleep 3
    green "    Bot restarted locally."
}

restart_remote() {
    local host="$1"
    ssh_cmd "${SSH_USER}@${host}" \
        "tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} C-c 2>/dev/null; sleep 2; tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} 'ccbot' Enter"
    sleep 3
    green "    Bot restarted on ${host}."
}

if [ "$BOT_HOST" = "$self_host" ]; then
    echo "    Bot is local ($BOT_HOST)"
    restart_local
else
    echo "    Bot is on $BOT_HOST"
    restart_remote "$BOT_HOST"
fi
