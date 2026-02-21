"""Machine abstraction protocol and LocalMachine implementation for multi-machine support.

Defines the MachineConnection Protocol that all machine backends must satisfy,
and provides LocalMachine which delegates filesystem and tmux operations to
the existing local singletons (tmux_manager, config), and RemoteMachine which
uses asyncssh to operate on remote machines over SSH.

Key components:
  - MachineConnection: Protocol defining the async interface for all machines.
  - LocalMachine: Implementation for the local machine using tmux_manager.
  - RemoteMachine: Implementation for remote machines via asyncssh.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from .config import config
from .tmux_manager import TmuxWindow, tmux_manager

logger = logging.getLogger(__name__)


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


class RemoteMachine:
    """MachineConnection implementation for remote machines via asyncssh."""

    def __init__(self, machine_id: str, host: str, user: str) -> None:
        self.machine_id = machine_id
        self._host = host
        self._user = user
        self._conn: object | None = None
        self._tmux_session_name: str = "ccbot"

    async def _get_conn(self) -> object:
        """Return a persistent asyncssh connection, creating it if necessary."""
        if self._conn is None:
            import asyncssh

            self._conn = await asyncssh.connect(
                self._host,
                username=self._user,
                known_hosts=None,
            )
        return self._conn

    async def _run(self, cmd: str, binary: bool = False) -> object:
        """Run a command over SSH, reconnecting on connection errors."""
        import asyncssh

        conn = await self._get_conn()
        try:
            return await conn.run(cmd, encoding=None if binary else "utf-8")  # type: ignore[union-attr]
        except (asyncssh.DisconnectError, asyncssh.ConnectionLost, OSError):
            logger.warning("SSH connection lost to %s, reconnecting", self._host)
            self._conn = None
            conn = await self._get_conn()
            return await conn.run(cmd, encoding=None if binary else "utf-8")  # type: ignore[union-attr]

    async def list_dir(self, path: str) -> list[str]:
        """Return sorted non-hidden subdirectory names; [] on error."""
        cmd = (
            f"find {path!r} -maxdepth 1 -mindepth 1 -type d -not -name '.*'"
            f" -printf '%f\\n' 2>/dev/null | sort"
        )
        try:
            result = await self._run(cmd)
            stdout: str = result.stdout  # type: ignore[union-attr]
            return [line for line in stdout.splitlines() if line]
        except Exception:
            logger.exception("list_dir failed for %s on %s", path, self._host)
            return []

    async def read_file_from_offset(self, path: str, offset: int) -> bytes:
        """Return bytes from offset to EOF; b'' on error."""
        cmd = f"tail -c +{offset + 1} {path!r} 2>/dev/null"
        try:
            result = await self._run(cmd, binary=True)
            data: bytes = result.stdout  # type: ignore[union-attr]
            return data
        except Exception:
            logger.exception(
                "read_file_from_offset failed for %s on %s", path, self._host
            )
            return b""

    async def file_size(self, path: str) -> int | None:
        """Return file size in bytes, or None if missing."""
        cmd = f"stat -c %s {path!r} 2>/dev/null"
        try:
            result = await self._run(cmd)
            stdout: str = result.stdout  # type: ignore[union-attr]
            text = stdout.strip()
            if not text:
                return None
            return int(text)
        except Exception:
            logger.exception("file_size failed for %s on %s", path, self._host)
            return None

    async def list_windows(self) -> list[TmuxWindow]:
        """List all tmux windows on this machine, excluding __main__."""
        fmt = "#{window_id}:#{window_name}:#{pane_current_path}:#{pane_current_command}"
        cmd = f"tmux list-windows -t {self._tmux_session_name} -F '{fmt}' 2>/dev/null"
        try:
            result = await self._run(cmd)
            stdout: str = result.stdout  # type: ignore[union-attr]
            windows: list[TmuxWindow] = []
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":", 3)
                if len(parts) != 4:
                    continue
                window_id, window_name, cwd, pane_cmd = parts
                if window_name == "__main__":
                    continue
                windows.append(
                    TmuxWindow(
                        window_id=window_id,
                        window_name=window_name,
                        cwd=cwd,
                        pane_current_command=pane_cmd,
                    )
                )
            return windows
        except Exception:
            logger.exception("list_windows failed on %s", self._host)
            return []

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a tmux window by its ID (e.g. '@0')."""
        windows = await self.list_windows()
        for window in windows:
            if window.window_id == window_id:
                return window
        return None

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        """Send keys to a tmux window; return True on success."""
        target = f"{self._tmux_session_name}:{window_id}"
        escaped = text.replace("'", "'\\''")
        literal_flag = " -l" if literal else ""
        cmd = f"tmux send-keys -t {target!r}{literal_flag} '{escaped}'"
        try:
            await self._run(cmd)
            if enter:
                await asyncio.sleep(0.5)
                await self._run(f"tmux send-keys -t {target!r} Enter")
            return True
        except Exception:
            logger.exception(
                "send_keys failed for window %s on %s", window_id, self._host
            )
            return False

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture visible content of a tmux pane; None on failure."""
        target = f"{self._tmux_session_name}:{window_id}"
        ansi_flag = " -e" if with_ansi else ""
        cmd = f"tmux capture-pane -p{ansi_flag} -t {target!r}"
        try:
            result = await self._run(cmd)
            stdout: str = result.stdout  # type: ignore[union-attr]
            return stdout
        except Exception:
            logger.exception(
                "capture_pane failed for window %s on %s", window_id, self._host
            )
            return None

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
        claude_cmd = config.claude_command
        if dangerous:
            claude_cmd = f"{claude_cmd} --dangerously-skip-permissions"
        wname = window_name or Path(work_dir).name or "claude"
        cmd = (
            f"tmux new-window -t {self._tmux_session_name}"
            f" -c {work_dir!r} -n {wname!r}"
            f" -P -F '#{{window_id}}' {claude_cmd!r}"
        )
        try:
            result = await self._run(cmd)
            stdout: str = result.stdout  # type: ignore[union-attr]
            window_id = stdout.strip()
            return (True, "Window created", wname, window_id)
        except Exception as e:
            logger.exception("create_window failed on %s", self._host)
            return (False, str(e), wname, "")

    async def kill_window(self, window_id: str) -> bool:
        """Kill a tmux window by ID; return True on success."""
        target = f"{self._tmux_session_name}:{window_id}"
        cmd = f"tmux kill-window -t {target!r} 2>/dev/null"
        try:
            await self._run(cmd)
            return True
        except Exception:
            logger.exception(
                "kill_window failed for window %s on %s", window_id, self._host
            )
            return False
