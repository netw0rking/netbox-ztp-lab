import json
from pathlib import Path

from ztp_server.render import render_config

FIXTURES = Path(__file__).parent / "fixtures"


def load_valid():
    with open(FIXTURES / "valid_data_model.json") as f:
        return json.load(f)


def test_render_ceos_leaf_config():
    data = load_valid()
    output = render_config("leaf-01-us-east-01a", data)
    assert "hostname leaf-01-us-east-01a" in output
    # EOS defaults to L2 mode; ip routing must be enabled for BGP.
    assert "ip routing" in output
    assert "router bgp 65011" in output
    assert "10.0.0.33/31" in output
    assert "neighbor 10.0.0.32 remote-as 65001" in output
    assert "neighbor 10.0.0.42 remote-as 65002" in output
    # Management0 address must be set explicitly in the config.
    assert "interface Management0" in output
    assert "ip address 172.20.20.11/24" in output
    # No users rendered when users is None or empty.
    assert "ssh-key" not in output
    assert "netautobot" not in output


def test_render_ceos_leaf_config_with_ssh_key_user():
    data = load_valid()
    users = [{"username": "netops", "role": "admin", "auth": "ssh_key", "value": "ssh-rsa AAAAtest"}]
    output = render_config("leaf-01-us-east-01a", data, users=users)
    assert "username netops privilege 15 role network-admin nopassword" in output
    # Deprecated ssh-key syntax is rejected by EOS.
    assert "username netops ssh-key ssh-rsa AAAAtest" in output
    assert "netautobot" not in output


def test_render_ceos_leaf_config_with_password_user():
    data = load_valid()
    users = [{"username": "netautobot", "role": "admin", "auth": "password", "value": "fake-password"}]
    output = render_config("leaf-01-us-east-01a", data, users=users)
    # EOS password users use the `secret` keyword.
    assert "username netautobot privilege 15 role network-admin secret fake-password" in output
    assert "ssh-key" not in output


def test_render_ceos_leaf_config_with_multiple_users():
    data = load_valid()
    users = [
        {"username": "netops", "role": "admin", "auth": "ssh_key", "value": "ssh-rsa AAAAtest"},
        {"username": "netautobot", "role": "admin", "auth": "password", "value": "fake-password"},
    ]
    output = render_config("leaf-01-us-east-01a", data, users=users)
    assert "username netops ssh-key ssh-rsa AAAAtest" in output
    assert "username netautobot privilege 15 role network-admin secret fake-password" in output


def test_render_srlinux_spine_config():
    data = load_valid()
    output = render_config("spine-01-us-east-01a", data)
    assert "host-name spine-01-us-east-01a" in output
    assert "autonomous-system 65001" in output
    assert "10.0.0.32/31" in output
    assert "neighbor 10.0.0.33 peer-as 65011" in output
    # Rendered interfaces use ethernet-1/N, not containerlab e1-N.
    assert "interface ethernet-1/2" in output
    assert "interface e1-2" not in output
    # Interface and subinterface admin-state must be enabled.
    assert "interface ethernet-1/2 admin-state enable" in output
    assert "interface ethernet-1/2 subinterface 0 ipv4 admin-state enable" in output
    # BGP and the underlay peer-group need an ipv4 address family.
    assert "protocols bgp afi-safi ipv4-unicast admin-state enable" in output
    assert "protocols bgp group underlay afi-safi ipv4-unicast admin-state enable" in output
    assert "neighbor 10.0.0.33 peer-group underlay" in output
    assert "network-instance default interface ethernet-1/2.0" in output
    # system0 (the loopback-equivalent) gets the same treatment.
    assert "interface system0 subinterface 0 ipv4 admin-state enable" in output
    assert "network-instance default interface system0.0" in output
    assert "netautobot" not in output


