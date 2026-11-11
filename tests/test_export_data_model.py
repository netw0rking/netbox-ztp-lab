from unittest.mock import MagicMock

from netbox.export_data_model import SITE_SLUGS, export_data_model


def test_site_slugs_match_what_the_builders_and_topology_generator_know():
    # Three independent site lists must agree, or a site gets built into
    # NetBox but never exported (or exported but never deployed): this
    # exporter's SITE_SLUGS, build_backbone_fabric_lab.py's SITES (+ the
    # us-east-01a base lab), and generate_topology.py's LAB_SITES.
    from generate_topology import LAB_SITES
    from netbox.build_backbone_fabric_lab import SITES

    assert set(SITE_SLUGS) == {"us-east-01a"} | set(SITES)
    assert set(SITE_SLUGS) == {s for sites in LAB_SITES.values() for s in sites}
    assert "us-south-01a" not in SITE_SLUGS and "us-north-01a" not in SITE_SLUGS


def _fake_device(device_id, name, role_slug, bgp_asn=None, custom_fields=None, site_id=1):
    device = MagicMock()
    device.id = device_id
    device.name = name
    device.role.slug = role_slug
    device.platform = None
    device.custom_fields = {"bgp_asn": bgp_asn, **(custom_fields or {})}
    device.primary_ip4 = None
    device.site.id = site_id
    return device


def test_export_data_model_reads_back_backbone_ring_custom_fields():
    # The bug this test exists to prevent, found live building a
    # 4-site ring: net/backbone_peers/vendor_image had no NetBox custom
    # field at all, so they silently never survived a push/export
    # round-trip (every device came back with them as None) - the
    # whole IS-IS + multihop-EVPN frr_config.j2 work never actually
    # rendered anything on a real deploy, since every `{% if net %}`/
    # `{% if backbone_peers %}` guard was false.
    nb = MagicMock()

    site_a = MagicMock(id=1)
    nb.dcim.sites.get.return_value = site_a

    device = _fake_device(
        101,
        "bbr-01-us-central-01a",
        "bbr",
        bgp_asn=65100,
        custom_fields={
            "net": "49.0000.0000.0001.00",
            "backbone_peers": [{"loopback": "10.1.3.1", "asn": 65300}],
            "vendor_image": "quay.io/frrouting/frr:10.5.1",
            "srv6_locator": "fc00:100::/48",
            "sr_policies": [
                {
                    "color": 100,
                    "endpoint": "fc00:200::1",
                    "name": "via-south",
                    "segments": [{"index": 10, "nai_prefix": "fc00:300::/48"}],
                }
            ],
        },
    )
    nb.dcim.devices.filter.return_value = [device]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []
    nb.ipam.prefixes.filter.return_value = []
    nb.extras.tags.get.return_value = None

    data = export_data_model(nb, ["site-a"])

    entry = data["devices"]["bbr-01-us-central-01a"]
    assert entry["net"] == "49.0000.0000.0001.00"
    assert entry["backbone_peers"] == [{"loopback": "10.1.3.1", "asn": 65300}]
    assert entry["vendor_image"] == "quay.io/frrouting/frr:10.5.1"
    # Found live during deploy: this field had no NetBox custom
    # field at all (an earlier plan note incorrectly claimed NetBox
    # custom fields can't hold list-of-object values - backbone_peers,
    # asserted right above, already disproves that), so the "real,
    # deployable demonstration" SR-Policy could never actually reach a
    # live device via the normal push/export/generate_topology pipeline.
    assert entry["sr_policies"] == [
        {
            "color": 100,
            "endpoint": "fc00:200::1",
            "name": "via-south",
            "segments": [{"index": 10, "nai_prefix": "fc00:300::/48"}],
        }
    ]
    # Found live during review: this field's read-back was
    # missing entirely, reproducing the exact bug class already fixed
    # once for net/backbone_peers/vendor_image (Bug #10) - would have
    # silently blocked the live deploy the same way.
    assert entry["srv6_locator"] == "fc00:100::/48"


