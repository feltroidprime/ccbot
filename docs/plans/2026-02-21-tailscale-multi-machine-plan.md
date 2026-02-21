# Multi-Machine Tailscale Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend ccbot to control Claude Code sessions on any machine in a Tailscale network from a single Telegram bot.

**Architecture:** Centralized bot host (MacBook/RPi5) connects to remote machines via persistent asyncssh connections. Remote SessionStart hooks POST to an HTTP endpoint on the bot instead of writing local files. All internal window state is namespaced by machine (`local:@0`, `fedora:@3`).

**Tech Stack:** asyncssh (remote tmux + file reads), aiohttp (hook HTTP server), textual or prompt_toolkit (setup TUI), existing libtmux (local machine)

---

## Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add asyncssh and aiohttp**

In `pyproject.toml`, add to `dependencies`:
```toml
"asyncssh>=2.18.0",
"aiohttp>=3.10.0",
```

**Step 2: Sync dependencies**

```bash
uv sync
```
Expected: resolves without errors.

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add asyncssh and aiohttp dependencies"
```

---

## Task 2: Create `machines.py` — MachineConnection protocol + LocalMachine

**Files:**
- Create: `src/ccbot/machines.py`
- Create: `tests/ccbot/test_machines.py`

**Step 1: Write failing test for LocalMachine.list_dir**

```python
# tests/ccbot/test_machines.py
import pytest
import tempfile
from pathlib import Path
from ccbot.machines import LocalMachine

@pytest.fixture
def local():
    return LocalMachine(machine_id="local")

@pytest.mark.asyncio
async def test_local_list_dir_returns_sorted_non_hidden(local, tmp_path):
    (tmp_path / "beta").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / ".hidden").mkdir()
    result = await local.list_dir(str(tmp_path))
    assert result == ["alpha", "beta"]

@pytest.mark.asyncio
async def test_local_list_dir_missing_path_returns_empty(local):
    result = await local.list_dir("/nonexistent/path/xyz")
    assert result == []

@pytest.mark.asyncio
async def test_local_read_file_from_offset(local, tmp_path):
    f = tmp_path / "test.jsonl"
    f.write_bytes(b"hello world")
    result = await local.read_file_from_offset(str(f), offset=6)
    assert result == b"world"

@pytest.mark.asyncio
async def test_local_read_file_offset_beyond_eof_returns_empty(local, tmp_path):
    f = tmp_path / "test.jsonl"
    f.write_bytes(b"hi")
    result = await local.read_file_from_offset(str(f), offset=100)
    assert result == b""
```

**Step 2: Run tests to verify failure**

```bash
uv run pytest tests/ccbot/test_machines.py -v
```
Expected: `ModuleNotFoundError: No module named 'ccbot.machines'`

**Step 3: Implement `machines.py`**

```python
"""Machine abstraction — local and remote machine connections.

MachineConnection protocol defines the interface for controlling tmux and
reading files on a machine, whether local or remote via asyncssh.

LocalMachine wraps TmuxManager + local filesystem.
RemoteMachine wraps an asyncssh connection to a Tailscale peer.

Key class: MachineRegistry (singleton) — loads machines from machines.json,
returns the correct MachineConnection by machine_id.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .tmux_manager import TmuxWindow

logger = logging.getLogger(__name__)


class MachineConnection(Protocol):
    """Interface for controlling tmux and reading files on a machine."""

    machine_id: str

    async def list_dir(self, path: str) -> list[str]:
        """Return sorted non-hidden subdirectory names under path."""
        ...

    async def read_file_from_offset(self, path: str, offset: int) -> bytes:
        """Return bytes from path starting at offset. Returns b"" if offset >= size."""
        ...

    async def list_windows(self) -> list["TmuxWindow"]:
        """List tmux windows on this machine."""
        ...

    async def find_window_by_id(self, window_id: str) -> "TmuxWindow | None":
        """Find a tmux window by ID."""
        ...

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        """Send keys to a tmux window."""
        ...

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture the visible content of a tmux pane."""
        ...

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        dangerous: bool = False,
    ) -> tuple[bool, str, str, str]:
        """Create a tmux window. Returns (success, message, window_name, window_id)."""
        ...

    async def kill_window(self, window_id: str) -> bool:
        """Kill a tmux window by ID."""
        ...

    async def file_size(self, path: str) -> int | None:
        """Return file size in bytes, or None if file does not exist."""
        ...


class LocalMachine:
    """MachineConnection backed by local filesystem and TmuxManager."""

    def __init__(self, machine_id: str = "local") -> None:
        self.machine_id = machine_id

    async def list_dir(self, path: str) -> list[str]:
        p = Path(path).expanduser()
        try:
            return sorted(
                d.name
                for d in p.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
        except (PermissionError, OSError):
            return []

    async def read_file_from_offset(self, path: str, offset: int) -> bytes:
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                return f.read()
        except OSError:
            return b""

    async def file_size(self, path: str) -> int | None:
        try:
            return Path(path).stat().st_size
        except OSError:
            return None

    async def list_windows(self) -> list[TmuxWindow]:
        from .tmux_manager import tmux_manager
        return await tmux_manager.list_windows()

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        from .tmux_manager import tmux_manager
        return await tmux_manager.find_window_by_id(window_id)

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        from .tmux_manager import tmux_manager
        return await tmux_manager.send_keys(window_id, text, enter=enter, literal=literal)

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        from .tmux_manager import tmux_manager
        return await tmux_manager.capture_pane(window_id, with_ansi=with_ansi)

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        dangerous: bool = False,
    ) -> tuple[bool, str, str, str]:
        from .config import config
        from .tmux_manager import tmux_manager
        cmd = config.claude_command
        if dangerous:
            cmd = f"{cmd} --dangerously-skip-permissions"
        return await tmux_manager.create_window(
            work_dir, window_name=window_name, start_claude=True, claude_command=cmd
        )

    async def kill_window(self, window_id: str) -> bool:
        from .tmux_manager import tmux_manager
        return await tmux_manager.kill_window(window_id)
```

**Step 4: Update `tmux_manager.create_window` to accept `claude_command` override**

In `src/ccbot/tmux_manager.py`, change `create_window` signature:
```python
async def create_window(
    self,
    work_dir: str,
    window_name: str | None = None,
    start_claude: bool = True,
    claude_command: str | None = None,  # ADD THIS
) -> tuple[bool, str, str, str]:
```

And in `_create_and_start`:
```python
if start_claude:
    pane = window.active_pane
    if pane:
        cmd = claude_command if claude_command is not None else config.claude_command
        pane.send_keys(cmd, enter=True)
