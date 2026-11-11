import json
from pathlib import Path

import pytest
import yaml

import generate_topology as gt_module
from generate_topology import (
    build_links,
    clab_endpoint_name,
    clab_node_name,
    generate_topology,
    split_devices_by_lab,
    write_dnsmasq_configs,
    write_identity_files,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_valid():
    with open(FIXTURES / "valid_data_model.json") as f:
        return json.load(f)


def test_build_links_has_no_duplicates_and_correct_count():
    data = load_valid()
    links = build_links(data["devices"])
    # 10 OOB, 14 fabric and 4 host access links.
    assert len(links) == 28
    endpoints_seen = set()
    for link in links:
        pair = frozenset(link["endpoints"])
        assert pair not in endpoints_seen
        endpoints_seen.add(pair)


def test_clab_endpoint_name_translates_ceos_but_not_other_platforms():
    # cEOS: EthernetN is mapped to containerlab's ethN.
    assert clab_endpoint_name("Ethernet1", "ceos") == "eth1"
    assert clab_endpoint_name("Ethernet24", "ceos") == "eth24"
    # SRLinux e1-x names pass through unchanged.
    assert clab_endpoint_name("e1-2", "srlinux") == "e1-2"
    # "server" role (linux platform) interfaces are already generic ethN.
    assert clab_endpoint_name("eth1", "linux") == "eth1"


def test_clab_node_name_passes_through_short_names_unchanged():
    assert clab_node_name("leaf-01-us-east-01a", "leaf") == "leaf-01-us-east-01a"


def test_clab_node_name_passes_through_short_mgmt_switch_names_unchanged():
    # Shortened only when the name exceeds IFNAMSIZ (15 chars).
    assert clab_node_name("mgmt-sw-a", "mgmt-switch") == "mgmt-sw-a"


def test_clab_node_name_shortens_long_mgmt_switch_names():
    # ovs-bridge node name is the Linux interface name (15 chars max).
    name = clab_node_name("mgmt-switch-us-east-01a", "mgmt-switch")
    assert len(name) <= 15


def test_clab_node_name_is_deterministic():
    assert clab_node_name("mgmt-switch-us-east-01a", "mgmt-switch") == clab_node_name(
        "mgmt-switch-us-east-01a", "mgmt-switch"
    )


def test_clab_node_name_does_not_collide_across_similar_site_names():
    # Plain truncation would collide for same-prefix site names.
    a = clab_node_name("mgmt-switch-us-east-01a", "mgmt-switch")
    b = clab_node_name("mgmt-switch-us-west-01b", "mgmt-switch")
    assert a != b


def test_clab_endpoint_name_hashes_mgmt_switch_ports_uniquely():
    # Same port names on two switches must get distinct endpoints.
    a = clab_endpoint_name("Ethernet2", None, "mgmt-switch", "mgmt-switch-us-central-01a")
    b = clab_endpoint_name("Ethernet2", None, "mgmt-switch", "mgmt-switch-us-north-01a")
    assert a != b
    # Deterministic: same device+port always hashes to the same name.
    assert a == clab_endpoint_name("Ethernet2", None, "mgmt-switch", "mgmt-switch-us-central-01a")
    # Linux IFNAMSIZ limit (15 characters).
    assert len(a) <= 15
    assert len(b) <= 15


def test_build_links_translates_ceos_endpoints_to_ethn():
    data = load_valid()
    links = build_links(data["devices"])
    endpoints = {ep for link in links for ep in link["endpoints"]}

    # cEOS endpoints use ethN on both ends.
    ceos_devices = {"leaf-01-us-east-01a"}
    for ep in endpoints:
        dev_name, if_name = ep.split(":", 1)
        if dev_name in ceos_devices:
            assert if_name.startswith("eth"), f"{ep} should use ethN, not the real interface name"
            assert not if_name.startswith("Ethernet"), f"{ep} still uses the untranslated EOS name"
    # mgmt-switch EthernetN names are hashed, never used raw.
    mgmt_switch_node = clab_node_name("mgmt-switch-us-east-01a", "mgmt-switch")
    expected_mgmt_ep = clab_endpoint_name("Ethernet4", None, "mgmt-switch", "mgmt-switch-us-east-01a")
    assert f"{mgmt_switch_node}:{expected_mgmt_ep}" in endpoints
    assert f"{mgmt_switch_node}:Ethernet4" not in endpoints

    # SRLinux's e1-x endpoints are untouched.
    assert "spine-01-us-east-01a:e1-1" in endpoints
    assert "spine-01-us-east-01a:e1-2" in endpoints


def test_build_links_never_collides_two_mgmt_switches_with_same_port_names(tmp_path):
    # Two switches' same-named ports must not collide in the root namespace.
    data = {
        "devices": {
            "mgmt-switch-a": {
                "role": "mgmt-switch",
                "interfaces": {
                    "Ethernet1": {"connects_to": {"device": "host-a", "interface": "eth1"}},
                    "Ethernet2": {"connects_to": {"device": "host-b", "interface": "eth1"}},
                },
            },
            "mgmt-switch-b": {
                "role": "mgmt-switch",
                "interfaces": {
                    "Ethernet1": {"connects_to": {"device": "host-c", "interface": "eth1"}},
                    "Ethernet2": {"connects_to": {"device": "host-d", "interface": "eth1"}},
                },
            },
            "host-a": {"role": "host", "interfaces": {"eth1": {"connects_to": {"device": "mgmt-switch-a", "interface": "Ethernet1"}}}},
            "host-b": {"role": "host", "interfaces": {"eth1": {"connects_to": {"device": "mgmt-switch-a", "interface": "Ethernet2"}}}},
            "host-c": {"role": "host", "interfaces": {"eth1": {"connects_to": {"device": "mgmt-switch-b", "interface": "Ethernet1"}}}},
            "host-d": {"role": "host", "interfaces": {"eth1": {"connects_to": {"device": "mgmt-switch-b", "interface": "Ethernet2"}}}},
        }
    }

    links = build_links(data["devices"])
    # Collision is on the bare interface name, not the full endpoint.
    mgmt_switch_if_names = [
        ep.split(":", 1)[1]
        for link in links
        for ep in link["endpoints"]
        if ep.startswith("mgmt-switch-")
    ]

    assert len(mgmt_switch_if_names) == len(set(mgmt_switch_if_names)), (
        f"mgmt-switch port names collide in the shared root namespace: {mgmt_switch_if_names}"
    )


def test_write_identity_files_creates_ceos_and_srlinux_files(tmp_path):
    data = load_valid()
    binds, srlinux_exec_cmds = write_identity_files(data["devices"], tmp_path)

    assert "leaf-01-us-east-01a" in binds
    ceos_file = tmp_path / "leaf-01-us-east-01a" / "ceos-config"
    assert ceos_file.exists()
    assert "SERIALNUMBER=CEOS-LEAF1-0001" in ceos_file.read_text()
    assert binds["leaf-01-us-east-01a"] == [f"{ceos_file.resolve()}:/mnt/flash/ceos-config"]
    # cEOS uses its own DHCP-based ZTP, not exec.
    assert "leaf-01-us-east-01a" not in srlinux_exec_cmds

    assert "spine-01-us-east-01a" in binds
    srl_file = tmp_path / "spine-01-us-east-01a" / "chassis_id_suffix"
    assert srl_file.exists()
    assert srl_file.read_text().strip() == "SRL-SPINE1-0001"

    # ztpd does not run in containerlab, so bootstrap is exec'd.
    bootstrap_file = tmp_path / "spine-01-us-east-01a" / "ztp_bootstrap.py"
    assert bootstrap_file.exists()
    bootstrap_content = bootstrap_file.read_text()
    assert "chassis_serial_number" in bootstrap_content
    # Static OOB address and sr_cli interface name are baked in.
    assert 'oob_iface_cli = "ethernet-1/1"' in bootstrap_content
    assert "10.0.0.131/25" in bootstrap_content
    assert 'oob_iface_cli = "e1-1"' not in bootstrap_content
    assert binds["spine-01-us-east-01a"] == [
        f"{srl_file.resolve()}:/etc/opt/srlinux/ztp/chassis_id_suffix",
        f"{bootstrap_file.resolve()}:/tmp/ztp_bootstrap.py",
    ]
    assert srlinux_exec_cmds["spine-01-us-east-01a"] == [
        "/opt/srlinux/python/virtual-env/bin/python3 /tmp/ztp_bootstrap.py"
    ]

    # mgmt-switch and ztp-server aren't ZTP'd, no identity file expected
    assert "mgmt-switch-us-east-01a" not in binds
    assert "ztp-server-us-east-01a" not in binds


def test_write_identity_files_widens_srlinux_fetch_retry_budget(tmp_path):
    # 180 attempts (30 min) so slow per-site servers are waited for.
    data = load_valid()
    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "spine-01-us-east-01a" / "ztp_bootstrap.py").read_text()
    assert "range(180)" in bootstrap_content
    assert "after 180 attempts" in bootstrap_content
    assert "range(60)" not in bootstrap_content


