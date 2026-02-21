"""Interactive fleet setup — discovers Tailscale peers, configures machines.json, and provisions each machine.

Idempotent: every step checks current state before acting, so re-running is always safe.

Responsibilities:
  - Query Tailscale for available peers via `tailscale status --json`.
  - Present a checkbox TUI (prompt_toolkit) for machine selection.
  - Collect SSH user and display name for each remote machine.
  - Write machines.json to the ccbot config directory.
  - Provision each machine: SSH check, ccbot install (skip if present), hook install
    (skip if present), endpoint reachability verification.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from prompt_toolkit import prompt
from prompt_toolkit.shortcuts import checkboxlist_dialog

from .utils import ccbot_dir, atomic_write_json

logger = logging.getLogger(__name__)
HOOK_DEFAULT_PORT = 8080
GITHUB_REPO = "https://github.com/feltroidprime/ccbot"


@dataclass
class MachineSetupResult:
    machine_id: str
    success: bool
    errors: list[str] = field(default_factory=list)


def _tailscale_status() -> dict | None:
    """Run tailscale status --json and return parsed dict, or None on failure."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception as e:
        logger.warning("Could not query Tailscale: %s", e)
        return None


def _get_tailscale_peers() -> list[dict]:  # type: ignore[type-arg]
    """Return list of peer dicts (includes self with _is_self=True)."""
    data = _tailscale_status()
    if not data:
        return []
    peers = list(data.get("Peer", {}).values())
    self_node = data.get("Self", {})
    if self_node:
        self_node["_is_self"] = True
        peers.insert(0, self_node)
    return peers


def _get_tailscale_self_hostname() -> str:
    """Return the local machine's Tailscale MagicDNS hostname (without trailing dot)."""
    data = _tailscale_status()
    if data:
        return data.get("Self", {}).get("DNSName", "").rstrip(".")
    return socket.gethostname()


def _load_existing_machines(machines_file: Path) -> dict:  # type: ignore[type-arg]
    if machines_file.exists():
        try:
            return json.loads(machines_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"hook_port": HOOK_DEFAULT_PORT, "machines": {}}