def test_export_data_model_merges_devices_from_multiple_sites():
    nb = MagicMock()

    site_a = MagicMock(id=1)
    site_b = MagicMock(id=2)

    def fake_sites_get(slug):
        return {"site-a": site_a, "site-b": site_b}[slug]

    nb.dcim.sites.get.side_effect = fake_sites_get

    device_a = _fake_device(101, "device-a", "spine")
    device_b = _fake_device(201, "device-b", "leaf")

    def fake_devices_filter(site_id):
        return {1: [device_a], 2: [device_b]}[site_id]

    nb.dcim.devices.filter.side_effect = fake_devices_filter

    # No mgmt interface, no cabled interfaces - simplest case.
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []

    data = export_data_model(nb, ["site-a", "site-b"])

    assert "device-a" in data["devices"]
    assert "device-b" in data["devices"]


def test_export_data_model_resolves_cross_site_cable_peer_via_global_device_by_id():
    # The bug this task exists to prevent: if device_by_id were rebuilt
    # per-site, resolving a cable peer that lives in a DIFFERENT site would
    # fail (device_by_id.get(...) returns None -> AttributeError on
    # peer_device.custom_fields). This test cables device-a (site-a) to
    # device-b (site-b) and asserts the peer resolves correctly.
    nb = MagicMock()

    site_a = MagicMock(id=1)
    site_b = MagicMock(id=2)

    def fake_sites_get(slug):
        return {"site-a": site_a, "site-b": site_b}[slug]

    nb.dcim.sites.get.side_effect = fake_sites_get

    device_a = _fake_device(101, "device-a", "spine", bgp_asn=65001)
    device_b = _fake_device(201, "device-b", "leaf", bgp_asn=65002)

    def fake_devices_filter(site_id):
        return {1: [device_a], 2: [device_b]}[site_id]

    nb.dcim.devices.filter.side_effect = fake_devices_filter

    cable = MagicMock(id=99)

    iface_a = MagicMock()
    iface_a.id = 1001
    iface_a.name = "eth1"
    iface_a.cable = cable
    iface_a.device.id = 101

    iface_b = MagicMock()
    iface_b.id = 2001
    iface_b.name = "eth1"
    iface_b.cable = cable
    iface_b.device.id = 201

    def fake_interfaces_filter(device_id):
        return {101: [iface_a], 201: [iface_b]}[device_id]

    nb.dcim.interfaces.filter.side_effect = fake_interfaces_filter

    # a_terminations/b_terminations resolve to the actual terminating
    # objects (Interfaces records), compared by .id against the current
    # iface, per the existing code's own comment.
    term_a = MagicMock(id=1001)
    term_b = MagicMock(id=2001)
    fake_cable = MagicMock(a_terminations=[term_a], b_terminations=[term_b])
    nb.dcim.cables.get.return_value = fake_cable

    # nb.dcim.interfaces.get is called two ways in the real code:
    #   1. get(device_id=nb_device.id, name="mgmt0")   -> mgmt lookup
    #   2. get(other_end.id)                            -> peer iface lookup
    # Use side_effect covering both call shapes.
    other_end_id_a = 2001  # device-a's cable peer termination id
    other_end_id_b = 1001  # device-b's cable peer termination id

    def interfaces_get_side_effect(*args, **kwargs):
        if kwargs.get("name") == "mgmt0":
            return None
        if args and args[0] == other_end_id_a:
            return iface_b
        if args and args[0] == other_end_id_b:
            return iface_a
        return None

    nb.dcim.interfaces.get.side_effect = interfaces_get_side_effect

    # No IPs assigned anywhere - keeps this test focused on peer resolution.
    nb.ipam.ip_addresses.get.return_value = None

    data = export_data_model(nb, ["site-a", "site-b"])

    connects_to = data["devices"]["device-a"]["interfaces"]["eth1"]["connects_to"]
    assert connects_to["device"] == "device-b"


