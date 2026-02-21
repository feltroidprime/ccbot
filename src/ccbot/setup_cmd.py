"""Interactive fleet setup — discovers Tailscale peers, configures machines.json, and provisions each machine.

Responsibilities:
  - Query Tailscale for available peers via `tailscale status --json`.
  - Present a checkbox TUI (prompt_toolkit) for machine selection.
  - Collect SSH user and display name for each remote machine.
  - Write machines.json to the ccbot config directory.
  - Provision each machine: SSH connectivity check, uv install, hook install,
    endpoint reachability verification.
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


@dataclass
class MachineSetupResult:
    machine_id: str
    success: bool
    errors: list[str] = field(default_factory=list)


def _get_tailscale_peers() -> list[dict]:  # type: ignore[type-arg]
    """Run tailscale status --json and return list of peer dicts (includes self with _is_self=True)."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        peers = list(data.get("Peer", {}).values())
        self_node = data.get("Self", {})
        if self_node:
            self_node = dict(self_node)
            self_node["_is_self"] = True
            peers.insert(0, self_node)
        return peers
    except Exception as e:
        logger.warning("Could not query Tailscale: %s", e)
        return []


def _get_tailscale_self_hostname() -> str:
    """Return the local machine's Tailscale MagicDNS hostname (without trailing dot)."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        return data.get("Self", {}).get("DNSName", "").rstrip(".")
    except Exception:
        return socket.gethostname()


def _detect_github_url() -> str:
    """Auto-detect GitHub repo URL from local git remote origin."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent.parent)  # repo root
        )
        return result.stdout.strip()
    except Exception:
        return ""


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
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             f"{user}@{host}", "echo ok"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def _uv_install_remote(user: str, host: str, github_url: str) -> bool:
    cmd = f"uv tool install 'ccbot @ git+{github_url}' --force 2>&1"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_hook_remote(user: str, host: str, remote_url: str, machine_id: str) -> bool:
    cmd = f"ccbot hook --install --remote {remote_url} --machine-id {machine_id}"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_endpoint_reachable(user: str, host: str, health_url: str) -> bool:
    cmd = f"curl -sf {health_url}"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_hook_local() -> bool:
    try:
        result = subprocess.run(
            ["ccbot", "hook", "--install"],
            capture_output=True, text=True, timeout=10
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

    # Detect GitHub URL for uv install
    github_url = _detect_github_url()
    if not github_url:
        print("Warning: could not detect GitHub URL from git remote origin")
        try:
            github_url = prompt("GitHub repo URL (e.g. https://github.com/user/ccbot): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            sys.exit(0)

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
        is_local = (hostname == local_hostname or hostname == "localhost")

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
    new_config: dict[str, object] = {"hook_port": hook_port, "machines": machines_config}
    config_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(machines_file, new_config)
    print(f"\nWrote {machines_file}\n")

    # --- Per-machine provisioning ---
    hook_url_base = f"http://{local_hostname}:{hook_port}" if local_hostname else ""
    results: list[MachineSetupResult] = []

    for machine_id, cfg in machines_config.items():
        if cfg.get("type") == "local":
            print(f"[{machine_id}] Installing local hook...", end=" ", flush=True)
            ok = _install_hook_local()
            r = MachineSetupResult(machine_id=machine_id, success=ok)
            if not ok:
                r.errors.append("Hook install failed — run: ccbot hook --install")
            print("✓" if ok else "✗")
            results.append(r)
            continue

        host = cfg["host"]
        user = cfg["user"]
        r = MachineSetupResult(machine_id=machine_id, success=True)

        print(f"[{machine_id}] Checking SSH connectivity...", end=" ", flush=True)
        if not _ssh_check(user, host):
            r.success = False
            r.errors.append(f"SSH failed — run: ssh-copy-id {user}@{host}")
            print("✗")
            results.append(r)
            continue
        print("✓")

        print(f"[{machine_id}] Installing ccbot via uv...", end=" ", flush=True)
        if not _uv_install_remote(user, host, github_url):
            r.success = False
            r.errors.append("uv tool install failed")
            print("✗")
        else:
            print("✓")

        if hook_url_base:
            hook_url = f"{hook_url_base}/hook"
            print(f"[{machine_id}] Installing hook (--remote {hook_url})...", end=" ", flush=True)
            if not _install_hook_remote(user, host, hook_url, machine_id):
                r.success = False
                r.errors.append("Hook install failed on remote")
                print("✗")
            else:
                print("✓")

            health_url = f"{hook_url_base}/health"
            print(f"[{machine_id}] Verifying endpoint reachable...", end=" ", flush=True)
            if not _check_endpoint_reachable(user, host, health_url):
                r.errors.append(f"Endpoint {health_url} not reachable from {host}")
                print("✗ (warning only)")
            else:
                print("✓")

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
        print("\nSome machines failed. Re-run with --machine <hostname> to retry individual machines.")
    sys.exit(0 if all_ok else 1)
