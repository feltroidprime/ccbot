"""Tests for ccbot setup command helper functions."""
import json
from ccbot.setup_cmd import (
    _load_existing_machines,
    _detect_github_url,
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

def test_detect_github_url_returns_string():
    # Just verify it returns a string (may be empty in test env)
    result = _detect_github_url()
    assert isinstance(result, str)
