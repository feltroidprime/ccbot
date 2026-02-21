"""Unit tests for SessionMonitor JSONL reading and offset handling."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.monitor_state import TrackedSession
from ccbot.session_monitor import SessionMonitor


class TestReadNewLinesOffsetRecovery:
    """Tests for _read_new_lines offset corruption recovery."""

    @pytest.fixture
    def monitor(self, tmp_path):
        """Create a SessionMonitor with temp state file."""
        return SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "monitor_state.json",
        )

    @pytest.mark.asyncio
    async def test_mid_line_offset_recovery(self, monitor, tmp_path, make_jsonl_entry):
        """Recover from corrupted offset pointing mid-line."""
        # Create JSONL file with two valid lines
        jsonl_file = tmp_path / "session.jsonl"
        entry1 = make_jsonl_entry(msg_type="assistant", content="first message")
        entry2 = make_jsonl_entry(msg_type="assistant", content="second message")
        jsonl_file.write_text(
            json.dumps(entry1) + "\n" + json.dumps(entry2) + "\n",
            encoding="utf-8",
        )

        # Calculate offset pointing into the middle of line 1
        line1_bytes = len(json.dumps(entry1).encode("utf-8")) // 2
        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=line1_bytes,  # Mid-line (corrupted)
        )

        # Read should recover and return empty (offset moved to next line)
        result = await monitor._read_new_lines(session, jsonl_file)

        # Should return empty list (recovery skips to next line, no new content yet)
        assert result == []

        # Offset should now point to start of line 2
        line1_full = len(json.dumps(entry1).encode("utf-8")) + 1  # +1 for newline
        assert session.last_byte_offset == line1_full

    @pytest.mark.asyncio
    async def test_valid_offset_reads_normally(
        self, monitor, tmp_path, make_jsonl_entry
    ):
        """Normal reading when offset points to line start."""
        jsonl_file = tmp_path / "session.jsonl"
        entry1 = make_jsonl_entry(msg_type="assistant", content="first")
        entry2 = make_jsonl_entry(msg_type="assistant", content="second")
        jsonl_file.write_text(
            json.dumps(entry1) + "\n" + json.dumps(entry2) + "\n",
            encoding="utf-8",
        )

        # Offset at 0 should read both lines
        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=0,
        )

        result = await monitor._read_new_lines(session, jsonl_file)

        assert len(result) == 2
        assert session.last_byte_offset == jsonl_file.stat().st_size

    @pytest.mark.asyncio
    async def test_truncation_detection(self, monitor, tmp_path, make_jsonl_entry):
        """Detect file truncation and reset offset."""
        jsonl_file = tmp_path / "session.jsonl"
        entry = make_jsonl_entry(msg_type="assistant", content="content")
        jsonl_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        # Set offset beyond file size (simulates truncation)
        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=9999,  # Beyond file size
        )

        result = await monitor._read_new_lines(session, jsonl_file)

        # Should reset offset to 0 and read the line
        assert session.last_byte_offset == jsonl_file.stat().st_size
        assert len(result) == 1


class TestReadNewLinesMachineAbstraction:
    """Tests that _read_new_lines uses machine_registry for file I/O."""

    @pytest.fixture
    def monitor(self, tmp_path):
        return SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "monitor_state.json",
        )

    @pytest.mark.asyncio
    async def test_local_machine_reads_file(self, monitor, tmp_path, make_jsonl_entry):
        """machine_id='local' reads via LocalMachine (same result as before)."""
        jsonl_file = tmp_path / "session.jsonl"
        entry = make_jsonl_entry(msg_type="assistant", content="hello")
        jsonl_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        session = TrackedSession(
            session_id="test-session",
            file_path=str(jsonl_file),
            last_byte_offset=0,
            machine_id="local",
        )

        result = await monitor._read_new_lines(session, jsonl_file)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_remote_machine_uses_machine_registry(
        self, monitor, tmp_path, make_jsonl_entry
    ):
        """machine_id != 'local' reads via machine_registry.get(machine_id)."""
        jsonl_file = tmp_path / "session.jsonl"
        entry = make_jsonl_entry(msg_type="assistant", content="remote msg")
        raw_bytes = (json.dumps(entry) + "\n").encode("utf-8")

        session = TrackedSession(
            session_id="test-remote",
            file_path=str(jsonl_file),
            last_byte_offset=0,
            machine_id="fedora",
        )

        mock_machine = AsyncMock()
        mock_machine.read_file_from_offset = AsyncMock(return_value=raw_bytes)
        mock_machine.file_size = AsyncMock(return_value=len(raw_bytes))

        with patch(
            "ccbot.session_monitor.machine_registry"
        ) as mock_registry:
            mock_registry.get.return_value = mock_machine
            result = await monitor._read_new_lines(session, jsonl_file)

        mock_registry.get.assert_called_with("fedora")
        mock_machine.read_file_from_offset.assert_called()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_truncation_via_machine_file_size(
        self, monitor, tmp_path, make_jsonl_entry
    ):
        """Truncation detection uses machine.file_size(), not stat()."""
        jsonl_file = tmp_path / "session.jsonl"
        entry = make_jsonl_entry(msg_type="assistant", content="msg")
        raw_bytes = (json.dumps(entry) + "\n").encode("utf-8")

        # Offset > actual file size → truncation
        session = TrackedSession(
            session_id="test-trunc",
            file_path=str(jsonl_file),
            last_byte_offset=9999,
            machine_id="fedora",
        )

        mock_machine = AsyncMock()
        # file_size returns small value → truncation detected → reset to 0
        mock_machine.file_size = AsyncMock(return_value=len(raw_bytes))
        mock_machine.read_file_from_offset = AsyncMock(return_value=raw_bytes)

        with patch("ccbot.session_monitor.machine_registry") as mock_registry:
            mock_registry.get.return_value = mock_machine
            result = await monitor._read_new_lines(session, jsonl_file)

        # Truncation detected: offset should have been reset and file re-read
        assert session.last_byte_offset == len(raw_bytes)
        assert len(result) == 1


class TestSessionMapParsing:
    """Tests for _load_current_session_map with local and remote key formats."""

    @pytest.fixture
    def monitor(self, tmp_path):
        return SessionMonitor(
            projects_path=tmp_path / "projects",
            state_file=tmp_path / "monitor_state.json",
        )

    @pytest.mark.asyncio
    async def test_local_key_format(self, monitor, tmp_path, monkeypatch):
        """Keys matching tmux_session_name prefix are parsed as local."""
        from ccbot.config import config

        session_map_file = tmp_path / "session_map.json"
        data = {
            f"{config.tmux_session_name}:@3": {
                "session_id": "local-sess-uuid",
                "cwd": "/home/user/project",
            }
        }
        session_map_file.write_text(json.dumps(data))
        monkeypatch.setattr(config, "session_map_file", session_map_file)

        result = await monitor._load_current_session_map()
        assert result == {"@3": "local-sess-uuid"}

    @pytest.mark.asyncio
    async def test_remote_key_format(self, monitor, tmp_path, monkeypatch):
        """Keys with unknown prefix are parsed as remote machine entries."""
        from ccbot.config import config

        session_map_file = tmp_path / "session_map.json"
        data = {
            "fedora:@5": {
                "session_id": "remote-sess-uuid",
                "cwd": "/home/user/project",
                "machine": "fedora",
            }
        }
        session_map_file.write_text(json.dumps(data))
        monkeypatch.setattr(config, "session_map_file", session_map_file)

        result = await monitor._load_current_session_map()
        # Remote entries are included; window key is "@5"
        assert "@5" in result
        assert result["@5"] == "remote-sess-uuid"

    @pytest.mark.asyncio
    async def test_mixed_local_and_remote(self, monitor, tmp_path, monkeypatch):
        """Both local and remote entries are returned together."""
        from ccbot.config import config

        session_map_file = tmp_path / "session_map.json"
        data = {
            f"{config.tmux_session_name}:@3": {
                "session_id": "local-sess",
                "cwd": "/local/project",
            },
            "fedora:@5": {
                "session_id": "remote-sess",
                "cwd": "/remote/project",
                "machine": "fedora",
            },
        }
        session_map_file.write_text(json.dumps(data))
        monkeypatch.setattr(config, "session_map_file", session_map_file)

        result = await monitor._load_current_session_map()
        assert result.get("@3") == "local-sess"
        assert result.get("@5") == "remote-sess"