def test_write_identity_files_creates_frr_bootstrap(tmp_path):
    # FRR has no identity-bind file; serial is baked in.
    data = load_valid()
    binds, exec_cmds = write_identity_files(data["devices"], tmp_path)

    assert "leaf-02-us-east-01a" in binds
    bootstrap_file = tmp_path / "leaf-02-us-east-01a" / "ztp_bootstrap.py"
    assert bootstrap_file.exists()
    bootstrap_content = bootstrap_file.read_text()
    assert 'serial = "FRR-LEAF2-0001"' in bootstrap_content
    # No DHCP client in the FRR image; static OOB address.
    assert 'addr("10.0.0.133/25", "eth1")' in bootstrap_content
    # leaf-02 fetches ZTP config from its static OOB IP.
    assert "http://10.0.0.130/ztp/config/" in bootstrap_content
    # Overlay fields (VXLAN/bridge netdev setup) baked in too.
    assert '"10.99.99.2/24"' in bootstrap_content
    assert "vxlan10010" in bootstrap_content
    assert "br10" in bootstrap_content

    # Host-facing eth4 is bridged into br10.
    assert "eth4" in bootstrap_content

    assert binds["leaf-02-us-east-01a"] == [
        f"{bootstrap_file.resolve()}:/tmp/ztp_bootstrap.py",
        f"{gt_module.FRR_DAEMONS_HOST_PATH.resolve()}:/etc/frr/daemons",
    ]
    # Detached (setsid) so it does not block node creation.
    assert exec_cmds["leaf-02-us-east-01a"] == [
        "sh -c 'setsid python3 /tmp/ztp_bootstrap.py > /tmp/ztp_bootstrap.log 2>&1 &'"
    ]


