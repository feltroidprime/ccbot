"""Tests for machines.py — MachineConnection protocol and LocalMachine implementation."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from ccbot.machines import LocalMachine, RemoteMachine


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


@pytest.fixture
def remote():
    m = RemoteMachine(machine_id="fedora", host="fedora.tail.ts.net", user="user")
    return m


@pytest.mark.asyncio
async def test_remote_list_dir_parses_output(remote):
    mock_result = MagicMock()
    mock_result.stdout = "alpha\nbeta\n"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    remote._conn = mock_conn
    result = await remote.list_dir("/home/user/projects")
    assert result == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_remote_list_dir_excludes_empty_lines(remote):
    mock_result = MagicMock()
    mock_result.stdout = "alpha\n\nbeta\n"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    remote._conn = mock_conn
    result = await remote.list_dir("/home/user")
    assert result == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_remote_read_file_from_offset(remote):
    mock_result = MagicMock()
    mock_result.stdout = b"world"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    remote._conn = mock_conn
    result = await remote.read_file_from_offset("/path/file.jsonl", offset=6)
    assert result == b"world"


@pytest.mark.asyncio
async def test_remote_list_windows_parses_format(remote):
    mock_result = MagicMock()
    mock_result.stdout = (
        "@3:myproject:/home/user/projects/foo:claude\n@4:__main__:/home:bash\n"
    )
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)
    remote._conn = mock_conn
    windows = await remote.list_windows()
    assert len(windows) == 1
    assert windows[0].window_id == "@3"
    assert windows[0].window_name == "myproject"


@pytest.mark.asyncio
async def test_remote_list_dir_returns_empty_on_error(remote):
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(side_effect=Exception("SSH error"))
    remote._conn = mock_conn
    result = await remote.list_dir("/some/path")
    assert result == []
