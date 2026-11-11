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
    # EOS defaults to L2/switch mode - BGP (and any L3 routing) never
    # actually runs without this, even though the router bgp block below
    # applies cleanly as text with no error. Confirmed live: interactively
    # re-entering "router bgp 65011" after this project's own automated
    # config apply failed outright with "% IP routing not enabled".
    assert "ip routing" in output
    assert "router bgp 65011" in output
    assert "10.0.0.33/31" in output
    assert "neighbor 10.0.0.32 remote-as 65001" in output
    assert "neighbor 10.0.0.42 remote-as 65002" in output
    # cEOS's own boot process syncs Management0's empty EOS-side config
    # down to the underlying kernel interface, wiping whatever address
    # Docker/containerlab assigned it - confirmed live: eth0 came up with
    # no inet address at all, before and after switching the ZTP script
    # from a config replace to a merge-style copy, since neither ever
    # mentioned Management0 at all. The rendered config now explicitly
    # states it, sourced from NetBox's own mgmt_ip (see the Container
    # Mgmt prefix), so it survives regardless of ZTP's own DHCP cleanup
    # on that interface.
    assert "interface Management0" in output
    assert "ip address 172.20.20.11/24" in output
    # users defaults to None -> [] (main.py only passes a real list when
    # the secrets repo's users.yaml is actually mounted in) - no admin
    # user or service account should be rendered at all when it's empty.
    assert "ssh-key" not in output
    assert "netautobot" not in output


def test_render_ceos_leaf_config_with_ssh_key_user():
    data = load_valid()
    users = [{"username": "netops", "role": "admin", "auth": "ssh_key", "value": "ssh-rsa AAAAtest"}]
    output = render_config("leaf-01-us-east-01a", data, users=users)
    assert "username netops privilege 15 role network-admin nopassword" in output
    # `ssh-key` (hyphenated) is the real EOS keyword - `sshkey` is a
    # deprecated alias that the boot-time startup-config parser used by
    # mgmt-switch-us-east-01a (a non-ZTP'd device) rejects outright,
    # confirmed live: "% Unavailable command (This command is deprecated
    # by 'username USERNAME ssh-key')", which silently dropped the whole
    # username block, not just that one line.
    assert "username netops ssh-key ssh-rsa AAAAtest" in output
    assert "netautobot" not in output


def test_render_ceos_leaf_config_with_password_user():
    data = load_valid()
    users = [{"username": "netautobot", "role": "admin", "auth": "password", "value": "fake-password"}]
    output = render_config("leaf-01-us-east-01a", data, users=users)
    # `secret` (not `ssh-key`/`nopassword`) is the real EOS keyword for a
    # password-based local user - confirmed live: EOS accepts the
    # plaintext value here and stores its own sha512 hash internally.
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
    # containerlab's topology-level endpoint convention (e1-N) is NOT a
    # valid sr_cli interface name - confirmed live: "Failed to parse
    # value 'e1-1' for key 'name'". Every interface reference in the
    # rendered config must use the real ethernet-1/N form instead.
    assert "interface ethernet-1/2" in output
    assert "interface e1-2" not in output
    # An interface's admin-state, and separately its subinterface's own
    # ipv4 admin-state, both default to disable - confirmed live neither
    # was enabled by this template before, and an address alone never
    # reached the kernel without both. A subinterface must also be
    # explicitly bound into a network-instance for the same reason.
    assert "interface ethernet-1/2 admin-state enable" in output
    assert "interface ethernet-1/2 subinterface 0 ipv4 admin-state enable" in output
    # BGP itself, and separately its "underlay" peer-group, each need an
    # address-family (afi-safi ipv4-unicast) explicitly enabled, or the
    # whole commit is rejected outright - confirmed live: "Error in
    # .../bgp/admin-state: One of the address families must be enabled."
    # Every neighbor also requires a peer-group - confirmed live:
    # "Mandatory field 'peer-group' is not present" - SRLinux's BGP model
    # doesn't allow configuring a neighbor directly the way cEOS/SRLinux's
    # own simpler models do.
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
    # Same three-grant AAA requirement as every user role - superuser
    # alone isn't enough for actual CLI access, confirmed live.
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
    # leaf-01 has overlay_vlan_id/overlay_vni/overlay_test_ip set in the
    # fixture. Every assertion here was confirmed live directly on a
    # running leaf-01 before being written - including the "vlan 10 / rd
    # .../route-target .../redistribute learned" block, which turned out
    # to be mandatory: the Vxlan1 interface's own vlan-to-vni mapping
    # alone never generated a real EVPN route on its own.
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
    # leaf-03/04/05 stay plain cEOS leafs with no overlay fields in this
    # phase (only leaf-01/leaf-02 are part of the single-VLAN proof) -
    # none of the VXLAN/EVPN block should render for them.
    data = load_valid()
    output = render_config("leaf-03-us-east-01a", data)
    assert "Vxlan1" not in output
    assert "address-family evpn" not in output