def test_write_identity_files_widens_frr_fetch_retry_budget(tmp_path):
    # Same longer retry budget as SRLinux, for FRR.
    data = load_valid()
    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "leaf-02-us-east-01a" / "ztp_bootstrap.py").read_text()
    assert "range(180)" in bootstrap_content
    assert "after 180 attempts" in bootstrap_content
    assert "range(60)" not in bootstrap_content


def test_write_identity_files_binds_bbr_specific_daemons_file(tmp_path):
    # Ring BBRs need isisd enabled whenever IS-IS is rendered.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "serial_number": "FRR-BBR1-CENTRAL-0001",
                "net": "49.0000.0000.0001.00",
                "interfaces": {},
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    assert binds["bbr-01-us-central-01a"] == [
        f"{(tmp_path / 'bbr-01-us-central-01a' / 'ztp_bootstrap.py').resolve()}:/tmp/ztp_bootstrap.py",
        f"{gt_module.FRR_DAEMONS_BBR_HOST_PATH.resolve()}:/etc/frr/daemons",
    ]


def test_write_identity_files_non_bbr_frr_role_keeps_plain_daemons_file(tmp_path):
    data = load_valid()

    binds, _ = write_identity_files(data["devices"], tmp_path)

    assert f"{gt_module.FRR_DAEMONS_HOST_PATH.resolve()}:/etc/frr/daemons" in binds["leaf-02-us-east-01a"]
    assert f"{gt_module.FRR_DAEMONS_BBR_HOST_PATH.resolve()}:/etc/frr/daemons" not in binds["leaf-02-us-east-01a"]