def test_render_srlinux_spine_config_with_password_user():
    data = load_valid()
    users = [{"username": "netautobot", "role": "admin", "auth": "password", "value": "fake-password"}]
    output = render_config("spine-01-us-east-01a", data, users=users)
    # Same three AAA grants as every user role.
    assert "system aaa authorization role netautobot superuser true" in output
    assert 'system aaa authorization role netautobot cli allow-command-list [ ".*" ]' in output
    assert "system aaa authorization role netautobot services [ cli gnmi ]" in output
    assert "system aaa authentication user netautobot role [ netautobot ]" in output
    assert 'system aaa authentication user netautobot password "fake-password"' in output


def test_render_srlinux_spine_config_with_ssh_key_user():
    data = load_valid()
    users = [{"username": "netops", "role": "admin", "auth": "ssh_key", "value": "ssh-rsa AAAAtest"}]
    output = render_config("spine-01-us-east-01a", data, users=users)
    assert "system aaa authorization role netops superuser true" in output
    assert "system aaa authentication user netops role [ netops ]" in output
    assert 'system aaa authentication user netops ssh-key [ "ssh-rsa AAAAtest" ]' in output


def test_render_unknown_device_raises():
    data = load_valid()
    try:
        render_config("does-not-exist", data)
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_render_ceos_leaf_config_with_overlay_vxlan_evpn():
    # leaf-01 has overlay fields set; checks VLAN and EVPN blocks.
    data = load_valid()
    output = render_config("leaf-01-us-east-01a", data)
    assert "vlan 10" in output
    assert "interface Vxlan1" in output
    assert "vxlan source-interface Loopback0" in output
    assert "vxlan vlan 10 vni 10010" in output
    assert "interface Vlan10" in output
    assert "ip address 10.99.99.1/24" in output
    assert "rd 10.0.0.11:10" in output
    assert "route-target both 10010:10010" in output
    assert "redistribute learned" in output
    assert "address-family evpn" in output
    assert "neighbor 10.0.0.32 activate" in output
    assert "neighbor 10.0.0.42 activate" in output


def test_render_ceos_leaf_config_omits_overlay_when_no_vni():
    # Leafs without overlay fields render no VXLAN/EVPN block.
    data = load_valid()
    output = render_config("leaf-03-us-east-01a", data)
    assert "Vxlan1" not in output
    assert "address-family evpn" not in output


def test_render_srlinux_spine_config_includes_evpn_relay():
    # Spines always relay EVPN; next-hop-self false keeps the VTEP next-hop.
    data = load_valid()
    output = render_config("spine-01-us-east-01a", data)
    assert "set / network-instance default protocols bgp afi-safi evpn admin-state enable" in output
    assert (
        "set / network-instance default protocols bgp group underlay afi-safi evpn admin-state enable"
        in output
    )
    assert "set / network-instance default protocols bgp group underlay next-hop-self false" in output


def test_render_frr_leaf_config():
    # FRR nests "redistribute connected" under the address family.
    data = load_valid()
    output = render_config("leaf-02-us-east-01a", data)
    assert "hostname leaf-02-us-east-01a" in output
    assert "router bgp 65012" in output
    assert "bgp router-id 10.0.0.12" in output
    assert "neighbor 10.0.0.34 remote-as 65001" in output
    assert "neighbor 10.0.0.44 remote-as 65002" in output
    assert "address-family ipv4 unicast" in output
    assert "redistribute connected" in output
    assert "address-family l2vpn evpn" in output
    assert "neighbor 10.0.0.34 activate" in output
    assert "neighbor 10.0.0.44 activate" in output
    assert "advertise-all-vni" in output


def test_render_frr_leaf_config_omits_evpn_without_overlay_vni():
    data = load_valid()
    # FRR leaf without overlay_vni gets no l2vpn evpn family.
    device = data["devices"]["leaf-02-us-east-01a"]
    device.pop("overlay_vni")
    output = render_config("leaf-02-us-east-01a", data)
    assert "address-family l2vpn evpn" not in output


