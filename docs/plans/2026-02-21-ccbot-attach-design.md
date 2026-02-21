# ccbot attach — Design Doc

**Date**: 2026-02-21
**Status**: Approved

## Problem

When starting a Claude Code session from Telegram (iPhone), there's no ergonomic way to continue that session from a PC. `tmux attach -t ccbot` opens all windows at once. `claude --resume` breaks the Telegram bridge. Users need a unified picker that works from any machine in the Tailscale network.

## Solution

`ccbot attach` — a CLI command that lists all active Claude Code sessions across all machines and attaches to the selected one via tmux.

## Architecture

```
    Any machine in Tailscale           Orchestrator (e.g. Raspberry Pi)
    ┌──────────────────┐               ┌──────────────────────────┐
    │ ccbot attach     │─GET /sessions─▶│ Bot + HTTP server        │
    │                  │◀── JSON ──────│                          │
    │  Picker TUI      │               │ For each machine:        │
    │  (grouped by     │               │   local: tmux_manager    │
    │   machine)       │               │   remote: SSH tmux list  │
    │                  │               └──────────────────────────┘
    │  User selects    │
    │                  │
    │  Local window?   │──tmux attach -t ccbot:@N──────────────────▶ local tmux
    │  Remote window?  │──ssh -t user@host tmux attach -t ccbot:@N─▶ remote tmux
    └──────────────────┘
```

## Components

### 1. `ccbot setup` — Orchestrator selection (modification)

Added to the existing setup flow, before machine selection:

**First run:**
- After Tailscale peer discovery, ask "Which machine is the orchestrator?"
- Default: this machine (`_is_self`)
- Verify port 8080 is reachable (or will be once bot starts)
- Store `"orchestrator": "<hostname>"` in `machines.json`

**Re-run:**
- Show current orchestrator with checkmark
- Ask "Change orchestrator? [y/N]" (default: no)
- Existing machines pre-selected in checkbox TUI (current behavior)

**Provisioning addition:**
- For each remote machine, write `~/.ccbot/orchestrator.json` via SSH:
  ```json
  {"host": "raspberrypi.tailnet", "port": 8080}
  ```

**`--machine` flag:** Uses existing orchestrator from machines.json, no re-prompting.

### 2. `GET /sessions` endpoint (new)

Added to the existing HTTP server on the orchestrator (hook port 8080).

**Response:**
```json
{
  "sessions": [
    {
      "machine_id": "macbook",
      "machine_display": "MacBook",
      "window_id": "@5",
      "window_name": "ccbot",
      "cwd": "~/PycharmProjects/ccbot",
      "ssh_host": "macbook.tailnet",
      "ssh_user": "felt"
    }
  ]
}
```

**Implementation:** Iterates all machines from `machine_registry`:
- Local: `tmux_manager.list_windows()`
- Remote: SSH `tmux list-windows` (with short timeout)
- Enriches with display names from `state.json`

### 3. `ccbot attach` command (new)

**Entry point:** Early-exit in `main.py` (same pattern as `hook`, `setup`). No config.py import, no token required.

**Orchestrator resolution:**
1. Read `~/.ccbot/orchestrator.json` (written by setup on remotes)
2. If absent, read `~/.ccbot/machines.json` — if local machine is the orchestrator, use localhost
3. If neither → error: "Run `ccbot setup` from the orchestrator first"

**Flow:**
1. `GET http://<orchestrator>:<port>/sessions`
2. Display interactive picker (simple-term-menu), grouped by machine:
   ```
   ┌ MacBook (local) ────────────────────────┐
   │   1. ccbot           ~/PycharmProjects/ccbot │
   │ ❯ 2. api-server      ~/work/api             │
   ├ Homelab ────────────────────────────────┤
   │   3. ml-training     ~/projects/ml           │
   └─────────────────────────────────────────┘
   ```
3. Attach:
   - Local (ssh_host matches local Tailscale hostname): `os.execvp("tmux", ...)`
   - Remote: `os.execvp("ssh", ["ssh", "-t", "user@host", "tmux", "attach-session", "-t", "ccbot:@N"])`

**Edge cases:**
- Orchestrator unreachable → clear error with hint
- No sessions → "No active sessions"
- One session → attach directly, no picker
- simple-term-menu not installed → fallback to numbered input()

### 4. New file: `src/ccbot/attach_cmd.py`

Standalone module, ~100-120 lines. Dependencies: simple-term-menu (optional, with fallback).

## Files impacted

| File | Change |
|------|--------|
| `main.py` | Add `attach` subcommand early-exit |
| `attach_cmd.py` (new) | Orchestrator resolution, GET /sessions, picker, exec attach |
| `setup_cmd.py` | Add orchestrator selection step + write orchestrator.json on remotes |
| `bot.py` or new handler | Add `GET /sessions` endpoint to HTTP server |
| `machines.json` schema | Add `"orchestrator"` field |

## Out of scope (YAGNI)

- Creating sessions from `ccbot attach` (just attach to existing)
- Detach / session management
- Fallback SSH fan-out if orchestrator is down
- Filtering / search in picker
- Argument-based direct attach (e.g. `ccbot attach @5`)