def test_render_srlinux_spine_config_includes_evpn_relay():
    # Spines get the EVPN relay addition unconditionally - they have no
    # overlay_vni field at all (no VTEP of their own in this design), but
    # always need to be ready to relay whenever any leaf does have one.
    # next-hop-self false (not EOS's "next-hop-unchanged" - confirmed
    # live these are genuinely different keywords on the two platforms)
    # is what stops this spine from rewriting a relayed route's next-hop
    # to itself, which would otherwise point the receiving leaf at the
    # spine's own loopback instead of the real originating VTEP.
    data = load_valid()
    output = render_config("spine-01-us-east-01a", data)
    assert "set / network-instance default protocols bgp afi-safi evpn admin-state enable" in output
    assert (
        "set / network-instance default protocols bgp group underlay afi-safi evpn admin-state enable"
        in output
    )
    assert "set / network-instance default protocols bgp group underlay next-hop-self false" in output


def test_render_frr_leaf_config():
    # leaf-02 converted from ceos to frr for this phase (see the design
    # spec - FRR is dramatically lighter than cEOS/SRLinux, and proves
    # cross-vendor EVPN interop). Confirmed live: FRR auto-nests
    # "redistribute connected" under "address-family ipv4 unicast" even
    # when written at the top level of "router bgp" (unlike EOS, which
    # keeps it top-level) - written here already correctly nested to
    # match FRR's own idiom, not relying on FRR's auto-correction.
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
    # A hypothetical FRR leaf with no overlay_vni assigned shouldn't get
    # an l2vpn evpn address-family at all.
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
    # Physical topology confirmed live on kp00srvr03: DSR (spine) is the
    # ONLY device with a fabric link to every other device in the site
    # (ESR-01, ESR-02, MRR, BBR each only connect to DSR, never to each
    # other or to MRR directly) - so DSR is the only device that CAN
    # reflect iBGP routes between them. Before this fix, DSR rendered no
    # route-reflector-client anywhere and no l2vpn evpn address-family at
    # all (it has no overlay_vni/l3vni of its own), so - confirmed live
    # via `show ip route bgp` on esr-01-us-central-01a - ESR-01 never
    # learned ESR-02's, MRR's, or BBR's loopback at all, let alone any
    # EVPN route: standard iBGP split-horizon with no RR blocks it.
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
    # DSR isn't a VTEP itself (no overlay_vni/l3vni) - it must not claim
    # to originate any VNI.
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
    # Confirmed live: `vtysh -f` loading two separate "interface eth3 ...
    # exit" blocks for the same interface does NOT merge them - the
    # second one silently wins and the isis lines never take effect
    # ("show isis neighbor" came back empty, no adjacency, on the real
    # deployed ring). Only one "interface eth3" stanza must ever render.
    assert rendered.count("interface eth3") == 1
    # Confirmed live: with only eth3/eth4 IS-IS-enabled, adjacencies
    # formed fine (hellos only need the enabled interface) but LSPs
    # never carried this device's own loopback (10.1.1.14/32) at all -
    # "show ip route isis" on a neighbor came back completely empty,
    # since ztp_frr.py.j2's dummy "lo-vtep" interface (holding the
    # loopback) was never itself enabled for IS-IS. "isis passive"
    # includes its connected prefix in LSPs without attempting to form
    # an adjacency over it (which could never happen on a loopback
    # anyway).
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
                # srv6_locator set here (in addition to sr_index) so this
                # fixture actually exercises the bottom-of-file top-level
                # "segment-routing" (zebra/pathd) stanza, which is gated
                # on `srv6_locator or sr_policies` - without it, that
                # stanza doesn't render at all in this fixture and the
                # count check below would be vacuously checking 0 == 1.
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
    # Must land inside router isis, not as a second top-level
    # "segment-routing" stanza - confirmed live this project's own
    # history, two separate top-level segment-routing blocks crash
    # pathd. There's already exactly one legitimate top-level
    # "segment-routing" stanza (the srv6-locator one, bottom of file,
    # triggered by srv6_locator above) - this must not add a second.
    assert rendered.count("\nsegment-routing\n") == 1