def test_mrr_role_gets_route_reflector_client_on_evpn_neighbors():
    data = {
        "devices": {
            "mrr-01-us-central-01a": {
                "role": "mrr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.13/32",
                "interfaces": {
                    "eth1": {
                        "role": "fabric",
                        "ip": "10.1.1.38/31",
                        "connects_to": {
                            "device": "dsr-01-us-central-01a",
                            "interface": "eth4",
                            "peer_ip": "10.1.1.39/31",
                            "peer_asn": 65100,
                        },
                    }
                },
            }
        }
    }

    rendered = render_config("mrr-01-us-central-01a", data)

    assert "route-reflector-client" in rendered


def test_non_mrr_role_has_no_route_reflector_client():
    data = {
        "devices": {
            "esr-01-us-central-01a": {
                "role": "leaf",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.11/32",
                "interfaces": {},
            }
        }
    }

    rendered = render_config("esr-01-us-central-01a", data)

    assert "route-reflector-client" not in rendered


def _dsr_hub_and_spoke_data():
    # DSR is the only fabric link to other devices, so it must reflect iBGP.
    return {
        "devices": {
            "dsr-01-us-central-01a": {
                "role": "spine",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.1/32",
                "interfaces": {
                    "eth1": {
                        "role": "fabric",
                        "ip": "10.1.1.33/31",
                        "connects_to": {
                            "device": "esr-01-us-central-01a",
                            "interface": "eth2",
                            "peer_ip": "10.1.1.32/31",
                            "peer_asn": 65100,
                        },
                    },
                    "eth2": {
                        "role": "fabric",
                        "ip": "10.1.1.35/31",
                        "connects_to": {
                            "device": "esr-02-us-central-01a",
                            "interface": "eth2",
                            "peer_ip": "10.1.1.34/31",
                            "peer_asn": 65100,
                        },
                    },
                },
            }
        }
    }


def test_spine_role_gets_route_reflector_client_for_underlay_unicast():
    data = _dsr_hub_and_spoke_data()

    rendered = render_config("dsr-01-us-central-01a", data)

    assert "address-family ipv4 unicast" in rendered
    assert "neighbor 10.1.1.32 route-reflector-client" in rendered
    assert "neighbor 10.1.1.34 route-reflector-client" in rendered


def test_spine_role_gets_evpn_route_reflector_even_without_overlay_vni():
    data = _dsr_hub_and_spoke_data()

    rendered = render_config("dsr-01-us-central-01a", data)

    assert "address-family l2vpn evpn" in rendered
    assert "neighbor 10.1.1.32 activate" in rendered
    assert "neighbor 10.1.1.32 route-reflector-client" in rendered
    assert "neighbor 10.1.1.34 activate" in rendered
    assert "neighbor 10.1.1.34 route-reflector-client" in rendered
    # DSR is not a VTEP, so it originates no VNI.
    assert "advertise-all-vni" not in rendered


def test_ring_bbr_renders_isis_block_and_interface_enablement():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
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

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "router isis 1" in rendered
    assert "net 49.0000.0000.0001.00" in rendered
    assert "is-type level-2-only" in rendered
    assert "interface eth3" in rendered
    assert "ip router isis 1" in rendered
    assert "isis network point-to-point" in rendered
    assert "isis circuit-type level-2-only" in rendered
    # Only one "interface eth3" stanza may render.
    assert rendered.count("interface eth3") == 1
    # Loopback must be passive IS-IS so its prefix is advertised.
    assert "interface lo-vtep" in rendered
    assert "isis passive" in rendered


def test_ring_bbr_renders_segment_routing_prefix_sid_when_sr_index_set():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "sr_index": 101,
                # srv6_locator is set so the top-level segment-routing block renders.
                "srv6_locator": "fc00:100::/48",
                "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
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

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "segment-routing on" in rendered
    assert "segment-routing prefix 10.1.1.14/32 index 101" in rendered
    # Must not add a second top-level segment-routing stanza.
    assert rendered.count("\nsegment-routing\n") == 1