def test_write_identity_files_assigns_ip_to_backbone_role_interface(tmp_path):
    # Backbone-role interfaces must get IP addresses.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "serial_number": "FRR-BBR1-CENTRAL-0001",
                "net": "49.0000.0000.0001.00",
                "interfaces": {
                    "eth3": {
                        "role": "backbone",
                        "ip": "10.1.0.1/31",
                        "connects_to": {"device": "bbr-01-us-south-01a", "interface": "eth1"},
                    }
                },
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "bbr-01-us-central-01a" / "ztp_bootstrap.py").read_text()
    assert 'addr("10.1.0.1/31", "eth3")' in bootstrap_content


def test_write_identity_files_derives_server_host_from_last_usable_oob_ip(tmp_path):
    # Other sites resolve server_host to their own OOB subnet.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "serial_number": "FRR-BBR1-CENTRAL-0001",
                "net": "49.0000.0000.0001.00",
                "interfaces": {
                    "eth1": {"role": "oob", "ip": "10.1.1.133/25"},
                },
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "bbr-01-us-central-01a" / "ztp_bootstrap.py").read_text()
    assert 'url = "http://10.1.1.254/ztp/config/' in bootstrap_content


def test_write_identity_files_derives_server_host_on_a_slash_30_oob_subnet(tmp_path):
    # Tightest subnet: a /30 with 2 usable addresses.
    data = {
        "devices": {
            "bbr-01-us-south-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65300,
                "loopback": "10.1.3.1/32",
                "serial_number": "FRR-BBR1-SOUTH-0001",
                "net": "49.0000.0000.0003.00",
                "interfaces": {
                    "eth3": {"role": "oob", "ip": "10.1.3.5/30"},
                },
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "bbr-01-us-south-01a" / "ztp_bootstrap.py").read_text()
    assert 'url = "http://10.1.3.6/ztp/config/' in bootstrap_content


def test_write_identity_files_us_east_01a_oob_device_still_uses_ztp_server_host(tmp_path):
    # us-east-01a keeps the constant ZTP_SERVER_HOST.
    data = {
        "devices": {
            "leaf-02-us-east-01a": {
                "role": "leaf",
                "platform": "frr",
                "bgp_asn": 65001,
                "loopback": "10.0.0.6/32",
                "serial_number": "FRR-LEAF2-0001",
                "interfaces": {
                    "eth1": {"role": "oob", "ip": "10.0.0.133/25"},
                },
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "leaf-02-us-east-01a" / "ztp_bootstrap.py").read_text()
    assert 'url = "http://10.0.0.130/ztp/config/' in bootstrap_content


def test_write_identity_files_no_oob_interface_still_falls_back_to_container_name(tmp_path):
    # Fallback when a device has no OOB interface.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "serial_number": "FRR-BBR1-CENTRAL-0001",
                "net": "49.0000.0000.0001.00",
                "interfaces": {},
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "bbr-01-us-central-01a" / "ztp_bootstrap.py").read_text()
    assert 'url = "http://ztp-server-us-east-01a/ztp/config/' in bootstrap_content


def test_write_identity_files_with_sr_policies_adds_pathd_recovery_block(tmp_path):
    # Restart pathd and apply traffic-eng via individual vtysh -c.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "serial_number": "FRR-BBR1-CENTRAL-0001",
                "net": "49.0000.0000.0001.00",
                "srv6_locator": "fc00:100::/48",
                "sr_policies": [
                    {
                        "color": 100,
                        "endpoint": "fc00:200::1",
                        "name": "via-south",
                        "segments": [{"index": 10, "nai_prefix": "fc00:300::/48"}],
                    }
                ],
                "interfaces": {},
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "bbr-01-us-central-01a" / "ztp_bootstrap.py").read_text()
    assert "/var/run/frr/pathd.pid" in bootstrap_content
    assert '["su", "-s", "/bin/sh", "frr", "-c", "/usr/lib/frr/pathd -d -F traditional -A 127.0.0.1"]' in bootstrap_content
    assert '"segment-list via-south-segments"' in bootstrap_content
    assert '"index 10 nai prefix fc00:300::/48 algorithm 0"' in bootstrap_content
    assert '"policy color 100 endpoint fc00:200::1"' in bootstrap_content
    assert '"candidate-path preference 100 name via-south explicit segment-list via-south-segments"' in bootstrap_content
    # One vtysh invocation with -c flags, not a -f load.
    assert 'vtysh_cmd = ["vtysh"]' in bootstrap_content


def test_write_identity_files_without_sr_policies_omits_pathd_recovery_block(tmp_path):
    # Only BBRs with sr_policies get this block.
    data = {
        "devices": {
            "bbr-01-us-west-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65200,
                "loopback": "10.1.2.14/32",
                "serial_number": "FRR-BBR1-WEST-0001",
                "net": "49.0000.0000.0002.00",
                "srv6_locator": "fc00:200::/48",
                "interfaces": {},
            }
        }
    }

    binds, _ = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "bbr-01-us-west-01a" / "ztp_bootstrap.py").read_text()
    assert "/var/run/frr/pathd.pid" not in bootstrap_content
    assert "vtysh_cmd" not in bootstrap_content