def test_link_to_host_role_device_with_no_ip_is_overlay_not_oob():
    # The bug this test exists to prevent, found live on kp00srvr03:
    # a leaf's host-facing link was exported as role "oob" (the fallback
    # for "not both ends BGP-speaking") instead of "overlay", because the
    # is_fabric check only knows two outcomes. generate_topology.py's FRR
    # branch silently drops any interface whose role matches neither
    # "oob and has ip" nor "fabric" nor "overlay" nor "access" - so this
    # host-facing port was never enslaved into the VXLAN bridge at all,
    # and host-01 couldn't reach anything across the fabric.
    nb = MagicMock()

    site_a = MagicMock(id=1)
    nb.dcim.sites.get.return_value = site_a

    leaf = _fake_device(101, "esr-01-us-central-01a", "leaf", bgp_asn=65100)
    host = _fake_device(201, "host-01-us-central-01a", "host")

    nb.dcim.devices.filter.side_effect = lambda site_id: {1: [leaf, host]}[site_id]

    cable = MagicMock(id=99)

    iface_leaf = MagicMock()
    iface_leaf.id = 1001
    iface_leaf.name = "eth3"
    iface_leaf.cable = cable
    iface_leaf.device.id = 101

    iface_host = MagicMock()
    iface_host.id = 2001
    iface_host.name = "eth1"
    iface_host.cable = cable
    iface_host.device.id = 201

    nb.dcim.interfaces.filter.side_effect = lambda device_id: {
        101: [iface_leaf],
        201: [iface_host],
    }[device_id]

    term_leaf = MagicMock(id=1001)
    term_host = MagicMock(id=2001)
    fake_cable = MagicMock(a_terminations=[term_leaf], b_terminations=[term_host])
    nb.dcim.cables.get.return_value = fake_cable

    def interfaces_get_side_effect(*args, **kwargs):
        if kwargs.get("name") == "mgmt0":
            return None
        if args and args[0] == 2001:
            return iface_host
        if args and args[0] == 1001:
            return iface_leaf
        return None

    nb.dcim.interfaces.get.side_effect = interfaces_get_side_effect
    nb.ipam.ip_addresses.get.return_value = None

    data = export_data_model(nb, ["site-a"])

    leaf_entry = data["devices"]["esr-01-us-central-01a"]["interfaces"]["eth3"]
    assert leaf_entry["role"] == "overlay"


def _cabled_link_data(device_a, device_b, if_name_a="eth1", if_name_b="eth1"):
    nb = MagicMock()

    def fake_devices_filter(site_id):
        return {1: [device_a] if device_a.site.id == 1 else [], 2: [device_b] if device_b.site.id == 2 else []}.get(
            site_id, []
        )

    nb.dcim.sites.get.side_effect = lambda slug: MagicMock(id={"site-a": 1, "site-b": 2}[slug])
    nb.dcim.devices.filter.side_effect = fake_devices_filter

    cable = MagicMock(id=99)
    # MagicMock's own `name` constructor kwarg is reserved for the mock's
    # repr, not a settable attribute - must assign it after construction.
    iface_a = MagicMock(id=1001, cable=cable)
    iface_a.name = if_name_a
    iface_a.device.id = device_a.id
    iface_b = MagicMock(id=2001, cable=cable)
    iface_b.name = if_name_b
    iface_b.device.id = device_b.id

    nb.dcim.interfaces.filter.side_effect = lambda device_id: {
        device_a.id: [iface_a],
        device_b.id: [iface_b],
    }[device_id]

    term_a = MagicMock(id=1001)
    term_b = MagicMock(id=2001)
    fake_cable = MagicMock(a_terminations=[term_a], b_terminations=[term_b])
    nb.dcim.cables.get.return_value = fake_cable

    def interfaces_get_side_effect(*args, **kwargs):
        if kwargs.get("name") == "mgmt0":
            return None
        if args and args[0] == 2001:
            return iface_b
        if args and args[0] == 1001:
            return iface_a
        return None

    nb.dcim.interfaces.get.side_effect = interfaces_get_side_effect
    nb.ipam.ip_addresses.get.return_value = None
    return nb


def test_cross_site_link_between_two_bgp_devices_is_backbone_not_fabric():
    # The bug this test exists to prevent, found live deploying a
    # backbone ring: the old fabric/oob split only checked whether BOTH
    # ends have bgp_asn set - true for two ring BBRs in DIFFERENT sites
    # too, so a ring link was mis-classified "fabric" (with spurious
    # peer_ip/peer_asn) instead of "backbone". generate_topology.py's
    # per-interface IS-IS enablement is gated on role == "backbone" -
    # under "fabric" it silently never rendered, so isisd never formed
    # an adjacency at all ("show isis neighbor" came back empty).
    device_a = _fake_device(101, "bbr-01-us-central-01a", "bbr", bgp_asn=65100, site_id=1)
    device_b = _fake_device(201, "bbr-01-us-south-01a", "bbr", bgp_asn=65300, site_id=2)
    nb = _cabled_link_data(device_a, device_b, if_name_a="eth3", if_name_b="eth1")

    data = export_data_model(nb, ["site-a", "site-b"])

    entry = data["devices"]["bbr-01-us-central-01a"]["interfaces"]["eth3"]
    assert entry["role"] == "backbone"
    assert "peer_ip" not in entry["connects_to"]
    assert "peer_asn" not in entry["connects_to"]


