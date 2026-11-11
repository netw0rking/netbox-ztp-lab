from scripts.ensure_ovs_bridges import ensure_bridges, find_ovs_bridge_names


def test_find_ovs_bridge_names_returns_only_ovs_bridge_kind_nodes():
    topology = {
        "topology": {
            "nodes": {
                "mgmt-switch-us-east-01a": {"kind": "ovs-bridge"},
                "leaf-01-us-east-01a": {"kind": "arista_ceos"},
                "spine-01-us-east-01a": {"kind": "nokia_srlinux"},
            }
        }
    }

    assert find_ovs_bridge_names(topology) == ["mgmt-switch-us-east-01a"]


def test_find_ovs_bridge_names_returns_empty_list_when_none_present():
    topology = {"topology": {"nodes": {"leaf-01-us-east-01a": {"kind": "arista_ceos"}}}}

    assert find_ovs_bridge_names(topology) == []


def test_find_ovs_bridge_names_finds_multiple_across_renamed_sites():
    # Bridge names come from the topology, not hardcoded.
    topology = {
        "topology": {
            "nodes": {
                "mgmt-switch-us-central-01a": {"kind": "ovs-bridge"},
                "mgmt-switch-us-west-01b": {"kind": "ovs-bridge"},
                "leaf-01-us-central-01a": {"kind": "arista_ceos"},
            }
        }
    }

    assert find_ovs_bridge_names(topology) == [
        "mgmt-switch-us-central-01a",
        "mgmt-switch-us-west-01b",
    ]


def test_ensure_bridges_skips_creation_when_bridge_already_exists():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            returncode = 0  # br-exists: 0 means it already exists

        return Result()

    ensure_bridges(["mgmt-switch-us-east-01a"], run=fake_run)

    assert calls == [["sudo", "ovs-vsctl", "br-exists", "mgmt-switch-us-east-01a"]]


def test_ensure_bridges_creates_bridge_when_missing():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            # Exit code 2 means the bridge does not exist.
            returncode = 0 if cmd[2] != "br-exists" else 2

        return Result()

    ensure_bridges(["mgmt-switch-us-east-01a"], run=fake_run)

    assert calls == [
        ["sudo", "ovs-vsctl", "br-exists", "mgmt-switch-us-east-01a"],
        ["sudo", "ovs-vsctl", "add-br", "mgmt-switch-us-east-01a"],
    ]


def test_ensure_bridges_handles_multiple_names_independently():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            # Only the second bridge is missing.
            returncode = 2 if cmd[2] == "br-exists" and cmd[3] == "bridge-b" else 0

        return Result()

    ensure_bridges(["bridge-a", "bridge-b"], run=fake_run)

    assert calls == [
        ["sudo", "ovs-vsctl", "br-exists", "bridge-a"],
        ["sudo", "ovs-vsctl", "br-exists", "bridge-b"],
        ["sudo", "ovs-vsctl", "add-br", "bridge-b"],
    ]
