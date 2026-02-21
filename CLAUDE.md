# CLAUDE.md

ccbot — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows. Each topic is bound to one tmux window running one Claude Code instance. Supports multi-machine fleets via Tailscale.

Tech stack: Python, python-telegram-bot, tmux, uv, asyncssh, aiohttp.

## Common Commands

```bash
uv run ruff check src/ tests/         # Lint — MUST pass before committing
uv run ruff format src/ tests/        # Format — auto-fix, then verify with --check
uv run pyright src/ccbot/             # Type check — MUST be 0 errors before committing
./scripts/restart.sh                  # Restart the ccbot service after code changes
ccbot hook --install                  # Auto-install Claude Code SessionStart hook
ccbot setup                           # Fleet setup: Tailscale peers, hooks, ccbot upgrade
```

## Deployment

- **GitHub repo**: `feltroidprime/ccbot` (hardcoded in setup_cmd.py)
- **Install**: `uv tool install 'ccbot @ git+https://github.com/feltroidprime/ccbot'`
- **Upgrade**: `uv tool upgrade ccbot` (pulls latest main)
- **Fleet upgrade**: `ccbot setup` upgrades ccbot on all configured machines
- **Push to fork remote**: `git push fork main` (origin points to upstream six-ddc/ccbot)

## Core Design Constraints

- **1 Topic = 1 Window = 1 Session** — all internal routing keyed by tmux window ID (`@0`, `@12`), not window name. Window names kept as display names. Same directory can have multiple windows.
- **Topic-only** — no backward-compat for non-topic mode. No `active_sessions`, no `/list`, no General topic routing.
- **Multi-machine** — `MachineConnection` protocol with `LocalMachine` and `RemoteMachine` (asyncssh) implementations. `MachineRegistry` loads fleet config from `machines.json`.
- **No message truncation** at parse layer — splitting only at send layer (`split_message`, 4096 char limit).
- **MarkdownV2 only** — use `safe_reply`/`safe_edit`/`safe_send` helpers (auto fallback to plain text). Internal queue/UI code calls bot API directly with its own fallback.
- **Hook-based session tracking** — `SessionStart` hook writes `session_map.json` (local) or POSTs to bot's hook server (remote). Monitor polls it to detect session changes.
- **Message queue per user** — FIFO ordering, message merging (3800 char limit), tool_use/tool_result pairing.
- **Rate limiting** — `AIORateLimiter(max_retries=5)` on the Application (30/s global). On restart, the global bucket is pre-filled to avoid burst against Telegram's server-side counter.

## Code Conventions

- Every `.py` file starts with a module-level docstring: purpose clear within 10 lines, one-sentence summary first line, then core responsibilities and key components.
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes; use `answer_callback_query` for instant feedback.

## Configuration

- Config directory: `~/.ccbot/` by default, override with `CCBOT_DIR` env var.
- `.env` loading priority: local `.env` > config dir `.env`.
- State files: `state.json` (thread bindings), `session_map.json` (hook-generated), `monitor_state.json` (byte offsets).
- `machines.json` — fleet config: machine IDs, types (local/remote), SSH hosts/users, display names, hook port.

## Multi-Machine Architecture

```
Bot host (ASUS)                        Remote machines (Mac, etc.)
┌──────────────┐                       ┌──────────────────────┐
│ ccbot (bot)  │◄──── Tailscale SSH ──►│ Claude Code sessions │
│ hook server  │◄──── POST /hook ──────│ SessionStart hook    │
│ :8080        │                       │ (ccbot hook --remote)│
└──────────────┘                       └──────────────────────┘
```

- Bot runs on one machine (the "local" machine in machines.json).
- Remote machines run Claude Code sessions; their SessionStart hooks POST to the bot's hook server.
- `ccbot setup` discovers Tailscale peers, configures machines.json, upgrades ccbot, and installs hooks on all machines. Idempotent — safe to re-run.
- Remote file reads (JSONL transcripts) and tmux operations go through `RemoteMachine` via asyncssh.

## Hook Configuration

Auto-install: `ccbot hook --install`

Remote hook (installed by `ccbot setup` on remote machines):
```
ccbot hook --install --remote http://<bot-host>:8080/hook --machine-id <id>
```

## Architecture Details

See @.claude/rules/architecture.md for full system diagram and module inventory.
See @.claude/rules/topic-architecture.md for topic→window→session mapping details.
See @.claude/rules/message-handling.md for message queue, merging, and rate limiting.