def test_ring_bbr_renders_segment_routing_prefix_sid_when_sr_index_is_zero():
    # sr_index=0 is valid; test that it is not dropped.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "sr_index": 0,
                "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
                "interfaces": {},
            }
        }
    }

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "segment-routing prefix" in rendered
    assert "index 0" in rendered


def test_ring_bbr_omits_segment_routing_prefix_sid_without_sr_index():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
                "interfaces": {},
            }
        }
    }

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "segment-routing on" not in rendered
    assert "segment-routing prefix" not in rendered


def test_ring_bbr_renders_multihop_type5_only_evpn_session():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
                "interfaces": {},
            }
        }
    }

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "neighbor 10.1.3.1 remote-as 65300" in rendered
    # IS-IS sources from lo-vtep, not lo.
    assert "neighbor 10.1.3.1 update-source lo-vtep" in rendered
    assert "neighbor 10.1.3.1 ebgp-multihop" in rendered
    assert "route-map BACKBONE-TYPE5-OUT permit 10" in rendered
    assert "match evpn route-type prefix" in rendered
    assert "neighbor 10.1.3.1 activate" in rendered
    assert "neighbor 10.1.3.1 route-map BACKBONE-TYPE5-OUT out" in rendered


def test_non_ring_device_has_no_isis_or_backbone_bgp_config():
    data = {
        "devices": {
            "esr-01-us-central-01a": {
                "role": "leaf",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.11/32",
                "interfaces": {},
            }
        }
    }

    rendered = render_config("esr-01-us-central-01a", data)

    assert "router isis" not in rendered
    assert "BACKBONE-TYPE5-OUT" not in rendered


def _ring_bbr_with_hold_down_data():
    return {
        "devices": {
            "bbr-01-us-central-02a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65500,
                "loopback": "10.1.22.14/32",
                "net": "49.0000.0000.0005.00",
                "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
                "hold_down_block": "10.1.20.0/23",
                "interfaces": {
                    "eth3": {
                        "role": "backbone",
                        "ip": "10.1.0.8/31",
                        "connects_to": {"device": "bbr-01-us-south-01a", "interface": "eth3"},
                    }
                },
            }
        }
    }


def test_bbr_with_hold_down_block_originates_it_into_underlay():
    rendered = render_config("bbr-01-us-central-02a", _ring_bbr_with_hold_down_data())

    assert "ip route 10.1.20.0/23 Null0" in rendered
    assert "redistribute static" in rendered
    # backbone_peers EVPN lines stay alongside hold-down lines.
    assert "neighbor 10.1.3.1 activate" in rendered
    assert "neighbor 10.1.3.1 route-map BACKBONE-TYPE5-OUT out" in rendered
    assert "advertise-all-vni" in rendered


def test_bbr_without_hold_down_block_has_no_hold_down_lines():
    # Ring BBRs without hold_down_block render no hold-down lines.
    data = _ring_bbr_with_hold_down_data()
    del data["devices"]["bbr-01-us-central-02a"]["hold_down_block"]

    rendered = render_config("bbr-01-us-central-02a", data)

    assert "Null0" not in rendered
    assert "redistribute static" not in rendered


def test_bbr_with_srv6_locator_renders_zebra_locator_block():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "srv6_locator": "fc00:100::/48",
                "interfaces": {},
            }
        }
    }

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "segment-routing" in rendered
    assert "srv6" in rendered
    assert "locator default" in rendered
    assert "prefix fc00:100::/48" in rendered


def test_bbr_with_srv6_locator_binds_it_inside_router_isis_block():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "net": "49.0000.0000.0001.00",
                "srv6_locator": "fc00:100::/48",
                "interfaces": {},
            }
        }
    }

    rendered = render_config("bbr-01-us-central-01a", data)

    isis_block = rendered.split("\nrouter isis 1\n")[1].split("!")[0]
    assert "segment-routing srv6" in isis_block
    assert "locator default" in isis_block