def test_write_identity_files_creates_l3vni_frr_bootstrap(tmp_path):
    # leaf-06/07 use routed EVPN Type-5 with a P2P host.
    data = load_valid()
    binds, exec_cmds = write_identity_files(data["devices"], tmp_path)

    bootstrap_content = (tmp_path / "leaf-06-us-east-01a" / "ztp_bootstrap.py").read_text()
    assert 'serial = "FRR-LEAF6-0001"' in bootstrap_content
    assert '"VRF-HOST", "type", "vrf"' in bootstrap_content
    assert "vxlan10100" in bootstrap_content
    assert "br-VRF-HOST" in bootstrap_content
    # eth4 is addressed and enslaved into the VRF.
    assert 'addr("10.0.0.64/31", "eth4")' in bootstrap_content
    assert '"eth4", "master", "VRF-HOST"' in bootstrap_content


def test_write_identity_files_frr_falls_back_to_container_mgmt_hostname_when_no_oob_interface(
    tmp_path,
):
    # No OOB interfaces here; fall back to the container hostname.
    devices = {
        "dsr-01-us-central-01a": {
            "role": "dsr",
            "platform": "frr",
            "serial_number": "FRR-DSR1-0001",
            "loopback": "10.1.1.1/32",
            "interfaces": {
                "eth1": {
                    "role": "fabric",
                    "ip": "10.1.2.1/31",
                    "connects_to": {"device": "peer", "interface": "eth1"},
                },
            },
        },
    }
    binds, exec_cmds = write_identity_files(devices, tmp_path)

    bootstrap_file = tmp_path / "dsr-01-us-central-01a" / "ztp_bootstrap.py"
    assert bootstrap_file.exists()
    bootstrap_content = bootstrap_file.read_text()
    assert "http://ztp-server-us-east-01a/ztp/config/" in bootstrap_content
    assert "http://10.0.0.130/ztp/config/" not in bootstrap_content


def test_generate_topology_writes_valid_clab_yaml(tmp_path):
    data = load_valid()
    output_path = tmp_path / "clos-fabric.clab.yml"
    identity_dir = tmp_path / "identity"

    topology = generate_topology(data, output_path, identity_dir)

    assert output_path.exists()
    parsed = yaml.safe_load(output_path.read_text())
    assert parsed == topology
    # Empty prefix so container names match device names.
    assert parsed["prefix"] == ""
    expected_node_names = {
        clab_node_name(name, dev.get("role")) for name, dev in data["devices"].items()
    }
    assert set(parsed["topology"]["nodes"].keys()) == expected_node_names
    assert parsed["topology"]["nodes"]["leaf-01-us-east-01a"]["kind"] == "arista_ceos"
    assert parsed["topology"]["nodes"]["spine-01-us-east-01a"]["kind"] == "nokia_srlinux"
    assert "binds" in parsed["topology"]["nodes"]["leaf-01-us-east-01a"]

    # mgmt-switch is a native ovs-bridge node.
    mgmt_switch_node_name = clab_node_name("mgmt-switch-us-east-01a", "mgmt-switch")
    mgmt_switch = parsed["topology"]["nodes"][mgmt_switch_node_name]
    assert mgmt_switch["kind"] == "ovs-bridge"
    assert "image" not in mgmt_switch
    assert "startup-config" not in mgmt_switch
    assert "binds" not in mgmt_switch
    assert "exec" not in mgmt_switch
    assert "startup-config" not in parsed["topology"]["nodes"]["leaf-01-us-east-01a"]
    assert "startup-config" not in parsed["topology"]["nodes"]["ztp-server-us-east-01a"]

    # Identity binds are likewise absolute for the same reason.
    leaf_bind_src = parsed["topology"]["nodes"]["leaf-01-us-east-01a"]["binds"][0].split(":")[0]
    assert Path(leaf_bind_src).is_absolute()

    # ZTP'd cEOS devices suppress the default startup-config.
    assert parsed["topology"]["nodes"]["leaf-01-us-east-01a"]["suppress-startup-config"] is True
    assert "suppress-startup-config" not in parsed["topology"]["nodes"]["spine-01-us-east-01a"]

    # ztp-server needs an explicit exec for its static OOB IP.
    assert parsed["topology"]["nodes"]["ztp-server-us-east-01a"]["exec"] == [
        "ip addr add 10.0.0.130/25 dev eth1"
    ]
    assert "exec" not in parsed["topology"]["nodes"]["leaf-01-us-east-01a"]

    # Mgmt network addresses are pinned to NetBox's mgmt_ip.
    assert parsed["mgmt"] == {"network": "clab", "ipv4-subnet": "172.20.20.0/24"}
    assert parsed["topology"]["nodes"]["leaf-01-us-east-01a"]["mgmt-ipv4"] == "172.20.20.11"
    assert parsed["topology"]["nodes"]["spine-01-us-east-01a"]["mgmt-ipv4"] == "172.20.20.31"


