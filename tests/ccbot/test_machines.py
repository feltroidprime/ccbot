"""Tests for machines.py — MachineConnection protocol and LocalMachine implementation."""

import pytest
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