def test_same_site_link_between_two_bgp_devices_is_still_fabric():
    device_a = _fake_device(101, "esr-01-us-central-01a", "leaf", bgp_asn=65100, site_id=1)
    device_b = _fake_device(102, "dsr-01-us-central-01a", "spine", bgp_asn=65100, site_id=1)
    nb = _cabled_link_data(device_a, device_b, if_name_a="eth2", if_name_b="eth1")
    nb.dcim.sites.get.side_effect = lambda slug: MagicMock(id=1)
    nb.dcim.devices.filter.side_effect = lambda site_id: [device_a, device_b]

    data = export_data_model(nb, ["site-a"])

    entry = data["devices"]["esr-01-us-central-01a"]["interfaces"]["eth2"]
    assert entry["role"] == "fabric"
    assert entry["connects_to"]["peer_asn"] == 65100


def test_bbr_device_gets_hold_down_block_from_its_own_sites_tagged_prefix():
    # The Review Focus risk this test exists to pin: a site with a
    # hold-down-tagged Prefix must only ever hand its value to THAT
    # site's own bbr device - a lookup missing scope_id (matching by tag
    # alone, across every site) would leak one site's block onto
    # another's device once two new sites both have one.
    nb = MagicMock()

    site_a = MagicMock(id=1)
    site_b = MagicMock(id=2)
    nb.dcim.sites.get.side_effect = lambda slug: {"site-a": site_a, "site-b": site_b}[slug]

    bbr_a = _fake_device(101, "bbr-01-us-central-02a", "bbr", bgp_asn=65500, site_id=1)
    bbr_b = _fake_device(201, "bbr-01-us-east-02a", "bbr", bgp_asn=65600, site_id=2)
    nb.dcim.devices.filter.side_effect = lambda site_id: {1: [bbr_a], 2: [bbr_b]}[site_id]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []

    hold_down_a = MagicMock(prefix="10.1.20.0/23")
    hold_down_b = MagicMock(prefix="10.1.24.0/23")

    def fake_prefixes_filter(**kwargs):
        if kwargs.get("tag") != "hold-down-block":
            return []
        return {1: [hold_down_a], 2: [hold_down_b]}.get(kwargs.get("scope_id"), [])

    nb.ipam.prefixes.filter.side_effect = fake_prefixes_filter

    data = export_data_model(nb, ["site-a", "site-b"])

    assert data["devices"]["bbr-01-us-central-02a"]["hold_down_block"] == "10.1.20.0/23"
    assert data["devices"]["bbr-01-us-east-02a"]["hold_down_block"] == "10.1.24.0/23"


def test_non_bbr_device_never_gets_hold_down_block():
    # Review Focus: only role == "bbr" ever gets this field, even though
    # a dsr/esr/mrr device in the same new site shares its site.id.
    nb = MagicMock()

    site_a = MagicMock(id=1)
    nb.dcim.sites.get.return_value = site_a

    dsr = _fake_device(101, "dsr-01-us-central-02a", "spine", bgp_asn=65500, site_id=1)
    nb.dcim.devices.filter.return_value = [dsr]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []

    hold_down = MagicMock(prefix="10.1.20.0/23")
    nb.ipam.prefixes.filter.return_value = [hold_down]

    data = export_data_model(nb, ["site-a"])

    assert "hold_down_block" not in data["devices"]["dsr-01-us-central-02a"]