```

**Step 5: Run tests**

```bash
uv run pytest tests/ccbot/test_machines.py -v
uv run pyright src/ccbot/
```
Expected: all tests PASS, 0 type errors.

**Step 6: Commit**

```bash
git add src/ccbot/machines.py src/ccbot/tmux_manager.py tests/ccbot/test_machines.py
git commit -m "feat: add MachineConnection protocol and LocalMachine"
```

---

## Task 3: Create RemoteMachine (asyncssh)

**Files:**
- Modify: `src/ccbot/machines.py`
- Modify: `tests/ccbot/test_machines.py`

**Step 1: Write failing test for RemoteMachine**

Add to `tests/ccbot/test_machines.py`:
```python
from unittest.mock import AsyncMock, MagicMock, patch
from ccbot.machines import RemoteMachine

@pytest.mark.asyncio
async def test_remote_list_dir_parses_output():
    """RemoteMachine.list_dir parses ls output from SSH."""
    machine = RemoteMachine(machine_id="fedora", host="fedora.tail.ts.net", user="user")
    mock_result = MagicMock()
    mock_result.stdout = "alpha\nbeta\n.hidden\n"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    machine._conn = mock_conn
    result = await machine.list_dir("/home/user/projects")
    mock_conn.run.assert_called_once()
    assert "alpha" in result
    assert "beta" in result
    assert ".hidden" not in result

@pytest.mark.asyncio
async def test_remote_read_file_from_offset():
    """RemoteMachine.read_file_from_offset uses tail via SSH."""
    machine = RemoteMachine(machine_id="fedora", host="fedora.tail.ts.net", user="user")
    mock_result = MagicMock()
    mock_result.stdout = None
    mock_result.stdout_bytes = b"world"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    machine._conn = mock_conn
    result = await machine.read_file_from_offset("/path/to/file.jsonl", offset=6)
    assert result == b"world"
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/ccbot/test_machines.py::test_remote_list_dir_parses_output -v
```
Expected: `ImportError: cannot import name 'RemoteMachine'`

**Step 3: Implement RemoteMachine in `machines.py`**

Add after `LocalMachine`:
```python
class RemoteMachine:
    """MachineConnection backed by an asyncssh connection to a Tailscale peer."""

    def __init__(self, machine_id: str, host: str, user: str) -> None:
        self.machine_id = machine_id
        self._host = host
        self._user = user
        self._conn: object | None = None  # asyncssh.SSHClientConnection when connected
        self._tmux_session_name: str = "ccbot"

    async def _get_conn(self) -> object:
        """Return existing connection or create a new one."""
        import asyncssh  # type: ignore[import-untyped]
        if self._conn is None:
            logger.info("Connecting to %s@%s via SSH", self._user, self._host)
            self._conn = await asyncssh.connect(
                self._host,
                username=self._user,
                known_hosts=None,  # Tailscale certs handle identity
            )
        return self._conn

    async def _run(self, cmd: str, binary: bool = False) -> object:
        """Run a command on the remote machine. Reconnects on connection loss."""
        import asyncssh  # type: ignore[import-untyped]
        try:
            conn = await self._get_conn()
            return await conn.run(cmd, encoding=None if binary else "utf-8")  # type: ignore[union-attr]
        except (asyncssh.DisconnectError, asyncssh.ConnectionLost, OSError):
            logger.warning("SSH connection lost to %s, reconnecting", self._host)
            self._conn = None
            conn = await self._get_conn()
            return await conn.run(cmd, encoding=None if binary else "utf-8")  # type: ignore[union-attr]

    async def list_dir(self, path: str) -> list[str]:
        try:
            result = await self._run(
                f"find {path!r} -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf '%f\\n' 2>/dev/null | sort"
            )
            output: str = result.stdout or ""  # type: ignore[union-attr]
            return [line for line in output.splitlines() if line]
        except Exception as e:
            logger.warning("list_dir failed on %s:%s: %s", self.machine_id, path, e)
            return []

    async def read_file_from_offset(self, path: str, offset: int) -> bytes:
        try:
            result = await self._run(
                f"tail -c +{offset + 1} {path!r} 2>/dev/null", binary=True
            )
            data = result.stdout  # type: ignore[union-attr]
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            return b""
        except Exception as e:
            logger.warning("read_file_from_offset failed on %s: %s", self.machine_id, e)
            return b""

    async def file_size(self, path: str) -> int | None:
        try:
            result = await self._run(f"stat -c %s {path!r} 2>/dev/null")
            out: str = (result.stdout or "").strip()  # type: ignore[union-attr]
            return int(out) if out.isdigit() else None
        except Exception:
            return None

    async def list_windows(self) -> list[TmuxWindow]:
        from .tmux_manager import TmuxWindow as TW
        try:
            result = await self._run(
                f"tmux list-windows -t {self._tmux_session_name} "
                "-F '#{window_id}:#{window_name}:#{pane_current_path}:#{pane_current_command}' 2>/dev/null"
            )
            out: str = result.stdout or ""  # type: ignore[union-attr]
            windows = []
            for line in out.splitlines():
                parts = line.split(":", 3)
                if len(parts) == 4:
                    wid, wname, cwd, cmd = parts
                    if wname == "__main__":
                        continue
                    windows.append(TW(window_id=wid, window_name=wname, cwd=cwd, pane_current_command=cmd))
            return windows
        except Exception as e:
            logger.warning("list_windows failed on %s: %s", self.machine_id, e)
            return []

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        windows = await self.list_windows()
        for w in windows:
            if w.window_id == window_id:
                return w
        return None

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        try:
            escaped = text.replace("'", "'\\''")
            await self._run(
                f"tmux send-keys -t {self._tmux_session_name}:{window_id} -l {escaped!r}"
            )
            if enter:
                await asyncio.sleep(0.5)
                await self._run(
                    f"tmux send-keys -t {self._tmux_session_name}:{window_id} Enter"
                )
            return True
        except Exception as e:
            logger.warning("send_keys failed on %s: %s", self.machine_id, e)
            return False

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        try:
            ansi_flag = "-e " if with_ansi else ""
            result = await self._run(
                f"tmux capture-pane -p {ansi_flag}-t {self._tmux_session_name}:{window_id} 2>/dev/null"
            )
            return result.stdout  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("capture_pane failed on %s: %s", self.machine_id, e)
            return None

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        dangerous: bool = False,
    ) -> tuple[bool, str, str, str]:
        from .config import config
        try:
            wname = window_name or Path(work_dir).name
            cmd = config.claude_command
            if dangerous:
                cmd = f"{cmd} --dangerously-skip-permissions"
            result = await self._run(
                f"tmux new-window -t {self._tmux_session_name} -c {work_dir!r} "
                f"-n {wname!r} -P -F '#{{window_id}}' {cmd!r}"
            )
            window_id = (result.stdout or "").strip()  # type: ignore[union-attr]
            if not window_id:
                return False, "Failed to get window_id from tmux", "", ""
            return True, f"Created window '{wname}' at {work_dir}", wname, window_id
        except Exception as e:
            logger.warning("create_window failed on %s: %s", self.machine_id, e)
            return False, f"Failed to create window: {e}", "", ""

    async def kill_window(self, window_id: str) -> bool:
        try:
            await self._run(
                f"tmux kill-window -t {self._tmux_session_name}:{window_id} 2>/dev/null"
            )
            return True
        except Exception as e:
            logger.warning("kill_window failed on %s: %s", self.machine_id, e)
            return False
