# Multi-Machine Tailscale Design

Control Claude Code sessions on any machine in your Tailscale network from a single Telegram bot.

## Context

ccbot currently runs on one machine and talks to its local tmux + local Claude Code sessions. This design extends it to support a fleet of machines (MacBook, Fedora, Asus, RPi5) connected via Tailscale, with the bot running on a single central host.

**Chosen approach:** Centralized bot (MacBook now, RPi5 later) + asyncssh persistent connections to remote machines + HTTP hook endpoint for remote SessionStart reporting.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TAILSCALE NETWORK                               │
│                                                                         │
│  ┌─────────────────────────┐                                            │
│  │   Bot host (MacBook)    │                                            │
│  │                         │                                            │
│  │  ┌───────────────────┐  │                                            │
│  │  │     ccbot         │◄─┼──── Telegram messages (via internet)       │
│  │  │                   │  │                                            │
│  │  │  asyncssh pool    │  │                                            │
│  │  │  (1 conn/machine) │  │                                            │
│  │  │                   │  │                                            │
│  │  │  HTTP :8080/hook  │◄─┼──── remote SessionStart hooks POST here   │
│  │  └───────────────────┘  │                                            │
│  └────────────┬────────────┘                                            │
│               │  asyncssh over Tailscale                                │
│    ┌──────────┴──────────────────────────────┐                          │
│    ▼                                         ▼                          │
│  ┌──────────────────────┐     ┌──────────────────────┐                  │
│  │       Fedora         │     │        Asus          │                  │
│  │  tmux + claude       │     │  tmux + claude       │                  │
│  │  ~/.claude/projects/ │     │  ~/.claude/projects/ │                  │
│  │  SessionStart hook   │     │  SessionStart hook   │                  │
│  │  └── POST /hook ─────┼─────┼──────────────────────┼──► bot :8080    │
│  └──────────────────────┘     └──────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Message flows

**Outbound (user → Claude):**
```
User sends message in [Fedora] my-project topic
  → thread_bindings[user][thread] → {machine: "fedora", window_id: "@3"}
  → asyncssh conn to fedora → tmux send-keys "@3" "message"
```

**Inbound (Claude → user):**
```
Monitor polls every 2s:
  asyncssh conn to fedora → tail -c +{offset} ~/.claude/.../uuid.jsonl
  parse new lines → format → send to Telegram topic
```

**New session hook:**
```
Claude starts on Fedora → SessionStart fires
  ccbot hook --remote http://bothost.tail.ts.net:8080/hook
  POST → bot resolves window_id via asyncssh tmux list-windows
  bot writes to local session_map.json
```

---

## Section 1 — New modules and changes

```
src/ccbot/
  machines.py          NEW — Machine registry + MachineConnection protocol
                             LocalMachine  — wraps existing libtmux/local fs
                             RemoteMachine — wraps asyncssh connection
                             Shared interface:
                               run_tmux_cmd(window_id, keys)
                               read_file_from_offset(path, offset) → bytes
                               list_dir(path) → list[str]
                               create_window(cwd, dangerous=False) → window_id
                               kill_window(window_id)

  hook_server.py       NEW — aiohttp HTTP server, Tailscale-bound
                             POST /hook  ← remote SessionStart hooks POST here
                             GET  /health ← reachability check during setup

  config.py            MOD — load machines from machines.json

  tmux_manager.py      MOD — becomes LocalMachine impl; unchanged for MacBook

  session_monitor.py   MOD — reads via machine.read_file_from_offset()
                             works identically for local and remote

  session.py           MOD — thread_bindings gain {machine, window_id} per entry
                             state.json gains "machine" and "dangerous" per binding

  handlers/
    directory_browser.py  MOD — step 0: machine picker (from machines.json)
                                step 1: list_dir() on selected machine
                                step 2: permissions mode picker
                                step 3: confirm → create_window()
```

---

## Section 2 — Machine config (`~/.ccbot/machines.json`)

```json
{
  "hook_port": 8080,
  "machines": {
    "macbook": {
      "display": "MacBook",
      "type": "local"
    },
    "fedora": {
      "display": "Fedora",
      "host": "fedora.tail12345.ts.net",
      "user": "myuser"
    },
    "asus": {
      "display": "Asus",
      "host": "asus.tail12345.ts.net",
      "user": "myuser"
    }
  }
}
```

- `type: local` — uses libtmux directly, zero SSH overhead
- Remote entries — asyncssh connects on first use, auto-reconnects on drop
- `host` is the Tailscale MagicDNS hostname (or IP)
- SSH auth uses the system key agent (no passwords, no extra config)
- `machines.json` is written and maintained by `ccbot setup`

---

## Section 3 — State changes

`state.json` thread_bindings gain `machine` and `dangerous` per window:

```json
{
  "thread_bindings": {
    "123456789": {
      "42": {"window_id": "@3", "machine": "fedora", "dangerous": false},
      "87": {"window_id": "@0", "machine": "macbook", "dangerous": true}
    }
  },
  "window_display_names": {
    "fedora:@3": "my-project",
    "macbook:@0": "ccbot"
  }
}
```