def test_device_without_srv6_locator_has_no_segment_routing_block():
    data = {
        "devices": {
            "esr-01-us-central-01a": {
                "role": "leaf",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.11/32",
                "interfaces": {},
            }
        }
    }

    rendered = render_config("esr-01-us-central-01a", data)

    assert "segment-routing" not in rendered


def test_bbr_with_sr_policies_renders_explicit_segment_list_and_policy():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65100,
                "loopback": "10.1.1.14/32",
                "srv6_locator": "fc00:100::/48",
                "interfaces": {},
                "sr_policies": [
                    {
                        "color": 100,
                        "endpoint": "fc00:200::1",
                        "name": "via-south",
                        "segments": [{"index": 10, "nai_prefix": "fc00:300::/48"}],
                    }
                ],
            }
        }
    }

    rendered = render_config("bbr-01-us-central-01a", data)

    assert "segment-list via-south-segments" in rendered
    assert "index 10 nai prefix fc00:300::/48 algorithm 0" in rendered
    assert "policy color 100 endpoint fc00:200::1" in rendered
    assert "name via-south" in rendered
    assert "candidate-path preference 100 name via-south explicit segment-list via-south-segments" in rendered
    # srv6 and traffic-eng must share one top-level segment-routing.
    assert rendered.count("\nsegment-routing\n") == 1


def test_device_without_sr_policies_has_no_traffic_eng_block():
    data = {
        "devices": {
            "bbr-01-us-south-01a": {
                "role": "bbr",
                "platform": "frr",
                "bgp_asn": 65300,
                "loopback": "10.1.3.1/32",
                "srv6_locator": "fc00:300::/48",
                "interfaces": {},
            }
        }
    }

    rendered = render_config("bbr-01-us-south-01a", data)

    assert "traffic-eng" not in rendered


# --- ladder backbone (iBGP AS 65000, DCI) ---

def _ladder_model():
    rr_peers = [{"loopback": f"192.168.1.{n}", "asn": 65000} for n in (1, 2, 4, 5, 6, 7, 8, 9, 10)]
    rr_peers += [{"loopback": "10.1.1.14", "asn": 65000}, {"loopback": "10.1.2.14", "asn": 65000}]
    clients = [{"loopback": "192.168.1.3", "asn": 65000}, {"loopback": "192.168.1.8", "asn": 65000}]
    return {"devices": {
        "bbr-03-us-north-02a": {
            "role": "bbr", "platform": "frr", "bgp_asn": 65000, "loopback": "192.168.1.3/32",
            "net": "49.0000.1921.6800.1003.00", "route_reflector": True, "backbone_peers": rr_peers,
            "interfaces": {"eth2": {"role": "backbone", "ip": "10.102.3.3/24",
                                    "connects_to": {"device": "bbr-02-us-east-02a", "interface": "eth3"}}},
        },
        "bbr-01-us-central-01a": {
            "role": "bbr", "platform": "frr", "bgp_asn": 65000, "loopback": "10.1.1.14/32",
            "net": "49.0000.0000.0001.00", "backbone_peers": clients, "l3vni": 50000, "vrf_name": "DCI",
            "interfaces": {
                "eth2": {"role": "fabric", "ip": "10.1.1.36/31", "connects_to": {
                    "device": "dsr-01-us-central-01a", "interface": "eth3",
                    "peer_ip": "10.1.1.37/31", "peer_asn": 65100}},
                "eth3": {"role": "backbone", "ip": "10.101.11.11/24",
                         "connects_to": {"device": "bbr-01-us-central-02a", "interface": "eth4"}},
            },
        },
        "dsr-01-us-central-01a": {
            "role": "spine", "platform": "frr", "bgp_asn": 65100, "loopback": "10.1.1.1/32",
            "interfaces": {
                "eth1": {"role": "fabric", "ip": "10.1.1.33/31", "connects_to": {
                    "device": "esr-01-us-central-01a", "interface": "eth2",
                    "peer_ip": "10.1.1.32/31", "peer_asn": 65100}},
                "eth3": {"role": "fabric", "ip": "10.1.1.37/31", "connects_to": {
                    "device": "bbr-01-us-central-01a", "interface": "eth2",
                    "peer_ip": "10.1.1.36/31", "peer_asn": 65000}},
            },
        },
        "esr-01-us-central-01a": {
            "role": "leaf", "platform": "frr", "bgp_asn": 65100, "loopback": "10.1.1.11/32",
            "l3vni": 50000, "vrf_name": "DCI",
            "interfaces": {"eth2": {"role": "fabric", "ip": "10.1.1.32/31", "connects_to": {
                "device": "dsr-01-us-central-01a", "interface": "eth1",
                "peer_ip": "10.1.1.33/31", "peer_asn": 65100}}},
        },
    }}


