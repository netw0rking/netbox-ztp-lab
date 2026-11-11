import json
from unittest.mock import MagicMock

from netbox.reconcile_backbone_fabric_lab import plan_actions


def _nb_with(device_cf, iface_ips):
    nb = MagicMock()
    nb.dcim.sites.get.return_value = None  # no retired site present
    nbdev = MagicMock(id=7, custom_fields=device_cf)
    nbdev.name = "esr-01-us-central-01a"
    nb.dcim.devices.get.return_value = nbdev
    nb.dcim.interfaces.get.side_effect = lambda device_id, name: MagicMock(id=hash(name), name=name, cable=None)
    def ips(interface_id):
        return [MagicMock(address=a) for a in iface_ips.get(interface_id, [])]
    nb.ipam.ip_addresses.filter.side_effect = ips
    return nb


def _fixture(tmp_path, dev):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"site": "us-central-01a", "devices": {"esr-01-us-central-01a": dev}}))
    return {"us-central-01a": {"fixture": path}}


def test_plan_clears_custom_fields_the_fixture_no_longer_sets(tmp_path):
    # The builder only creates/updates - an ESR moved from the L2 overlay
    # to the routed DCI VRF would otherwise keep exporting overlay_vni.
    dev = {"role": "leaf", "bgp_asn": 65100, "l3vni": 50000, "interfaces": {}}
    nb = _nb_with({"bgp_asn": 65100, "l3vni": 50000, "overlay_vni": 10010, "srv6_locator": None}, {})
    actions = plan_actions(nb, _fixture(tmp_path, dev), retired_sites=[])
    assert [(a[0], a[2]) for a in actions] == [("clear-field", "overlay_vni")]


def test_plan_deletes_ips_on_fixture_interfaces_that_the_fixture_no_longer_has(tmp_path):
    # e.g. a fabric BBR's eth3 moving from the ring (10.1.0.1/31) to R1
    # (10.101.11.11/24) - exporter peer_ip lookups break with two IPs.
    dev = {"role": "bbr", "interfaces": {"eth3": {"role": "backbone", "ip": "10.101.11.11/24"}}}
    nb = _nb_with({}, {hash("eth3"): ["10.1.0.1/31", "10.101.11.11/24"]})
    actions = plan_actions(nb, _fixture(tmp_path, dev), retired_sites=[])
    assert [(a[0], a[1].address) for a in actions] == [("delete-ip", "10.1.0.1/31")]


def test_plan_deletes_dangling_or_mismatched_cables_on_fixture_interfaces(tmp_path):
    # Found live 2026-10-04: deleting the retired transit BBRs left their
    # cables one-ended on the fabric BBRs' eth3/eth4 (NetBox keeps a cable
    # when one end's device is deleted). build_cables skips any interface
    # that already has a cable, so the new R1/R6 uplinks were never made.
    dev = {"role": "bbr", "interfaces": {
        "eth3": {"role": "backbone", "connects_to": {"device": "bbr-01-us-central-02a", "interface": "eth4"}},
        "eth4": {"role": "backbone", "connects_to": {"device": "bbr-06-us-central-03a", "interface": "eth4"}},
        "eth2": {"role": "fabric", "connects_to": {"device": "dsr-01-us-central-01a", "interface": "eth3"}},
    }}
    nb = _nb_with({}, {})
    good_peer = MagicMock(device=MagicMock())
    good_peer.name, good_peer.device.name = "eth3", "dsr-01-us-central-01a"
    cables = {"eth3": (MagicMock(id=45), []),            # dangling
              "eth4": (MagicMock(id=48), [MagicMock()]),  # wrong far end
              "eth2": (MagicMock(id=9), [good_peer])}     # correct - keep
    def iface(device_id, name):
        cable, peers = cables[name]
        return MagicMock(id=hash(name), cable=cable, link_peers=peers)
    nb.dcim.interfaces.get.side_effect = iface
    nb.dcim.cables.get.side_effect = lambda cid: MagicMock(id=cid)
    actions = plan_actions(nb, _fixture(tmp_path, dev), retired_sites=[])
    assert sorted(a[1].id for a in actions if a[0] == "delete-cable") == [45, 48]