def test_generate_topology_binds_server_role_secrets_when_present(tmp_path, monkeypatch):
    # SECRETS_REPO_PATH is mocked so the test is deterministic.
    secrets_repo = tmp_path / "secrets-repo"
    deploy_dir = secrets_repo / "deploy"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "ztp_server_deploy_key").write_text("fake-deploy-key")
    (deploy_dir / "known_hosts").write_text("fake-known-hosts")
    (secrets_repo / "users.yaml").write_text("users: []\n")
    monkeypatch.setattr(gt_module, "SECRETS_REPO_PATH", secrets_repo)

    data = load_valid()
    topology = generate_topology(
        data, tmp_path / "out" / "clos-fabric.clab.yml", tmp_path / "identity"
    )

    server_binds = topology["topology"]["nodes"]["ztp-server-us-east-01a"]["binds"]
    assert any(b.endswith(":/root/.ssh_deploy_key:ro") for b in server_binds)
    assert any(b.endswith(":/root/.ssh_known_hosts:ro") for b in server_binds)
    assert any(b.endswith(":/root/secrets/users.yaml:ro") for b in server_binds)


def test_generate_topology_omits_server_binds_when_secrets_absent(tmp_path, monkeypatch):
    # Optional secrets files must not be bound unconditionally.
    secrets_repo = tmp_path / "secrets-repo"
    secrets_repo.mkdir(parents=True)
    monkeypatch.setattr(gt_module, "SECRETS_REPO_PATH", secrets_repo)

    data = load_valid()
    topology = generate_topology(
        data, tmp_path / "out" / "clos-fabric.clab.yml", tmp_path / "identity"
    )

    # Only the per-site dnsmasq config is bound.
    server_binds = topology["topology"]["nodes"]["ztp-server-us-east-01a"]["binds"]
    assert [b for b in server_binds if not b.endswith(":/etc/dnsmasq.site.conf:ro")] == []


def test_host_role_gets_generic_image_and_ip_exec(tmp_path):
    # host devices set platform explicitly, not linux.
    data = load_valid()
    topology = generate_topology(
        data, tmp_path / "out" / "clos-fabric.clab.yml", tmp_path / "identity"
    )
    nodes = topology["topology"]["nodes"]

    # host-01 (L2-bridged, behind leaf-01) has no gateway to route through.
    host_01 = nodes["host-01-us-east-01a"]
    assert host_01["image"] == gt_module.HOST_IMAGE
    assert host_01["exec"] == ["ip addr add 10.99.99.11/24 dev eth1"]

    # host-06 gets a default route to its leaf's P2P address.
    host_06 = nodes["host-06-us-east-01a"]
    assert host_06["image"] == gt_module.HOST_IMAGE
    assert host_06["exec"] == [
        "ip addr add 10.0.0.65/31 dev eth1",
        "ip route replace default via 10.0.0.64",
    ]