```

**Step 4: Run tests**

```bash
uv run pytest tests/ccbot/test_machines.py -v
uv run pyright src/ccbot/machines.py
```
Expected: all PASS, 0 type errors.

**Step 5: Commit**

```bash
git add src/ccbot/machines.py tests/ccbot/test_machines.py
git commit -m "feat: add RemoteMachine with asyncssh for remote tmux + file access"
```

---

## Task 4: MachineRegistry — load `machines.json`, expose machines

**Files:**
- Modify: `src/ccbot/machines.py`
- Modify: `src/ccbot/config.py`
- Create: `tests/ccbot/test_machine_registry.py`

**Step 1: Write failing test**

```python
# tests/ccbot/test_machine_registry.py
import json
import pytest
from ccbot.machines import MachineRegistry, LocalMachine, RemoteMachine

@pytest.fixture
def registry_json(tmp_path):
    data = {
        "hook_port": 8080,
        "machines": {
            "macbook": {"display": "MacBook", "type": "local"},
            "fedora": {"display": "Fedora", "host": "fedora.ts.net", "user": "me"},
        }
    }
    f = tmp_path / "machines.json"
    f.write_text(json.dumps(data))
    return f

def test_registry_loads_local_machine(registry_json):
    reg = MachineRegistry(registry_json)
    m = reg.get("macbook")
    assert isinstance(m, LocalMachine)
    assert m.machine_id == "macbook"

def test_registry_loads_remote_machine(registry_json):
    reg = MachineRegistry(registry_json)
    m = reg.get("fedora")
    assert isinstance(m, RemoteMachine)
    assert m.machine_id == "fedora"
    assert m._host == "fedora.ts.net"

def test_registry_all_machines(registry_json):
    reg = MachineRegistry(registry_json)
    ids = [m.machine_id for m in reg.all()]
    assert "macbook" in ids
    assert "fedora" in ids

def test_registry_missing_file_returns_local_only(tmp_path):
    reg = MachineRegistry(tmp_path / "nonexistent.json")
    machines = reg.all()
    assert len(machines) == 1
    assert isinstance(machines[0], LocalMachine)

def test_registry_hook_port(registry_json):
    reg = MachineRegistry(registry_json)
    assert reg.hook_port == 8080

def test_registry_local_machine_id(registry_json):
    reg = MachineRegistry(registry_json)
    assert reg.local_machine_id == "macbook"
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/ccbot/test_machine_registry.py -v
```
Expected: `ImportError: cannot import name 'MachineRegistry'`

**Step 3: Implement MachineRegistry in `machines.py`**

Add at the bottom of `machines.py`:
```python
class MachineRegistry:
    """Loads machine config from machines.json and provides MachineConnection instances."""

    def __init__(self, machines_file: Path) -> None:
        self._machines: dict[str, MachineConnection] = {}
        self._local_machine_id: str = "local"
        self.hook_port: int = 8080
        self._load(machines_file)

    def _load(self, path: Path) -> None:
        if not path.exists():
            logger.warning("machines.json not found at %s, using local-only mode", path)
            self._machines = {"local": LocalMachine("local")}
            self._local_machine_id = "local"
            return

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load machines.json: %s", e)
            self._machines = {"local": LocalMachine("local")}
            self._local_machine_id = "local"
            return

        self.hook_port = data.get("hook_port", 8080)
        machines_data: dict = data.get("machines", {})
        for machine_id, cfg in machines_data.items():
            if cfg.get("type") == "local":
                self._machines[machine_id] = LocalMachine(machine_id)
                self._local_machine_id = machine_id
            else:
                host = cfg.get("host", "")
                user = cfg.get("user", "")
                if host and user:
                    self._machines[machine_id] = RemoteMachine(machine_id, host, user)

        if not self._machines:
            self._machines = {"local": LocalMachine("local")}
            self._local_machine_id = "local"

    def get(self, machine_id: str) -> MachineConnection:
        """Get machine by ID, falling back to local if not found."""
        return self._machines.get(machine_id, self._machines[self._local_machine_id])

    def all(self) -> list[MachineConnection]:
        """Return all configured machines."""
        return list(self._machines.values())

    @property
    def local_machine_id(self) -> str:
        return self._local_machine_id

    def display_name(self, machine_id: str) -> str:
        """Return display name for a machine_id (same as machine_id if not configured)."""
        return machine_id
```

Also add `import json` at the top of `machines.py`.

**Step 4: Add `machines_file` to `config.py`**

In `config.py`, after the other state file paths:
```python
self.machines_file = self.config_dir / "machines.json"
```

**Step 5: Add singleton to `machines.py`**

At the bottom of `machines.py`:
```python
from .config import config as _config

machine_registry = MachineRegistry(_config.machines_file)
```

**Step 6: Run tests**

```bash
uv run pytest tests/ccbot/test_machine_registry.py tests/ccbot/test_machines.py -v
uv run pyright src/ccbot/machines.py src/ccbot/config.py
```
Expected: all PASS, 0 type errors.

**Step 7: Commit**

```bash
git add src/ccbot/machines.py src/ccbot/config.py tests/ccbot/test_machine_registry.py
git commit -m "feat: add MachineRegistry loading from machines.json"
```

---

## Task 5: WindowBinding dataclass + update `session.py` state

**Files:**
- Modify: `src/ccbot/session.py`
- Modify: `tests/ccbot/test_session.py`

**Context:** `thread_bindings` currently maps `user_id → {thread_id → window_id (str)}`.
We change to `user_id → {thread_id → WindowBinding}` where `WindowBinding` holds `window_id`, `machine`, and `dangerous`. Window keys in `window_states`, `window_display_names`, `user_window_offsets` become machine-namespaced: `"local:@0"`, `"fedora:@3"`.

**Step 1: Write failing tests**

Add to `tests/ccbot/test_session.py`:
```python
from ccbot.session import SessionManager, WindowBinding

