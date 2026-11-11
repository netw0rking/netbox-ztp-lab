import json
from pathlib import Path

import pytest

from schema.validate import validate_data_model

FIXTURES = Path(__file__).parent / "fixtures"


def test_us_central_01a_fixture_is_schema_valid():
    with open(FIXTURES / "backbone_fabric_us_central_01a.json") as f:
        data = json.load(f)

    errors = validate_data_model(data)

    assert errors == []


def test_us_central_01a_fixture_has_expected_devices():
    with open(FIXTURES / "backbone_fabric_us_central_01a.json") as f:
        data = json.load(f)

    assert set(data["devices"].keys()) == {
        "bbr-01-us-central-01a",
        "dsr-01-us-central-01a",
        "esr-01-us-central-01a",
        "esr-02-us-central-01a",
        "mrr-01-us-central-01a",
        "host-01-us-central-01a",
        "mgmt-switch-us-central-01a",
        "ztp-server-us-central-01a",
    }
    assert data["devices"]["bbr-01-us-central-01a"]["role"] == "bbr"
    assert data["devices"]["mrr-01-us-central-01a"]["role"] == "mrr"


def test_us_west_01a_fixture_is_schema_valid():
    with open(FIXTURES / "backbone_fabric_us_west_01a.json") as f:
        data = json.load(f)

    errors = validate_data_model(data)

    assert errors == []


def test_us_west_01a_fixture_has_expected_devices():
    with open(FIXTURES / "backbone_fabric_us_west_01a.json") as f:
        data = json.load(f)

    assert set(data["devices"].keys()) == {
        "bbr-01-us-west-01a",
        "dsr-01-us-west-01a",
        "esr-01-us-west-01a",
        "esr-02-us-west-01a",
        "mrr-01-us-west-01a",
        "host-01-us-west-01a",
        "mgmt-switch-us-west-01a",
        "ztp-server-us-west-01a",
    }
    assert data["devices"]["bbr-01-us-west-01a"]["site_asn"] == 65200


ALL_FIXTURES = sorted(p.name for p in FIXTURES.glob("backbone_fabric_*.json"))
POPS = {1: "us-central-02a", 2: "us-east-02a", 3: "us-north-02a", 4: "us-west-02a",
        5: "us-west-04a", 6: "us-central-03a", 7: "us-east-03a", 8: "us-south-02a",
        9: "us-west-03a", 10: "us-west-05a"}


def _combined():
    devices = {}
    for fname in ALL_FIXTURES:
        with open(FIXTURES / fname) as f:
            devices.update(json.load(f)["devices"])
    return devices


def test_fixture_set_is_two_fabrics_plus_ten_pops():
    assert len(ALL_FIXTURES) == 12
    assert "backbone_fabric_us_south_01a.json" not in ALL_FIXTURES
    assert "backbone_fabric_us_north_01a.json" not in ALL_FIXTURES


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_every_frr_device_has_a_serial_number(fixture_name):
    # Bootstrappable devices need a serial_number.
    with open(FIXTURES / fixture_name) as f:
        data = json.load(f)
    missing = [n for n, d in data["devices"].items() if d.get("platform") == "frr" and not d.get("serial_number")]
    assert missing == []


@pytest.mark.parametrize("n,pop", sorted(POPS.items()))
def test_each_pop_has_one_bbr_its_own_ztp_server_and_mgmt_switch(n, pop):
    with open(FIXTURES / f"backbone_fabric_{pop.replace('-', '_')}.json") as f:
        data = json.load(f)
    assert validate_data_model(data) == []
    assert set(data["devices"]) == {f"bbr-{n:02d}-{pop}", f"ztp-server-{pop}", f"mgmt-switch-{pop}"}
    bbr = data["devices"][f"bbr-{n:02d}-{pop}"]
    assert bbr["loopback"] == f"192.168.1.{n}/32"
    # per-site ZTP: server on the last usable address of the POP's /29
    assert data["devices"][f"ztp-server-{pop}"]["interfaces"]["eth1"]["ip"] == f"10.200.{n}.6/29"


def test_ladder_wiring_matches_the_drawing():
    # 13 ladder links and 4 fabric uplinks, cabled both ends.
    devices = _combined()
    num = {f"bbr-{n:02d}-{p}": n for n, p in POPS.items()}
    num["bbr-01-us-central-01a"], num["bbr-01-us-west-01a"] = 11, 12
    links = set()
    for name, dev in devices.items():
        for if_name, intf in dev.get("interfaces", {}).items():
            if intf.get("role") != "backbone":
                continue
            peer = intf["connects_to"]
            back = devices[peer["device"]]["interfaces"][peer["interface"]]["connects_to"]
            assert (back["device"], back["interface"]) == (name, if_name)
            a, b = sorted((num[name], num[peer["device"]]))
            assert intf["ip"] == f"10.{100 + a}.{b}.{num[name]}/24"
            assert "peer_ip" not in peer and "peer_asn" not in peer
            links.add((a, b))
    assert links == {(1, 2), (2, 3), (3, 4), (4, 5), (6, 7), (7, 8), (8, 9), (9, 10),
                     (1, 6), (2, 7), (3, 8), (4, 9), (5, 10), (1, 11), (6, 11), (5, 12), (10, 12)}


def test_all_bbrs_ibgp_65000_with_bbr03_and_bbr08_as_route_reflectors():
    devices = _combined()
    bbrs = {n: d for n, d in devices.items() if d["role"] == "bbr"}
    assert len(bbrs) == 12
    assert all(d["bgp_asn"] == 65000 for d in bbrs.values())
    rrs = {n for n, d in bbrs.items() if d.get("route_reflector")}
    assert rrs == {"bbr-03-us-north-02a", "bbr-08-us-south-02a"}
    rr_los = {"192.168.1.3", "192.168.1.8"}
    for name, dev in bbrs.items():
        peer_los = {p["loopback"] for p in dev["backbone_peers"]}
        assert all(p["asn"] == 65000 for p in dev["backbone_peers"])
        if name in rrs:
            assert len(peer_los) == 11  # every other BBR, incl. the other RR
        else:
            assert peer_los == rr_los


def test_dci_vrf_on_every_esr_and_fabric_bbr():
    devices = _combined()
    for site, octet in (("us-central-01a", 1), ("us-west-01a", 2)):
        for dev in (f"esr-01-{site}", f"esr-02-{site}", f"bbr-01-{site}"):
            assert devices[dev]["l3vni"] == 50000
            assert devices[dev]["vrf_name"] == "DCI"
            assert "overlay_vni" not in devices[dev]
        assert devices[f"host-01-{site}"]["default_gw"] == f"10.1.{octet}.64"
        assert devices[f"esr-01-{site}"]["interfaces"]["eth3"]["role"] == "access"


def test_every_bbr_has_a_unique_sr_index_across_all_fixtures():
    seen = {}
    for fixture_path in sorted(FIXTURES.glob("backbone_fabric_*.json")):
        with open(fixture_path) as f:
            data = json.load(f)
        for name, dev in data["devices"].items():
            if dev.get("role") != "bbr":
                continue
            assert "sr_index" in dev, f"{name} ({fixture_path.name}) has no sr_index"
            idx = dev["sr_index"]
            assert idx not in seen, (
                f"sr_index {idx} used by both {seen.get(idx)} and {name}"
            )
            seen[idx] = name