def test_mrr_and_bbr_roles_use_frr_kind_like_leaf_and_spine(tmp_path):
    data = {
        "site": "us-central-01a",
        "devices": {
            "mrr-01-us-central-01a": {"role": "mrr", "platform": "frr", "loopback": "10.1.1.13/32"},
            "bbr-01-us-central-01a": {"role": "bbr", "platform": "frr", "loopback": "10.1.1.14/32"},
        },
    }
    topology = generate_topology(
        data, tmp_path / "out" / "backbone-fabric.clab.yml", tmp_path / "identity"
    )
    nodes = topology["topology"]["nodes"]

    assert nodes["mrr-01-us-central-01a"]["kind"] == "linux"
    assert nodes["mrr-01-us-central-01a"]["image"] == "netbox-ztp-lab-frr:8.4.1"
    assert nodes["bbr-01-us-central-01a"]["kind"] == "linux"
    assert nodes["bbr-01-us-central-01a"]["image"] == "netbox-ztp-lab-frr:8.4.1"


def _backbone_site_devices(extra=None):
    # Per-site ztp-server plus one FRR device on the same OOB subnet.
    devices = {
        "ztp-server-us-central-01a": {
            "role": "server",
            "platform": "linux",
            "interfaces": {"eth1": {"role": "oob", "ip": "10.1.1.254/25"}},
        },
        "bbr-01-us-central-01a": {
            "role": "bbr",
            "platform": "frr",
            "serial_number": "FRR-BBR1-CENTRAL-0001",
            "interfaces": {"eth1": {"role": "oob", "ip": "10.1.1.133/25"}},
        },
    }
    devices.update(extra or {})
    return devices


def test_write_dnsmasq_configs_us_east_01a_keeps_its_existing_dhcp_settings(tmp_path):
    # us-east-01a keeps the static dhcp/dnsmasq.conf output.
    data = load_valid()
    paths = write_dnsmasq_configs(data["devices"], tmp_path)

    conf = paths["ztp-server-us-east-01a"].read_text()
    assert "dhcp-range=10.0.0.140,10.0.0.199,255.255.255.128,12h" in conf
    assert "dhcp-option=option:router,10.0.0.129" in conf
    assert 'dhcp-option=tag:ceos,67,"http://10.0.0.130/ztp/script/ceos"' in conf
    assert 'dhcp-option=tag:srlinux,239,"http://10.0.0.130/ztp/script/srlinux"' in conf


def test_write_dnsmasq_configs_backbone_site_serves_no_dhcp_and_no_us_east_values(tmp_path):
    # Sites without DHCP-ZTP devices get no dhcp-range.
    paths = write_dnsmasq_configs(_backbone_site_devices(), tmp_path)

    conf = paths["ztp-server-us-central-01a"].read_text()
    assert "dhcp-range" not in conf
    assert "10.0.0." not in conf
    assert "interface=eth1" in conf


def test_write_dnsmasq_configs_refuses_to_guess_a_range_for_a_new_dhcp_site(tmp_path):
    # Unmodeled DHCP ranges fail loudly.
    devices = _backbone_site_devices({
        "leaf-01-us-central-01a": {
            "role": "leaf",
            "platform": "ceos",
            "serial_number": "CEOS-X",
            "interfaces": {"Management1": {"role": "oob", "ip": "10.1.1.140/25"}},
        },
    })
    try:
        write_dnsmasq_configs(devices, tmp_path)
    except ValueError as exc:
        assert "ztp-server-us-central-01a" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_generate_topology_binds_per_site_dnsmasq_config(tmp_path):
    data = load_valid()
    topology = generate_topology(
        data, tmp_path / "out" / "clos-fabric.clab.yml", tmp_path / "identity"
    )

    server_binds = topology["topology"]["nodes"]["ztp-server-us-east-01a"]["binds"]
    assert any(b.endswith(":/etc/dnsmasq.site.conf:ro") for b in server_binds)


# One containerlab topology file per lab.

def _combined_export():
    devices = dict(load_valid()["devices"])
    for path in sorted(FIXTURES.glob("backbone_fabric_*.json")):
        with open(path) as f:
            devices.update(json.load(f)["devices"])
    return {"devices": devices}