def _ssh_check(user: str, host: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "BatchMode=yes",
                f"{user}@{host}",
                "echo ok",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def _uv_upgrade_remote(user: str, host: str) -> bool:
    """Upgrade (or install) ccbot on a remote machine. Always pulls latest main."""
    cmd = f"uv tool upgrade ccbot 2>&1 || uv tool install 'ccbot @ git+{GITHUB_REPO}' 2>&1"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd], capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_hook_remote(
    user: str, host: str, remote_url: str, machine_id: str
) -> bool:
    cmd = f"ccbot hook --install --remote {remote_url} --machine-id {machine_id}"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd], capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_endpoint_reachable(user: str, host: str, health_url: str) -> bool:
    cmd = f"curl -sf {health_url}"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_hook_local() -> bool:
    try:
        result = subprocess.run(
            ["ccbot", "hook", "--install"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def setup_main(target_machine: str | None = None) -> None:
    """Run the ccbot setup TUI.

    Args:
        target_machine: If set, skip TUI and target this single machine hostname.
    """
    config_dir = ccbot_dir()
    machines_file = config_dir / "machines.json"
    existing = _load_existing_machines(machines_file)
    existing_machines = existing.get("machines", {})

    local_hostname = _get_tailscale_self_hostname()
    hook_port = existing.get("hook_port", HOOK_DEFAULT_PORT)

    # --- Machine selection ---
    if target_machine:
        selected_hostnames = [target_machine]
    else:
        peers = _get_tailscale_peers()
        if not peers:
            print("No Tailscale peers found. Is Tailscale running?")
            print("Falling back to local-only setup.")
            selected_hostnames = [local_hostname] if local_hostname else ["localhost"]
        else:
            # Build checkbox list for TUI
            choices = []
            defaults = []
            for peer in peers:
                hostname = peer.get("DNSName", "").rstrip(".")
                if not hostname:
                    continue
                is_self = peer.get("_is_self", False)
                label = f"{hostname}{'  (this machine)' if is_self else ''}"
                choices.append((hostname, label))
                # Pre-select: local machine always, plus machines already in config
                machine_id_hint = hostname.split(".")[0]
                if is_self or machine_id_hint in existing_machines:
                    defaults.append(hostname)

            if not choices:
                print("No valid Tailscale peers found.")
                sys.exit(1)

            try:
                result = checkboxlist_dialog(
                    title="CCBOT MACHINE SETUP",
                    text="Select machines to manage (Space=toggle, Enter=confirm, q=quit):",
                    values=choices,
                    default_values=defaults,
                ).run()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                sys.exit(0)

            if result is None:
                print("Cancelled.")
                sys.exit(0)
            selected_hostnames = result

    # --- Collect SSH user + display name for each remote machine ---
    machines_config: dict[str, dict[str, str]] = {}
    for hostname in selected_hostnames:
        machine_id = hostname.split(".")[0]
        is_local = hostname == local_hostname or hostname == "localhost"

        if is_local:
            existing_cfg = existing_machines.get(machine_id, {})
            display = existing_cfg.get("display", machine_id.capitalize())
            machines_config[machine_id] = {"display": display, "type": "local"}
            continue

        existing_cfg = existing_machines.get(machine_id, {})
        try:
            ssh_user = prompt(
                f"\n{hostname}\n  SSH user: ",
                default=existing_cfg.get("user", ""),
            ).strip()
            display_name = prompt(
                "  Display name: ",
                default=existing_cfg.get("display", machine_id.capitalize()),
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            sys.exit(0)

        machines_config[machine_id] = {
            "display": display_name,
            "host": hostname,
            "user": ssh_user,
        }

    # Write machines.json
    new_config: dict[str, object] = {
        "hook_port": hook_port,
        "machines": machines_config,
    }
    config_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(machines_file, new_config)
    print(f"\nWrote {machines_file}\n")

    # --- Per-machine provisioning (idempotent: check before act) ---
    hook_url_base = f"http://{local_hostname}:{hook_port}" if local_hostname else ""
    results: list[MachineSetupResult] = []

    for machine_id, cfg in machines_config.items():
        print(f"\n[{machine_id}]")

        if cfg.get("type") == "local":
            r = MachineSetupResult(machine_id=machine_id, success=True)
            # Upgrade local ccbot
            print("  ccbot ............ ", end="", flush=True)
            try:
                res = subprocess.run(
                    ["uv", "tool", "upgrade", "ccbot"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                print("✓ (upgraded)" if res.returncode == 0 else "✗")
                if res.returncode != 0:
                    r.errors.append("Local upgrade failed — run: uv tool upgrade ccbot")
            except Exception:
                print("✗")
                r.errors.append("Local upgrade failed — run: uv tool upgrade ccbot")
            # Hook
            print("  hook ............. ", end="", flush=True)
            ok = _install_hook_local()
            if not ok:
                r.errors.append("Hook install failed — run: ccbot hook --install")
            print("✓" if ok else "✗")
            r.success = not r.errors
            results.append(r)
            continue

        host = cfg["host"]
        user = cfg["user"]
        r = MachineSetupResult(machine_id=machine_id, success=True)

        # SSH
        print("  ssh .............. ", end="", flush=True)
        if not _ssh_check(user, host):
            r.success = False
            r.errors.append(f"SSH failed — run: ssh-copy-id {user}@{host}")
            print("✗")
            results.append(r)
            continue
        print("✓")

        # ccbot: always upgrade to latest main
        print("  ccbot ............ ", end="", flush=True)
        if _uv_upgrade_remote(user, host):
            print("✓ (upgraded)")
        else:
            r.success = False
            r.errors.append("Upgrade failed — run on remote: uv tool upgrade ccbot")
            print("✗")

        # Hook
        if hook_url_base:
            hook_url = f"{hook_url_base}/hook"
            print("  hook ............. ", end="", flush=True)
            if _install_hook_remote(user, host, hook_url, machine_id):
                print("✓")
            else:
                r.success = False
                r.errors.append("Hook install failed on remote")
                print("✗")

            # Endpoint
            health_url = f"{hook_url_base}/health"
            print("  endpoint ......... ", end="", flush=True)
            if _check_endpoint_reachable(user, host, health_url):
                print("✓")
            else:
                r.errors.append(f"Endpoint {health_url} not reachable from {host}")
                print("✗ (warning)")

        results.append(r)

    # Summary
    print("\n--- Summary ---")
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"  {status} {r.machine_id}")
        for err in r.errors:
            print(f"    → {err}")

    all_ok = all(r.success for r in results)
    if all_ok:
        print("\nAll machines configured successfully!")
    else:
        print(
            "\nSome machines failed. Re-run with --machine <hostname> to retry individual machines."
        )
    sys.exit(0 if all_ok else 1)