class TestWindowBinding:
    def test_bind_with_machine_and_dangerous(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", machine="fedora", dangerous=True)
        binding = mgr.get_binding_for_thread(100, 1)
        assert binding is not None
        assert binding.window_id == "@1"
        assert binding.machine == "fedora"
        assert binding.dangerous is True

    def test_bind_defaults_to_local_non_dangerous(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1")
        binding = mgr.get_binding_for_thread(100, 1)
        assert binding is not None
        assert binding.machine == "local"
        assert binding.dangerous is False

    def test_iter_thread_bindings_yields_bindings(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", machine="fedora")
        mgr.bind_thread(200, 2, "@2")
        results = list(mgr.iter_thread_bindings())
        assert (100, 1, "@1") in [(u, t, b.window_id) for u, t, b in results]
        assert (200, 2, "@2") in [(u, t, b.window_id) for u, t, b in results]

    def test_window_key_is_machine_namespaced(self, mgr: SessionManager) -> None:
        mgr.bind_thread(100, 1, "@1", machine="fedora")
        assert "fedora:@1" in mgr.window_display_names or \
               mgr.get_display_name("fedora:@1") is not None
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/ccbot/test_session.py::TestWindowBinding -v
```
Expected: `ImportError: cannot import name 'WindowBinding'`

**Step 3: Add WindowBinding dataclass to `session.py`**

After the existing imports, add:
```python
@dataclass
class WindowBinding:
    """Thread-to-window binding with machine and permissions metadata."""
    window_id: str
    machine: str = "local"
    dangerous: bool = False

    def make_key(self) -> str:
        """Namespaced window key: 'machine:window_id'."""
        return f"{self.machine}:{self.window_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"window_id": self.window_id, "machine": self.machine, "dangerous": self.dangerous}

    @classmethod
    def from_value(cls, val: Any) -> "WindowBinding":
        """Load from persisted value — supports old plain-string format."""
        if isinstance(val, str):
            # Old format: plain window_id string → local machine, non-dangerous
            return cls(window_id=val, machine="local", dangerous=False)
        return cls(
            window_id=val.get("window_id", ""),
            machine=val.get("machine", "local"),
            dangerous=val.get("dangerous", False),
        )
```

**Step 4: Update `thread_bindings` type and related methods in `session.py`**

Change the field declaration:
```python
thread_bindings: dict[int, dict[int, WindowBinding]] = field(default_factory=dict)
```

Update `bind_thread` signature and body:
```python
def bind_thread(
    self,
    user_id: int,
    thread_id: int,
    window_id: str,
    window_name: str = "",
    machine: str = "local",
    dangerous: bool = False,
) -> None:
    if user_id not in self.thread_bindings:
        self.thread_bindings[user_id] = {}
    binding = WindowBinding(window_id=window_id, machine=machine, dangerous=dangerous)
    self.thread_bindings[user_id][thread_id] = binding
    key = binding.make_key()
    if window_name:
        self.window_display_names[key] = window_name
    self._save_state()
    logger.info(
        "Bound thread %d -> %s (%s) for user %d [dangerous=%s]",
        thread_id, key, window_name or key, user_id, dangerous,
    )
```

Add `get_binding_for_thread`:
```python
def get_binding_for_thread(self, user_id: int, thread_id: int) -> WindowBinding | None:
    bindings = self.thread_bindings.get(user_id)
    if not bindings:
        return None
    return bindings.get(thread_id)
```

Update `get_window_for_thread` to use the binding:
```python
def get_window_for_thread(self, user_id: int, thread_id: int) -> str | None:
    b = self.get_binding_for_thread(user_id, thread_id)
    return b.window_id if b else None
```

Update `unbind_thread` to return the binding's window_id:
```python
def unbind_thread(self, user_id: int, thread_id: int) -> str | None:
    bindings = self.thread_bindings.get(user_id)
    if not bindings or thread_id not in bindings:
        return None
    binding = bindings.pop(thread_id)
    if not bindings:
        del self.thread_bindings[user_id]
    self._save_state()
    return binding.window_id
```

Update `iter_thread_bindings` to yield `(user_id, thread_id, WindowBinding)`:
```python
def iter_thread_bindings(self) -> Iterator[tuple[int, int, WindowBinding]]:
    for user_id, bindings in self.thread_bindings.items():
        for thread_id, binding in bindings.items():
            yield user_id, thread_id, binding
```

Update `_save_state` for thread_bindings:
```python
"thread_bindings": {
    str(uid): {str(tid): b.to_dict() for tid, b in bindings.items()}
    for uid, bindings in self.thread_bindings.items()
},
```

Update `_load_state` for thread_bindings:
```python
self.thread_bindings = {
    int(uid): {
        int(tid): WindowBinding.from_value(val)
        for tid, val in bindings.items()
    }
    for uid, bindings in state.get("thread_bindings", {}).items()
}
```

Update `get_display_name` — keys are now `machine:window_id`:
```python
def get_display_name(self, window_key: str) -> str:
    return self.window_display_names.get(window_key, window_key)
```

**Step 5: Update all callers of `iter_thread_bindings` in the codebase**

Search for callers: `grep -r "iter_thread_bindings" src/`
Each call site that destructures `(user_id, thread_id, window_id)` needs updating to `(user_id, thread_id, binding)` and using `binding.window_id` where needed.

**Step 6: Update existing tests that call `bind_thread` with 3 positional args**

The old tests call `mgr.bind_thread(100, 1, "@1")` — this still works because `machine` and `dangerous` have defaults. But `iter_thread_bindings` now yields `WindowBinding` not `str`. Update the existing test:

```python
def test_iter_thread_bindings(self, mgr: SessionManager) -> None:
    mgr.bind_thread(100, 1, "@1")
    mgr.bind_thread(100, 2, "@2")
    mgr.bind_thread(200, 3, "@3")
    result = {(u, t, b.window_id) for u, t, b in mgr.iter_thread_bindings()}
    assert result == {(100, 1, "@1"), (100, 2, "@2"), (200, 3, "@3")}
```

**Step 7: Run all tests and type check**

```bash
uv run pytest tests/ccbot/test_session.py -v
uv run pyright src/ccbot/session.py
```
Expected: all PASS, 0 type errors.

**Step 8: Commit**

```bash
git add src/ccbot/session.py tests/ccbot/test_session.py
git commit -m "feat: add WindowBinding with machine+dangerous fields to thread_bindings"
```

---

## Task 6: Update `session.py` — machine-aware `send_to_window` and `resolve_stale_ids`

**Files:**
- Modify: `src/ccbot/session.py`

**Step 1: Update `send_to_window` to route through machine**

Replace the existing method:
```python
async def send_to_window(self, window_id: str, text: str, machine_id: str = "local") -> tuple[bool, str]:
    from .machines import machine_registry
    machine = machine_registry.get(machine_id)
    key = f"{machine_id}:{window_id}"
    display = self.get_display_name(key)
    logger.debug("send_to_window: key=%s, text_len=%d", key, len(text))
    window = await machine.find_window_by_id(window_id)
    if not window:
        return False, "Window not found (may have been closed)"
    success = await machine.send_keys(window.window_id, text)
    return (True, f"Sent to {display}") if success else (False, "Failed to send keys")
```

**Step 2: Update `resolve_stale_ids` to handle machine-namespaced keys and iterate all machines**

The stale ID resolution must now check each machine's live windows separately. Update the method to:
1. Accept a `machine_id` parameter for which machine to check
2. Or iterate all machines when called at startup

Update the call sites in `bot.py` (post_init) accordingly — now call `resolve_stale_ids()` which internally iterates machines.

Change the signature to resolve per-machine:
```python
async def resolve_stale_ids(self) -> None:
    from .machines import machine_registry
    for machine in machine_registry.all():
        await self._resolve_stale_ids_for_machine(machine.machine_id)
```

And rename existing logic to `_resolve_stale_ids_for_machine(machine_id)` — filtering `window_states`, `thread_bindings`, and `user_window_offsets` to only those matching this machine.

**Step 3: Run linting + type check**

```bash
uv run ruff check src/ tests/
uv run pyright src/ccbot/session.py
```
Expected: 0 errors.

**Step 4: Commit**

```bash
git add src/ccbot/session.py
git commit -m "feat: route send_to_window through MachineConnection, machine-aware stale ID resolution"
```

---

## Task 7: Create `hook_server.py` — HTTP endpoint for remote hooks

**Files:**
- Create: `src/ccbot/hook_server.py`
- Create: `tests/ccbot/test_hook_server.py`

**Step 1: Write failing test**

```python
# tests/ccbot/test_hook_server.py
import json
import pytest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer
from ccbot.hook_server import create_hook_app

@pytest.mark.asyncio
async def test_health_returns_ok():
    app = create_hook_app(on_hook=AsyncMock())
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

@pytest.mark.asyncio
async def test_hook_post_calls_callback():
    received = []
    async def on_hook(payload):
        received.append(payload)

    app = create_hook_app(on_hook=on_hook)
    payload = {
        "session_id": "abc123-def456-ghi789-jkl012-mno345",
        "cwd": "/home/user/projects",
        "hook_event_name": "SessionStart",
        "machine_id": "fedora",
        "window_id": "@3",
        "window_name": "my-project",
    }
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/hook", json=payload)
        assert resp.status == 200
        assert len(received) == 1
        assert received[0]["machine_id"] == "fedora"

@pytest.mark.asyncio
async def test_hook_post_missing_session_id_returns_400():
    app = create_hook_app(on_hook=AsyncMock())
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/hook", json={"cwd": "/path"})
        assert resp.status == 400
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/ccbot/test_hook_server.py -v
```
Expected: `ModuleNotFoundError: No module named 'ccbot.hook_server'`

**Step 3: Implement `hook_server.py`**

```python
"""HTTP server for receiving remote SessionStart hook notifications.

Remote machines run 'ccbot hook --remote http://bothost:PORT/hook' which
POSTs session info here instead of writing a local session_map.json file.

Key function: create_hook_app() — returns an aiohttp.Application.
The app is started in bot.py post_init alongside the Telegram bot.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)

HookCallback = Callable[[dict[str, Any]], Awaitable[None]]


def create_hook_app(on_hook: HookCallback) -> web.Application:
    """Create the aiohttp application for receiving hook POSTs.

    Args:
        on_hook: Async callback invoked with the validated hook payload.
    """
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def hook(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        session_id = payload.get("session_id", "")
        if not session_id:
            return web.json_response({"error": "missing session_id"}, status=400)

        logger.info(
            "Hook received: machine=%s window=%s session=%s",
            payload.get("machine_id"),
            payload.get("window_id"),
            session_id,
        )
        try:
            await on_hook(payload)
        except Exception as e:
            logger.error("Hook callback failed: %s", e)
            return web.json_response({"error": "callback failed"}, status=500)

        return web.json_response({"status": "ok"})

    app.router.add_get("/health", health)
    app.router.add_post("/hook", hook)
    return app


async def start_hook_server(
    on_hook: HookCallback,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> web.AppRunner:
    """Start the hook HTTP server. Returns the runner for later cleanup."""
    app = create_hook_app(on_hook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Hook server listening on %s:%d", host, port)
    return runner
```

**Step 4: Run tests**

```bash
uv run pytest tests/ccbot/test_hook_server.py -v
uv run pyright src/ccbot/hook_server.py
```
Expected: all PASS, 0 type errors.

**Step 5: Commit**

```bash
git add src/ccbot/hook_server.py tests/ccbot/test_hook_server.py
git commit -m "feat: add HTTP hook server for remote SessionStart notifications"
```

---

## Task 8: Start hook server in `bot.py` post_init; wire `on_hook` callback

**Files:**
- Modify: `src/ccbot/bot.py`

**Step 1: Add `_on_remote_hook` handler to bot.py**

After the existing imports, add a function that handles remote hook payloads:
```python
async def _on_remote_hook(payload: dict) -> None:
    """Handle a SessionStart hook POST from a remote machine.

    Writes the session info to local session_map.json so SessionMonitor
    can pick it up on the next poll cycle.
    """
    from .machines import machine_registry
    from .utils import atomic_write_json
    import json

    machine_id = payload.get("machine_id", "")
    window_id = payload.get("window_id", "")
    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    window_name = payload.get("window_name", "")

    if not (machine_id and window_id and session_id):
        logger.warning("Incomplete hook payload: %s", payload)
        return

    # Key format for remote machines: "machine_id:window_id"
    key = f"{machine_id}:{window_id}"

    session_map: dict = {}
    if config.session_map_file.exists():
        try:
            session_map = json.loads(config.session_map_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    session_map[key] = {
        "session_id": session_id,
        "cwd": cwd,
        "window_name": window_name,
        "machine": machine_id,
    }
    atomic_write_json(config.session_map_file, session_map)
    logger.info("Remote hook: wrote session_map entry %s -> %s", key, session_id)
```

**Step 2: Start hook server in `post_init`**

Find the existing `post_init` function in `bot.py`. Add:
```python
from .hook_server import start_hook_server
from .machines import machine_registry

# Start HTTP hook server for remote SessionStart notifications
hook_runner = await start_hook_server(
    on_hook=_on_remote_hook,
    port=machine_registry.hook_port,
)
context.application.bot_data["hook_runner"] = hook_runner
```

**Step 3: Clean up hook server in `post_shutdown`**

In the existing `post_shutdown` function, add:
```python
runner = context.application.bot_data.get("hook_runner")
if runner:
    await runner.cleanup()
```

**Step 4: Run linting and type check**

```bash
uv run ruff check src/ && uv run pyright src/ccbot/bot.py
```
Expected: 0 errors.

**Step 5: Commit**

```bash
git add src/ccbot/bot.py
git commit -m "feat: start hook HTTP server in bot post_init, handle remote hook payloads"
```

---

## Task 9: Update `hook.py` — add `--remote` and `--uninstall` flags

**Files:**
- Modify: `src/ccbot/hook.py`
- Modify: `tests/ccbot/test_hook.py`

**Step 1: Write failing tests**

Add to `tests/ccbot/test_hook.py`:
```python
from unittest.mock import patch, MagicMock
import urllib.request

def test_hook_uninstall_removes_hook(tmp_path, monkeypatch):
    """--uninstall removes the ccbot hook from settings.json."""
    from ccbot.hook import _uninstall_hook
    settings = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "ccbot hook"}]}]
        }
    }
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)
    result = _uninstall_hook()
    assert result == 0
    updated = json.loads(settings_file.read_text())
    session_start = updated.get("hooks", {}).get("SessionStart", [])
    # All ccbot hooks should be removed
    for entry in session_start:
        for h in entry.get("hooks", []):
            assert "ccbot hook" not in h.get("command", "")

def test_hook_install_with_remote_url(tmp_path, monkeypatch):
    """--install --remote writes remote URL to hook command."""
    from ccbot.hook import _install_hook
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("ccbot.hook._CLAUDE_SETTINGS_FILE", settings_file)
    result = _install_hook(remote_url="http://macbook:8080/hook")
    assert result == 0
    settings = json.loads(settings_file.read_text())
    cmds = [
        h["command"]
        for e in settings["hooks"]["SessionStart"]
        for h in e["hooks"]
    ]
    assert any("--remote" in c and "http://macbook:8080/hook" in c for c in cmds)
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/ccbot/test_hook.py -v -k "uninstall or remote"
```
Expected: failures on new tests.

**Step 3: Update `hook.py`**

Add `_uninstall_hook()` function:
```python
def _uninstall_hook() -> int:
    """Remove the ccbot hook from Claude's settings.json."""
    settings_file = _CLAUDE_SETTINGS_FILE
    if not settings_file.exists():
        print("No settings.json found — nothing to uninstall")
        return 0

    try:
        settings = json.loads(settings_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {settings_file}: {e}", file=sys.stderr)
        return 1

    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])
    new_entries = [
        entry for entry in session_start
        if not any(
            (h.get("command", "") == _HOOK_COMMAND_SUFFIX or
             h.get("command", "").endswith("/" + _HOOK_COMMAND_SUFFIX) or
             "--remote" in h.get("command", ""))
            for h in entry.get("hooks", [])
        )
    ]
    settings["hooks"]["SessionStart"] = new_entries
    try:
        settings_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    print(f"Hook uninstalled from {settings_file}")
    return 0
```

Update `_install_hook` to accept `remote_url: str | None = None`:
```python
def _install_hook(remote_url: str | None = None) -> int:
    ...
    ccbot_path = _find_ccbot_path()
    if remote_url:
        hook_command = f"{ccbot_path} hook --remote {remote_url}"
    else:
        hook_command = f"{ccbot_path} hook"
    ...
```

Update `hook_main` argument parsing:
```python
parser.add_argument("--uninstall", action="store_true")
parser.add_argument("--remote", metavar="URL", default=None)
parser.add_argument("--install", action="store_true")

if args.uninstall:
    sys.exit(_uninstall_hook())
if args.install:
    sys.exit(_install_hook(remote_url=args.remote))
```

For `--remote` in hook processing (not install):
```python
if args.remote:
    _post_hook_remote(payload_dict, args.remote)
    return
# else: write local session_map.json as before
```

Add `_post_hook_remote`:
```python
def _post_hook_remote(payload: dict, url: str) -> None:
    """POST hook payload to remote bot endpoint."""
    import urllib.request as req
    data = json.dumps(payload).encode()
    try:
        r = req.urlopen(req.Request(url, data=data, headers={"Content-Type": "application/json"}), timeout=5)
        logger.info("Remote hook POST to %s: status=%d", url, r.status)
    except Exception as e:
        logger.error("Remote hook POST failed: %s", e)
```

**Step 4: Run tests**

```bash
uv run pytest tests/ccbot/test_hook.py -v
uv run pyright src/ccbot/hook.py
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add src/ccbot/hook.py tests/ccbot/test_hook.py
git commit -m "feat: add --remote, --uninstall flags to ccbot hook"
```

---

## Task 10: Update `session_monitor.py` — read remote JSONL files via machine

**Files:**
- Modify: `src/ccbot/session_monitor.py`

**Context:** The monitor currently reads local JSONL files via `aiofiles.open()`. Each session in `session_map.json` now has a `machine` field. The monitor must use `machine_registry.get(machine_id).read_file_from_offset()` for remote sessions.

**Step 1: Update `load_session_map` processing in `session_monitor.py`**

Find where the monitor reads `session_map.json`. Each entry now has a `"machine"` key. Store this in `TrackedSession` (or wherever sessions are tracked) so the poll loop knows which machine to read from.

Update `MonitorState` / `TrackedSession` (in `monitor_state.py`) to include `machine_id: str = "local"`.

In `monitor_state.py`, find `TrackedSession` dataclass and add:
```python
machine_id: str = "local"
```

Update persistence in `monitor_state.py` to save/load `machine_id`.

**Step 2: Update the poll loop to use machine for file reads**

In `session_monitor.py`, find where JSONL files are read (likely an `aiofiles.open()` call). Replace with:

```python
from .machines import machine_registry

# Get machine for this session
machine = machine_registry.get(session.machine_id)
new_data = await machine.read_file_from_offset(str(session.file_path), session.last_byte_offset)
if not new_data:
    continue
```

Also update file size check (for detecting truncation) to use `machine.file_size()`.

**Step 3: Update `session_map.json` loading to extract `machine` field**

When loading entries from `session_map.json`, read the `machine` field:
```python
machine_id = info.get("machine", "local")
# store in TrackedSession
```

**Step 4: Run linting + type check**

```bash
uv run ruff check src/ && uv run pyright src/ccbot/session_monitor.py src/ccbot/monitor_state.py
```
Expected: 0 errors.

**Step 5: Commit**

```bash
git add src/ccbot/session_monitor.py src/ccbot/monitor_state.py
git commit -m "feat: read remote JSONL files via MachineConnection in session_monitor"
```

---

## Task 11: Update `directory_browser.py` — machine picker + permissions mode

**Files:**
- Modify: `src/ccbot/handlers/directory_browser.py`
- Modify: `src/ccbot/handlers/callback_data.py`
- Modify: `src/ccbot/bot.py`

**Step 1: Add new callback data constants**

In `callback_data.py`, add:
```python
CB_MACHINE_SELECT = "machine_select:"   # machine picker selection
CB_PERM_NORMAL = "perm_normal"          # permissions: normal
CB_PERM_DANGEROUS = "perm_dangerous"    # permissions: skip-permissions
```

**Step 2: Add machine picker builder to `directory_browser.py`**

```python
BROWSE_MACHINE_KEY = "browse_machine"   # selected machine_id

def build_machine_picker() -> tuple[str, InlineKeyboardMarkup]:
    """Build machine selection keyboard from machine_registry."""
    from ..machines import machine_registry
    machines = machine_registry.all()
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(machines), 2):
        row = []
        for m in machines[i:i+2]:
            label = machine_registry.display_name(m.machine_id)
            row.append(InlineKeyboardButton(
                f"🖥 {label}",
                callback_data=f"{CB_MACHINE_SELECT}{m.machine_id}"
            ))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Cancel", callback_data=CB_DIR_CANCEL)])
    return "*Select Machine*\n\nWhich machine should this session run on?", InlineKeyboardMarkup(buttons)
```

**Step 3: Update `build_directory_browser` to use machine's `list_dir`**

Change `build_directory_browser` to accept `machine_id` and use the machine for listing:

```python
async def build_directory_browser(
    current_path: str, machine_id: str = "local", page: int = 0
) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    from ..machines import machine_registry
    machine = machine_registry.get(machine_id)
    subdirs = await machine.list_dir(current_path)
    # rest of the function unchanged, using subdirs directly
```

Note: function is now `async`.

**Step 4: Add permissions mode picker**

```python
def build_permissions_picker(machine_id: str, work_dir: str) -> tuple[str, InlineKeyboardMarkup]:
    from ..machines import machine_registry
    display = machine_registry.display_name(machine_id)
    dirname = work_dir.rstrip("/").split("/")[-1]
    text = f"*Run mode for [{display}] {dirname}*\n\nNormal or skip all permission prompts?"
    buttons = [[
        InlineKeyboardButton("Normal", callback_data=CB_PERM_NORMAL),
        InlineKeyboardButton("Skip permissions ⚡", callback_data=CB_PERM_DANGEROUS),
    ]]
    return text, InlineKeyboardMarkup(buttons)
```

**Step 5: Update bot.py callback handler**

In `bot.py`, add handling for `CB_MACHINE_SELECT`, `CB_PERM_NORMAL`, `CB_PERM_DANGEROUS`.

Flow in callback handler:
1. `CB_MACHINE_SELECT<id>` → store machine in `user_data[BROWSE_MACHINE_KEY]`, show directory browser for that machine (start at `~`)
2. `CB_DIR_CONFIRM` → instead of immediately creating window, show permissions picker
3. `CB_PERM_NORMAL` / `CB_PERM_DANGEROUS` → create window with `dangerous=True/False`, bind thread, rename topic

Update topic renaming after window creation:
```python
from .machines import machine_registry
display = machine_registry.display_name(machine_id)
topic_name = f"[{display}] {window_name}"
if dangerous:
    topic_name += " ⚡"
# rename the Telegram forum topic
await context.bot.edit_forum_topic(chat_id=..., message_thread_id=..., name=topic_name)
```

Update `bind_thread` calls to include `machine=machine_id, dangerous=dangerous`.

**Step 6: Update first-message handler flow**

In `bot.py`, when a message arrives for an unbound topic, instead of immediately showing the directory browser, first show the machine picker (if more than one machine is configured). If only one machine, skip the picker and go straight to directory browser.

```python
from .machines import machine_registry
if len(machine_registry.all()) > 1:
    text, keyboard = build_machine_picker()
    await safe_reply(update, context, text, reply_markup=keyboard)
    user_data[STATE_KEY] = STATE_SELECTING_MACHINE
else:
    # single machine — skip picker
    machine_id = machine_registry.local_machine_id
    user_data[BROWSE_MACHINE_KEY] = machine_id
    text, keyboard, subdirs = await build_directory_browser(str(Path.home()), machine_id=machine_id)
    ...
```

**Step 7: Run linting + type check**

```bash
uv run ruff check src/ && uv run pyright src/ccbot/
```
Expected: 0 errors.

**Step 8: Commit**

```bash
git add src/ccbot/handlers/directory_browser.py src/ccbot/handlers/callback_data.py src/ccbot/bot.py
git commit -m "feat: add machine picker + permissions mode to directory browser session creation"
```

---

## Task 12: Add `ccbot setup` command

**Files:**
- Create: `src/ccbot/setup_cmd.py`
- Modify: `src/ccbot/main.py`
- Modify: `pyproject.toml` (add `textual` or `prompt_toolkit` dependency)

**Step 1: Add TUI dependency**

In `pyproject.toml`, add to `dependencies`:
```toml
"prompt_toolkit>=3.0.0",
```

Run `uv sync`.

**Step 2: Create `setup_cmd.py`**

```python
"""ccbot setup — interactive fleet configuration and provisioning.

Discovers Tailscale peers, lets the user select machines to manage,
prompts for SSH user and display name, then:
  - Writes ~/.ccbot/machines.json
  - Checks SSH connectivity
  - Installs ccbot via uv tool install on each remote machine
  - Installs the SessionStart hook (local or --remote)
  - Verifies hook endpoint reachability

All steps are idempotent. Partial failures are summarized at the end.

Key functions: setup_main() (CLI entry point).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field

from prompt_toolkit import prompt
from prompt_toolkit.shortcuts import checkboxlist_dialog, input_dialog

from .utils import ccbot_dir, atomic_write_json

HOOK_DEFAULT_PORT = 8080


@dataclass
class MachineSetupResult:
    machine_id: str
    success: bool
    errors: list[str] = field(default_factory=list)


def _get_tailscale_peers() -> list[dict]:
    """Run tailscale status --json and return peer list."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        peers = list(data.get("Peer", {}).values())
        self_node = data.get("Self", {})
        if self_node:
            self_node["_is_self"] = True
            peers.insert(0, self_node)
        return peers
    except Exception as e:
        print(f"Warning: could not query Tailscale: {e}", file=sys.stderr)
        return []


def _detect_github_url() -> str:
    """Auto-detect the GitHub repo URL from local git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent)
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_local_tailscale_hostname() -> str:
    """Get the local machine's Tailscale hostname."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        self_node = data.get("Self", {})
        return self_node.get("DNSName", "").rstrip(".")
    except Exception:
        return ""


def _ssh_check(user: str, host: str) -> bool:
    """Test SSH connectivity. Returns True if successful."""
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
    """Install ccbot via uv tool install on remote machine."""
    cmd = f"uv tool install 'ccbot @ git+{github_url}' --force"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_hook_remote(user: str, host: str, remote_url: str) -> bool:
    """Install SessionStart hook on remote machine pointing to bot URL."""
    cmd = f"ccbot hook --install --remote {remote_url}"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_endpoint_reachable(user: str, host: str, url: str) -> bool:
    """Verify the bot's hook endpoint is reachable from the remote machine."""
    cmd = f"curl -sf {url}/health"
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_hook_local() -> bool:
    """Install hook locally (writes local session_map.json)."""
    try:
        result = subprocess.run(
            ["ccbot", "hook", "--install"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def _load_existing_machines(machines_file: Path) -> dict:
    if machines_file.exists():
        try:
            return json.loads(machines_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"hook_port": HOOK_DEFAULT_PORT, "machines": {}}


def setup_main(target_machine: str | None = None) -> None:
    """Run the ccbot setup TUI."""
    config_dir = ccbot_dir()
    machines_file = config_dir / "machines.json"
    existing = _load_existing_machines(machines_file)
    existing_machine_ids = set(existing.get("machines", {}).keys())

    github_url = _detect_github_url()
    if not github_url:
        print("Warning: could not detect GitHub URL from git remote origin")
        github_url = prompt("GitHub repo URL (e.g. https://github.com/user/ccbot): ").strip()

    local_hostname = _get_local_tailscale_hostname()

    # --- TUI: peer selection ---
    peers = _get_tailscale_peers()
    if not peers and target_machine is None:
        print("No Tailscale peers found. Is Tailscale running?")
        sys.exit(1)

    if target_machine:
        # Non-interactive: target one machine
        selected_hostnames = [target_machine]
    else:
        # Build checkbox list
        choices = []
        defaults = []
        for peer in peers:
            hostname = peer.get("DNSName", "").rstrip(".")
            is_self = peer.get("_is_self", False)
            label = f"{hostname}{'  (this machine)' if is_self else ''}"
            choices.append((hostname, label))
            if is_self or hostname in existing_machine_ids:
                defaults.append(hostname)

        result = checkboxlist_dialog(
            title="CCBOT MACHINE SETUP",
            text="Select machines to manage (Space=toggle, Enter=confirm):",
            values=choices,
            default_values=defaults,
        ).run()

        if result is None:
            print("Cancelled.")
            sys.exit(0)
        selected_hostnames = result

    # --- Prompt for SSH user + display name for each selected remote ---
    machines_config: dict = {}
    local_machine_id: str | None = None

    for hostname in selected_hostnames:
        is_self = hostname == local_hostname or (target_machine is None and any(
            p.get("_is_self") and p.get("DNSName", "").rstrip(".") == hostname
            for p in peers
        ))

        if is_self:
            machine_id = hostname.split(".")[0] if "." in hostname else hostname
            machines_config[machine_id] = {"display": machine_id.capitalize(), "type": "local"}
            local_machine_id = machine_id
            continue

        machine_id = hostname.split(".")[0]
        existing_cfg = existing.get("machines", {}).get(machine_id, {})

        ssh_user = prompt(
            f"\n{hostname}\n  SSH user: ",
            default=existing_cfg.get("user", ""),
        ).strip()
        display_name = prompt(
            f"  Display name: ",
            default=existing_cfg.get("display", machine_id.capitalize()),
        ).strip()

        machines_config[machine_id] = {
            "display": display_name,
            "host": hostname,
            "user": ssh_user,
        }

    # Write machines.json
    port = existing.get("hook_port", HOOK_DEFAULT_PORT)
    new_config = {"hook_port": port, "machines": machines_config}
    atomic_write_json(machines_file, new_config)
    print(f"\nWrote {machines_file}\n")

    # --- Per-machine setup ---
    hook_url = f"http://{local_hostname}:{port}/hook" if local_hostname else ""
    results: list[MachineSetupResult] = []

    for machine_id, cfg in machines_config.items():
        if cfg.get("type") == "local":
            print(f"[{machine_id}] Installing local hook...", end=" ", flush=True)
            ok = _install_hook_local()
            r = MachineSetupResult(machine_id=machine_id, success=ok)
            if not ok:
                r.errors.append("Hook install failed")
            print("✓" if ok else "✗")
            results.append(r)
            continue

        host = cfg["host"]
        user = cfg["user"]
        r = MachineSetupResult(machine_id=machine_id, success=True)

        print(f"[{machine_id}] Checking SSH...", end=" ", flush=True)
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

        if hook_url:
            print(f"[{machine_id}] Installing hook (--remote {hook_url})...", end=" ", flush=True)
            if not _install_hook_remote(user, host, hook_url):
                r.success = False
                r.errors.append("Hook install failed on remote")
                print("✗")
            else:
                print("✓")

            print(f"[{machine_id}] Verifying endpoint reachable...", end=" ", flush=True)
            if not _check_endpoint_reachable(user, host, f"http://{local_hostname}:{port}"):
                r.errors.append(f"Endpoint {hook_url} not reachable from remote")
                print("✗ (warning)")
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
    sys.exit(0 if all_ok else 1)
```

**Step 3: Update `main.py` to dispatch `ccbot setup`**

```python
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from .hook import hook_main
        hook_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from .setup_cmd import setup_main
        import argparse
        p = argparse.ArgumentParser(prog="ccbot setup")
        p.add_argument("--machine", default=None, help="Target a single machine by hostname")
        args = p.parse_args(sys.argv[2:])
        setup_main(target_machine=args.machine)
        return

    # ... rest unchanged
```

**Step 4: Run linting + type check**

```bash
uv run ruff check src/ && uv run pyright src/ccbot/setup_cmd.py src/ccbot/main.py
```
Expected: 0 errors.

**Step 5: Commit**

```bash
git add src/ccbot/setup_cmd.py src/ccbot/main.py pyproject.toml uv.lock
git commit -m "feat: add ccbot setup command with Tailscale TUI and per-machine provisioning"
```

---

## Task 13: End-to-end smoke test (manual)

**Goal:** Verify the full flow works with a real remote machine.

**Prerequisites:**
- Tailscale running on MacBook and Fedora
- SSH key auth working: `ssh-copy-id user@fedora.tail.ts.net`
- ccbot bot token configured

**Steps:**

```bash
# 1. Run setup
ccbot setup
# Select MacBook (local) + Fedora in TUI
# Verify: ✓ for both

# 2. Check machines.json was written
cat ~/.ccbot/machines.json

# 3. Start the bot
./scripts/restart.sh

# 4. In Telegram: open a new topic in your forum
# Expected: machine picker appears with [MacBook] [Fedora]

# 5. Select Fedora → navigate to a project dir → select "Skip permissions ⚡"
# Expected: topic renamed to "[Fedora] project-name ⚡"
# Expected: claude starts in a new tmux window on Fedora

# 6. Send a message in the topic
# Expected: message delivered to Claude on Fedora, response appears in Telegram

# 7. On Fedora: kill the tmux window
# Expected: topic shows as stale / next poll detects window gone

# 8. Run lint + type check one final time
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/ccbot/
```

---

## Implementation Order

Tasks 1–4 can be done without touching existing behavior (additive only).
Task 5 is the most disruptive (changes `thread_bindings` type) — do it in one focused session.
Tasks 6–8 layer on after Task 5.
Tasks 9–11 are UI changes.
Task 12 is standalone.
