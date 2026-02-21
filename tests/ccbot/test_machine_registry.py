"""Tests for MachineRegistry — loading machine configurations from machines.json."""

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
        },
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


def test_registry_display_name(registry_json):
    reg = MachineRegistry(registry_json)
    assert reg.display_name("macbook") == "MacBook"
    assert reg.display_name("fedora") == "Fedora"


def test_registry_get_unknown_falls_back_to_local(registry_json):
    reg = MachineRegistry(registry_json)
    m = reg.get("nonexistent")
    assert m.machine_id == "macbook"  # local machine


def test_registry_skips_remote_with_missing_host(tmp_path):
    data = {
        "machines": {
            "macbook": {"display": "MacBook", "type": "local"},
            "broken": {"display": "Broken"},  # no host or user
        }
    }
    f = tmp_path / "machines.json"
    f.write_text(json.dumps(data))
    reg = MachineRegistry(f)
    ids = [m.machine_id for m in reg.all()]
    assert "macbook" in ids
    assert "broken" not in ids
