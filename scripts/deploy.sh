#!/usr/bin/env bash
set -euo pipefail

# Deploy ccbot: push → ccbot setup on bot host → restart bot
# Usage: ./scripts/deploy.sh
#
# First run: scans Tailscale, asks which machine runs the bot.
# Upgrade: SSHs into bot host and runs `ccbot setup` (which upgrades all machines
# using the existing machines.json on that host).

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

# --- First-run setup ---
if [ ! -f "$DEPLOY_CONF" ]; then
    bold "==> First-time deploy setup"
    echo "    Scanning Tailscale network..."

    machines=()
    while IFS=' ' read -r hostname ip role; do
        machines+=("$hostname")
        echo "    ${#machines[@]}) $hostname $([ "$role" = "self" ] && echo "(this machine)" || echo "")"
    done < <(python3 -c "
import json, subprocess, sys
r = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True)
if r.returncode != 0: sys.exit(1)
data = json.loads(r.stdout)
self_node = data.get('Self', {})
if self_node:
    name = self_node.get('DNSName', '').rstrip('.')
    if name: print(f'{name} _ self')
for peer in data.get('Peer', {}).values():
    if not peer.get('Online', False): continue
    name = peer.get('DNSName', '').rstrip('.')
    if name: print(f'{name} _ peer')
" 2>/dev/null)

    if [ ${#machines[@]} -eq 0 ]; then
        red "    No Tailscale peers found. Is Tailscale running?"
        exit 1
    fi

    echo ""
    read -rp "    Which machine runs the bot? [1-${#machines[@]}]: " choice
    bot_host="${machines[$((choice - 1))]}"

    read -rp "    SSH user for bot host [$(whoami)]: " ssh_user
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

# --- Step 2: Upgrade ccbot (local + bot host first, so --headless flag exists) ---
bold "==> Upgrading ccbot"
printf "    %-12s " "local"
if uv tool upgrade ccbot >/dev/null 2>&1; then green "✓"; else red "✗"; fi
printf "    %-12s " "bot host"
if ssh_cmd "${SSH_USER}@${BOT_HOST}" "bash -lc 'uv tool upgrade ccbot'" >/dev/null 2>&1; then green "✓"; else red "✗"; fi

# --- Step 3: Run ccbot setup --headless on bot host (upgrades remaining machines) ---
bold "==> Running ccbot setup --headless on bot host"
ssh_cmd "${SSH_USER}@${BOT_HOST}" "bash -lic 'ccbot setup --headless'" 2>&1 | sed 's/^/    /'

# --- Step 4: Restart bot ---
bold "==> Restarting bot on ${BOT_HOST}"
ssh_cmd "${SSH_USER}@${BOT_HOST}" \
    "tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} C-c 2>/dev/null; sleep 2; tmux send-keys -t ${TMUX_SESSION}:${TMUX_WINDOW} 'ccbot' Enter"
sleep 3
green "    Bot restarted."