def test_bbr_in_a_site_with_no_tagged_prefix_gets_no_hold_down_block():
    # Existing sites (us-central-01a etc.) - no regression, field stays
    # unset, same as today.
    nb = MagicMock()

    site_a = MagicMock(id=5)
    nb.dcim.sites.get.return_value = site_a

    bbr = _fake_device(101, "bbr-01-us-central-01a", "bbr", bgp_asn=65100, site_id=5)
    nb.dcim.devices.filter.return_value = [bbr]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []
    nb.ipam.prefixes.filter.return_value = []

    data = export_data_model(nb, ["site-a"])

    assert "hold_down_block" not in data["devices"]["bbr-01-us-central-01a"]


def test_bbr_export_skips_prefix_lookup_when_hold_down_tag_does_not_exist_yet():
    # The bug this test exists to prevent, found live: no site currently
    # uses the new addressing scheme (deliberately), so the
    # "hold-down-block" Tag has never actually been created in NetBox.
    # nb.ipam.prefixes.filter(tag="hold-down-block", ...) validates the tag
    # slug against NetBox's own live choices and raises a 400 ("Select a
    # valid choice. hold-down-block is not one of the available choices.")
    # when the tag doesn't exist yet - a real error the earlier mock-only
    # tests couldn't reproduce (they mock the filter call directly, never
    # exercising NetBox's real choice-validation). Confirmed live running
    # netbox.export_data_model against the real instance.
    nb = MagicMock()

    site_a = MagicMock(id=5)
    nb.dcim.sites.get.return_value = site_a

    bbr = _fake_device(101, "bbr-01-us-central-01a", "bbr", bgp_asn=65100, site_id=5)
    nb.dcim.devices.filter.return_value = [bbr]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []

    nb.extras.tags.get.return_value = None

    def unexpected_prefixes_filter(**kwargs):
        raise AssertionError(
            "must not call nb.ipam.prefixes.filter(tag=...) when the tag "
            "object itself doesn't exist yet - real NetBox rejects that "
            "with a 400, it doesn't just return no matches"
        )

    nb.ipam.prefixes.filter.side_effect = unexpected_prefixes_filter

    data = export_data_model(nb, ["site-a"])

    nb.extras.tags.get.assert_called_once_with(slug="hold-down-block")
    assert "hold_down_block" not in data["devices"]["bbr-01-us-central-01a"]


def test_export_reads_back_dci_and_route_reflector_fields():
    # l3vni/vrf_name/default_gw had NetBox custom fields (build side) but
    # were never read back here - the routed EVPN Type-5 path never
    # survived a NetBox round-trip. DCI depends on all of them.
    nb = MagicMock()
    nb.dcim.sites.get.return_value = MagicMock(id=1)
    rr = _fake_device(1, "bbr-03-us-north-02a", "bbr", bgp_asn=65000,
                      custom_fields={"route_reflector": True})
    esr = _fake_device(2, "esr-01-us-central-01a", "leaf", bgp_asn=65100,
                       custom_fields={"l3vni": 50000, "vrf_name": "DCI"})
    host = _fake_device(3, "host-01-us-central-01a", "host",
                        custom_fields={"default_gw": "10.1.1.64"})
    nb.dcim.devices.filter.return_value = [rr, esr, host]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []
    nb.ipam.prefixes.filter.return_value = []
    nb.extras.tags.get.return_value = None

    devices = export_data_model(nb, ["site-a"])["devices"]

    assert devices["bbr-03-us-north-02a"]["route_reflector"] is True
    assert devices["esr-01-us-central-01a"]["l3vni"] == 50000
    assert devices["esr-01-us-central-01a"]["vrf_name"] == "DCI"
    assert devices["host-01-us-central-01a"]["default_gw"] == "10.1.1.64"
    assert "route_reflector" not in devices["esr-01-us-central-01a"]


def test_export_reads_back_sr_index():
    nb = MagicMock()
    nb.dcim.sites.get.return_value = MagicMock(id=1)
    bbr = _fake_device(1, "bbr-01-us-central-01a", "bbr", bgp_asn=65000,
                       custom_fields={"sr_index": 101})
    nb.dcim.devices.filter.return_value = [bbr]
    nb.dcim.interfaces.get.return_value = None
    nb.dcim.interfaces.filter.return_value = []
    nb.ipam.prefixes.filter.return_value = []
    nb.extras.tags.get.return_value = None

    devices = export_data_model(nb, ["site-a"])["devices"]

    assert devices["bbr-01-us-central-01a"]["sr_index"] == 101