def test_split_devices_by_lab_separates_us_east_from_backbone_fabric():
    labs = split_devices_by_lab(_combined_export()["devices"])

    assert set(labs) == {"us-east-01a", "backbone-fabric"}
    assert all(n.endswith("-us-east-01a") for n in labs["us-east-01a"])
    assert "bbr-03-us-north-02a" in labs["backbone-fabric"]
    assert not any(n.endswith("-us-east-01a") for n in labs["backbone-fabric"])


def test_split_devices_by_lab_rejects_an_unmapped_site():
    devices = {"leaf-01-eu-west-01a": {"role": "leaf", "interfaces": {}}}
    with pytest.raises(ValueError, match="eu-west-01a"):
        split_devices_by_lab(devices)


def test_split_devices_by_lab_rejects_a_cable_between_labs():
    devices = _combined_export()["devices"]
    devices["bbr-01-us-central-01a"] = json.loads(json.dumps(devices["bbr-01-us-central-01a"]))
    devices["bbr-01-us-central-01a"]["interfaces"]["eth9"] = {
        "role": "backbone",
        "connects_to": {"device": "spine-01-us-east-01a", "interface": "e1-9"},
    }
    with pytest.raises(ValueError, match="spine-01-us-east-01a"):
        split_devices_by_lab(devices)


def test_main_writes_one_topology_file_per_lab(tmp_path, monkeypatch):
    data_path = tmp_path / "export.json"
    data_path.write_text(json.dumps(_combined_export()))
    monkeypatch.chdir(tmp_path)

    assert gt_module.main(["--data", str(data_path)]) == 0

    east = yaml.safe_load((tmp_path / "topology" / "us-east-01a.clab.yml").read_text())
    bf = yaml.safe_load((tmp_path / "topology" / "backbone-fabric.clab.yml").read_text())
    assert east["name"] == "us-east-01a"
    assert bf["name"] == "backbone-fabric"
    assert all(n.startswith("ovsbr-") or n.endswith("-us-east-01a") for n in east["topology"]["nodes"])
    assert not any(n.endswith("-us-east-01a") for n in bf["topology"]["nodes"])
    assert "bbr-01-us-central-01a" in bf["topology"]["nodes"]
    assert not (tmp_path / "topology" / "clos-fabric.clab.yml").exists()


def test_labs_get_separate_mgmt_networks(tmp_path, monkeypatch):
    # Separate network per lab; avoids static IP clashes.
    data_path = tmp_path / "export.json"
    data_path.write_text(json.dumps(_combined_export()))
    monkeypatch.chdir(tmp_path)
    gt_module.main(["--data", str(data_path)])

    east = yaml.safe_load((tmp_path / "topology" / "us-east-01a.clab.yml").read_text())
    bf = yaml.safe_load((tmp_path / "topology" / "backbone-fabric.clab.yml").read_text())
    assert east["mgmt"]["network"] != bf["mgmt"]["network"]
    assert east["mgmt"]["ipv4-subnet"] != bf["mgmt"]["ipv4-subnet"]
    assert bf["mgmt"] == {
        "network": "clab-backbone-fabric",
        "ipv4-subnet": "172.20.21.0/24",
        "ipv6-subnet": "3fff:172:20:21::/64",
    }


def test_frr_nodes_get_the_ssh_enabled_image_for_their_frr_version(tmp_path):
    # FRR nodes use this repo's SSH-enabled image build.
    data = _combined_export()
    labs = split_devices_by_lab(data["devices"])
    topo = generate_topology(
        {"devices": labs["backbone-fabric"]}, tmp_path / "bf.clab.yml", tmp_path / "id", "backbone-fabric"
    )
    nodes = topo["topology"]["nodes"]
    assert nodes["bbr-01-us-central-01a"]["image"] == "netbox-ztp-lab-frr:10.5.1"
    assert nodes["esr-01-us-central-01a"]["image"] == "netbox-ztp-lab-frr:8.4.1"


def test_unknown_frr_base_image_is_rejected(tmp_path):
    data = load_valid()
    data["devices"]["leaf-02-us-east-01a"]["vendor_image"] = "frrouting/frr:v9.9.9"
    with pytest.raises(ValueError, match="frrouting/frr:v9.9.9"):
        generate_topology(data, tmp_path / "e.clab.yml", tmp_path / "id")