- Window IDs namespaced by machine (`fedora:@3`) — `@3` on Fedora ≠ `@3` on MacBook
- `session_map.json` stays local to the bot, populated by the hook server on receipt of POST
- `monitor_state.json` byte offsets keyed by full JSONL path (includes session UUID) — no changes needed

---

## Section 4 — UX flow

### Creating a session in a new topic

```
User sends first message in empty topic
  ↓
Machine picker (inline keyboard, from machines.json):
  [ MacBook ]  [ Fedora ]  [ Asus ]
  ↓ user taps Fedora
Directory browser (list_dir via asyncssh):
  📁 projects/
  📁 work/
  📁 ~/
  ↓ user navigates to projects/foo
Permissions mode picker:
  [ Normal ]  [ Skip permissions ⚡ ]
  ↓ user picks Skip permissions
Bot creates window on Fedora:
  tmux new-window -c /home/user/projects/foo "claude --dangerously-skip-permissions"
Topic renamed: "[Fedora] foo ⚡"
Thread bound: {machine: "fedora", window_id: "@3", dangerous: true}
Pending message forwarded to new window
```

- `⚡` suffix on topic name signals unrestricted mode at a glance
- All subsequent commands (`/screenshot`, `/esc`, `/history`) route through the same `MachineConnection` — no special casing

### Routing existing sessions

```
User sends message in [Fedora] foo ⚡ topic
  → lookup: machine=fedora, window=@3
  → asyncssh conn to fedora → tmux send-keys "@3" "message"
```

---

## Section 5 — Hook changes on remote machines

Remote hook POSTs to bot instead of writing a local file:

```json
// ~/.claude/settings.json on Fedora / Asus
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "ccbot hook --remote http://macbook.tail12345.ts.net:8080/hook",
        "timeout": 5
      }]
    }]
  }
}
```

- `ccbot hook --remote <url>` — POSTs session info instead of writing local file
- `ccbot hook --install --remote <url>` — writes the above settings.json on the remote
- `ccbot hook --uninstall` — removes hook from settings.json (used by `ccbot setup` when a machine is deselected)
- MacBook (local) — hook stays as-is, writes local file directly

---

## Section 6 — Setup & automation (`ccbot setup`)

Fully idempotent. Run anytime: first setup, adding a machine, migrating to RPi5. Reads Tailscale to discover peers; writes/updates `machines.json`.

GitHub repo URL auto-detected via `git remote get-url origin` — remotes always install the same version via uv.

### TUI flow

```
ccbot setup

  Querying Tailscale peers...   (tailscale status --json)
  Detecting local machine...

  ┌──────────────────────────────────────────────────────────────┐
  │  CCBOT MACHINE SETUP                                         │
  │                                                              │
  │  [x] macbook.tail12345.ts.net   (this machine, always on)   │
  │  [x] fedora.tail12345.ts.net    ← already in machines.json  │
  │  [ ] asus.tail12345.ts.net                                   │
  │  [ ] raspberrypi.tail12345.ts.net                            │
  │                                                              │
  │  Space = toggle   Enter = confirm   q = quit                 │
  └──────────────────────────────────────────────────────────────┘

  For each newly selected remote → prompt SSH user + display name
  ┌──────────────────────────────────────────────────────────────┐
  │  fedora.tail12345.ts.net                                     │
  │  SSH user:     myuser_                                       │
  │  Display name: Fedora_                                       │
  └──────────────────────────────────────────────────────────────┘
```

### Per-machine steps

```
machines.json  ← written/updated with current selection

[fedora] Checking SSH connectivity...                          ✓
[fedora] Installing ccbot via uv...
         ssh fedora "uv tool install git+https://github.com/user/ccbot"  ✓
[fedora] Installing SessionStart hook...
         ssh fedora "ccbot hook --install \
           --remote http://macbook.tail12345.ts.net:8080/hook"           ✓
[fedora] Verifying hook endpoint reachable from remote...                ✓

[macbook] Installing SessionStart hook (local)...
          ccbot hook --install                                            ✓

Summary:
  ✓ macbook — local, hook installed
  ✓ fedora  — ssh ok, ccbot installed, hook installed
  ✗ asus    — SSH failed: permission denied
    → fix:  ssh-copy-id myuser@asus.tail12345.ts.net
    → then: ccbot setup --machine asus
```

### Key behaviors

- **uv only** — `uv tool install git+<github-url>` on all remotes; URL auto-detected from local git remote
- **Tailscale-first discovery** — machine list comes from `tailscale status --json`, not manual config
- **Idempotent** — uv skips reinstall if version unchanged; hook install is idempotent; safe to re-run anytime
- **Deselection** — machines unchecked in TUI are removed from `machines.json` and their hook uninstalled via SSH
- **Single machine** — `ccbot setup --machine raspberrypi` skips TUI, targets one machine (useful for RPi5 next week)
- **Partial failures** — all machines attempted; failures summarized at end, never abort mid-run
- **SSH prerequisite** — if SSH auth fails, clear error with exact `ssh-copy-id` command to run