def test_ring_bbr_renders_segment_routing_prefix_sid_when_sr_index_is_zero():
    # sr_index=0 is a legitimate value (label 16000 in FRR's default
    # SRGB) - every other layer of this feature (NetBox push, NetBox
    # export, schema validation) correctly uses `is not None` for this
    # exact reason. A truthiness check on sr_index would silently drop
    # the segment-routing lines for this device.
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
    # "lo-vtep", not plain "lo" - this project's own established
    # dummy-interface convention (ztp_frr.py.j2) for where a device's
    # loopback IP actually lives; a literal "lo" would source from
    # 127.0.0.1 instead, which IS-IS never advertises across the ring.
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
    # Review Focus: the existing backbone_peers EVPN lines must still be
    # present alongside the hold-down lines, not replaced by them. Real
    # Type-5/backbone advertisement of the hold-down block is a separate,
    # still-open follow-up (needs an L3VNI, which nothing in this lab
    # has) - not asserted here.
    assert "neighbor 10.1.3.1 activate" in rendered
    assert "neighbor 10.1.3.1 route-map BACKBONE-TYPE5-OUT out" in rendered
    assert "advertise-all-vni" in rendered


def test_bbr_without_hold_down_block_has_no_hold_down_lines():
    # Review Focus: the 4 already-deployed ring BBRs (backbone_peers set,
    # no hold_down_block) must render zero hold-down lines - no
    # regression from this task.
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

    # The template comment mentions "ip router isis 1", so that's the first
    # split occurrence. The actual "router isis 1" block is the second.
    isis_block = rendered.split("router isis 1")[2].split("!")[0]
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
    # Confirmed live deploying: this device has BOTH srv6_locator
    # AND sr_policies set (the real bbr-01-us-central-01a case) - two
    # SEPARATE top-level "segment-routing" stanzas (one for srv6/zebra,
    # one for traffic-eng/pathd) crash pathd outright ("read returned
    # EOF") every time `vtysh -f` applies the combined file, even though
    # each half applies fine individually via interactive vtysh -c
    # commands. srv6 and traffic-eng must be siblings under ONE top-level
    # "segment-routing" stanza. A standalone "segment-routing" line (the
    # stanza opener, not the isisd sub-command "segment-routing srv6"
    # inside router isis 1, which is a different, legitimate context)
    # must appear exactly once.
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


# --- ladder backbone: iBGP AS 65000, R3/R8 route reflectors, DCI --------

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
    # The fabric BBR re-advertises eBGP-learned fabric prefixes (next hop
    # = the DSR's /31, unknown to IS-IS) into the backbone.
    config = render_config("bbr-01-us-central-01a", _ladder_model())
    v4 = _section(config, "address-family ipv4 unicast")
    assert "neighbor 192.168.1.3 next-hop-self" in v4
    assert "route-reflector-client" not in config


def test_ebgp_fabric_session_gets_explicit_policy_both_directions():
    # FRR enforces ebgp-requires-policy (confirmed live: every eBGP
    # session showed "(Policy)" and dropped all routes without one).
    for dev, peer in (("bbr-01-us-central-01a", "10.1.1.37"), ("dsr-01-us-central-01a", "10.1.1.36")):
        config = render_config(dev, _ladder_model())
        assert "route-map EBGP-PERMIT permit 10" in config
        for af in ("address-family ipv4 unicast", "address-family l2vpn evpn"):
            section = config[config.index(af):]
            assert f"neighbor {peer} route-map EBGP-PERMIT in" in section
            assert f"neighbor {peer} route-map EBGP-PERMIT out" in section


def test_dsr_only_reflects_to_ibgp_fabric_neighbors():
    # route-reflector-client is invalid on an eBGP neighbor - the BBR
    # (AS 65000) is now eBGP from the DSR (AS 65100).
    config = render_config("dsr-01-us-central-01a", _ladder_model())
    assert "neighbor 10.1.1.32 route-reflector-client" in config
    assert "neighbor 10.1.1.36 route-reflector-client" not in config


def test_l3vni_vrf_imports_the_l3vni_from_any_asn():
    # Auto-derived EVPN route-targets are <own ASN>:<VNI>, which differ
    # between the two fabrics (65100 vs 65200) - DCI needs a wildcard
    # import (syntax verified live on FRR 8.4 and 10.5.1).
    for dev in ("esr-01-us-central-01a", "bbr-01-us-central-01a"):
        config = render_config(dev, _ladder_model())
        vrf = config[config.index("vrf DCI"):]
        assert "route-target import *:50000" in vrf


def test_l3vni_is_bound_to_its_vrf_so_zebra_treats_it_as_l3():
    # Confirmed live 2026-10-04: without "vrf DCI / vni 50000", zebra
    # listed VNI 50000 as Type L2 - only Type-3 IMET routes existed, no
    # Type-5 at all, so DCI carried nothing. With it: Type L3, Type-5
    # routes crossed the backbone and the two fabrics' hosts pinged.
    for dev in ("esr-01-us-central-01a", "bbr-01-us-central-01a"):
        config = render_config(dev, _ladder_model())
        assert "vrf DCI\n vni 50000\nexit-vrf" in config
        assert config.index("exit-vrf") < config.index("router bgp 65")