def _section(config, start, end="exit-address-family"):
    i = config.index(start)
    return config[i:config.index(end, i)]


def test_ibgp_backbone_peers_get_no_ebgp_multihop():
    config = render_config("bbr-01-us-central-01a", _ladder_model())
    assert "neighbor 192.168.1.3 remote-as 65000" in config
    assert "neighbor 192.168.1.3 update-source lo-vtep" in config
    assert "ebgp-multihop" not in config


def test_route_reflector_marks_every_backbone_peer_as_client_in_both_afs():
    config = render_config("bbr-03-us-north-02a", _ladder_model())
    v4 = _section(config, "address-family ipv4 unicast")
    evpn = _section(config, "address-family l2vpn evpn")
    for lo in ("192.168.1.1", "192.168.1.8", "10.1.1.14", "10.1.2.14"):
        assert f"neighbor {lo} route-reflector-client" in v4
        assert f"neighbor {lo} route-reflector-client" in evpn


def test_rr_client_sets_next_hop_self_toward_ibgp_backbone_peers():
    # Fabric BBR re-advertises eBGP-learned prefixes into the backbone.
    config = render_config("bbr-01-us-central-01a", _ladder_model())
    v4 = _section(config, "address-family ipv4 unicast")
    assert "neighbor 192.168.1.3 next-hop-self" in v4
    assert "route-reflector-client" not in config


def test_ebgp_fabric_session_gets_explicit_policy_both_directions():
    # FRR needs a route policy on eBGP sessions.
    for dev, peer in (("bbr-01-us-central-01a", "10.1.1.37"), ("dsr-01-us-central-01a", "10.1.1.36")):
        config = render_config(dev, _ladder_model())
        assert "route-map EBGP-PERMIT permit 10" in config
        for af in ("address-family ipv4 unicast", "address-family l2vpn evpn"):
            section = config[config.index(af):]
            assert f"neighbor {peer} route-map EBGP-PERMIT in" in section
            assert f"neighbor {peer} route-map EBGP-PERMIT out" in section


def test_dsr_only_reflects_to_ibgp_fabric_neighbors():
    # route-reflector-client is invalid on an eBGP neighbor.
    config = render_config("dsr-01-us-central-01a", _ladder_model())
    assert "neighbor 10.1.1.32 route-reflector-client" in config
    assert "neighbor 10.1.1.36 route-reflector-client" not in config


def test_l3vni_vrf_imports_the_l3vni_from_any_asn():
    # DCI needs a wildcard route-target import.
    for dev in ("esr-01-us-central-01a", "bbr-01-us-central-01a"):
        config = render_config(dev, _ladder_model())
        vrf = config[config.index("vrf DCI"):]
        assert "route-target import *:50000" in vrf


def test_l3vni_is_bound_to_its_vrf_so_zebra_treats_it_as_l3():
    # Without "vrf DCI / vni 50000" there are no Type-5 routes.
    for dev in ("esr-01-us-central-01a", "bbr-01-us-central-01a"):
        config = render_config(dev, _ladder_model())
        assert "vrf DCI\n vni 50000\nexit-vrf" in config
        assert config.index("exit-vrf") < config.index("router bgp 65")
