"""Machine abstraction protocol and LocalMachine implementation for multi-machine support.

Defines the MachineConnection Protocol that all machine backends must satisfy,
and provides LocalMachine which delegates filesystem and tmux operations to
the existing local singletons (tmux_manager, config).

Key components:
  - MachineConnection: Protocol defining the async interface for all machines.
  - LocalMachine: Implementation for the local machine using tmux_manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .config import config
from .tmux_manager import TmuxWindow, tmux_manager


@runtime_checkable
class MachineConnection(Protocol):
    """Protocol for machine backends — local or remote via asyncssh."""

    machine_id: str

    async def list_dir(self, path: str) -> list[str]:
        """Return sorted non-hidden subdirectory names for path, or [] on error."""
        ...

    async def read_file_from_offset(self, path: str, offset: int) -> bytes:
        """Return bytes from offset to EOF; b'' if offset >= size or on error."""
        ...

    async def file_size(self, path: str) -> int | None:
        """Return file size in bytes, or None if missing."""
        ...

    async def list_windows(self) -> list[TmuxWindow]:
        """List all tmux windows on this machine."""
        ...

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a tmux window by its ID (e.g. '@0')."""
        ...

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        """Send keys to a tmux window; return True on success."""
        ...

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture visible content of a tmux pane; None on failure."""
        ...

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        dangerous: bool = False,
    ) -> tuple[bool, str, str, str]:
        """Create a new tmux window and start Claude Code.

        Returns:
            Tuple of (success, message, window_name, window_id).
        """
        ...

    async def kill_window(self, window_id: str) -> bool:
        """Kill a tmux window by ID; return True on success."""
        ...


class LocalMachine:
    """MachineConnection implementation for the local machine."""

    def __init__(self, machine_id: str = "local") -> None:
        self.machine_id = machine_id

    async def list_dir(self, path: str) -> list[str]:
        """Return sorted non-hidden subdirectory names; [] on error."""
        try:
            p = Path(path)
            return sorted(
                entry.name
                for entry in p.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            )
        except Exception:
            return []

    async def read_file_from_offset(self, path: str, offset: int) -> bytes:
        """Return bytes from offset to EOF; b'' if offset >= size or on OSError."""
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                return f.read()
        except OSError:
            return b""

    async def file_size(self, path: str) -> int | None:
        """Return file size in bytes, or None if file is missing."""
        try:
            return Path(path).stat().st_size
        except OSError:
            return None

    async def list_windows(self) -> list[TmuxWindow]:
        """List all tmux windows via the local tmux_manager."""
        return await tmux_manager.list_windows()

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a tmux window by ID via the local tmux_manager."""
        return await tmux_manager.find_window_by_id(window_id)

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        """Send keys to a tmux window via the local tmux_manager."""
        return await tmux_manager.send_keys(
            window_id, text, enter=enter, literal=literal
        )

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture a tmux pane via the local tmux_manager."""
        return await tmux_manager.capture_pane(window_id, with_ansi=with_ansi)

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        dangerous: bool = False,
    ) -> tuple[bool, str, str, str]:
        """Create a tmux window and start Claude Code with optional --dangerously-skip-permissions."""
        cmd = config.claude_command
        if dangerous:
            cmd = f"{cmd} --dangerously-skip-permissions"
        return await tmux_manager.create_window(
            work_dir,
            window_name=window_name,
            start_claude=True,
            claude_command=cmd,
        )

    async def kill_window(self, window_id: str) -> bool:
        """Kill a tmux window via the local tmux_manager."""
        return await tmux_manager.kill_window(window_id)
