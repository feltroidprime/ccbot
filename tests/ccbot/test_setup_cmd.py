"""Tests for ccbot setup command helper functions."""

import json
from ccbot.setup_cmd import (
    _load_existing_machines,
    GITHUB_REPO,
    MachineSetupResult,
    HOOK_DEFAULT_PORT,
)


def test_load_existing_machines_missing_file(tmp_path):
    result = _load_existing_machines(tmp_path / "nonexistent.json")
    assert result["hook_port"] == HOOK_DEFAULT_PORT
    assert result["machines"] == {}


def test_load_existing_machines_valid_file(tmp_path):
    data = {"hook_port": 9090, "machines": {"macbook": {"type": "local"}}}
    f = tmp_path / "machines.json"
    f.write_text(json.dumps(data))
    result = _load_existing_machines(f)
    assert result["hook_port"] == 9090
    assert "macbook" in result["machines"]


def test_load_existing_machines_corrupt_file_returns_default(tmp_path):
    f = tmp_path / "machines.json"
    f.write_text("NOT JSON")
    result = _load_existing_machines(f)
    assert result["machines"] == {}


def test_machine_setup_result_success():
    r = MachineSetupResult(machine_id="fedora", success=True)
    assert r.success is True
    assert r.errors == []


def test_machine_setup_result_failure_with_errors():
    r = MachineSetupResult(machine_id="asus", success=False, errors=["SSH failed"])
    assert r.success is False
    assert "SSH failed" in r.errors


def test_github_repo_is_string():
    assert isinstance(GITHUB_REPO, str)
    assert GITHUB_REPO.startswith("https://github.com/")
