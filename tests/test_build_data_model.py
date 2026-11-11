import json
from unittest.mock import MagicMock

import netbox.build_data_model as bdm
from netbox.build_data_model import build_cables, build_ipam_hierarchy, load_fixture


def test_load_fixture_reads_given_path(tmp_path):
    fixture_path = tmp_path / "custom.json"
    fixture_path.write_text(json.dumps({"site": "test-site", "devices": {}}))

    data = load_fixture(fixture_path)

    assert data["site"] == "test-site"


def test_build_ipam_hierarchy_uses_given_addressing_not_hardcoded(monkeypatch):
    nb = MagicMock()
    created_prefixes = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        if "prefix" in lookup:
            created_prefixes.append(lookup["prefix"])
        result = MagicMock()
        result.prefix = lookup.get("prefix")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=1, slug="us-central-01a")

    addressing = {
        "site_prefix": "10.1.1.0/24",
        "production_prefix": "10.1.1.0/25",
        "loopback_prefix": "10.1.1.0/27",
        "p2p_prefix": "10.1.1.32/27",
    }

    build_ipam_hierarchy(nb, site, addressing)

    assert "10.1.1.0/24" in created_prefixes
    assert "10.1.1.0/27" in created_prefixes
    assert "10.1.1.32/27" in created_prefixes
    # Base lab prefixes must not appear.
    assert "10.0.0.0/24" not in created_prefixes


def test_container_mgmt_prefix_lookup_is_not_scoped_to_site(monkeypatch):
    # Shared across sites, so the lookup is not scoped to site.id.
    nb = MagicMock()
    lookups = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        lookups.append(lookup)
        result = MagicMock()
        result.prefix = lookup.get("prefix")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=5, slug="us-central-01a")

    addressing = {
        "site_prefix": "10.1.1.0/24",
        "production_prefix": "10.1.1.0/25",
        "loopback_prefix": "10.1.1.0/27",
        "p2p_prefix": "10.1.1.32/27",
    }

    build_ipam_hierarchy(nb, site, addressing)

    container_mgmt_lookup = next(
        lookup for lookup in lookups if lookup.get("prefix") == bdm.CONTAINER_MGMT_PREFIX
    )
    assert "scope_type" not in container_mgmt_lookup
    assert "scope_id" not in container_mgmt_lookup


def test_p2p_prefix_lookup_is_not_scoped_to_site(monkeypatch):
    # Shared across sites, so the lookup is not scoped to site.id.
    nb = MagicMock()
    lookups = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        lookups.append(lookup)
        result = MagicMock()
        result.prefix = lookup.get("prefix")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=8, slug="us-north-01a")

    addressing = {
        "site_prefix": "10.1.4.0/30",
        "production_prefix": "10.1.4.0/30",
        "loopback_prefix": "10.1.4.0/30",
        "p2p_prefix": "10.1.0.0/28",
    }

    build_ipam_hierarchy(nb, site, addressing)

    p2p_lookup = next(lookup for lookup in lookups if lookup.get("prefix") == "10.1.0.0/28")
    assert "scope_type" not in p2p_lookup
    assert "scope_id" not in p2p_lookup


def test_site_aggregate_sets_site_aggregate_role_on_site_prefix(monkeypatch):
    # Uses the site-aggregate role; no separate Aggregate object.
    nb = MagicMock()
    prefix_calls = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        result = MagicMock()
        # Distinct ids so a wrong id cannot pass by coincidence.
        if lookup.get("slug") == "site-aggregate":
            result.id = 77
        elif lookup.get("slug") == "production":
            result.id = 1
        elif "slug" in lookup:
            result.id = 2
        else:
            result.id = 1
        if "prefix" in lookup:
            prefix_calls.append((lookup, defaults))
        result.prefix = lookup.get("prefix")
        result.slug = lookup.get("slug")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=9, slug="us-central-02a")

    addressing = {
        "site_prefix": "10.1.20.0/22",
        "site_aggregate": True,
        "production_prefix": "10.1.22.0/24",
        "loopback_prefix": "10.1.22.0/26",
        "p2p_prefix": "10.1.22.64/26",
    }

    build_ipam_hierarchy(nb, site, addressing)

    site_prefix_call = next(
        (lookup, defaults)
        for lookup, defaults in prefix_calls
        if lookup.get("prefix") == "10.1.20.0/22"
    )
    _, defaults = site_prefix_call
    assert defaults["role"] == 77  # the site-aggregate role's id


def test_no_site_aggregate_addressing_key_sets_no_role_on_site_prefix(monkeypatch):
    # Existing sites pass no site_aggregate, so no role.
    nb = MagicMock()
    prefix_calls = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        result = MagicMock()
        if "prefix" in lookup:
            prefix_calls.append((lookup, defaults))
        result.id = 1
        result.prefix = lookup.get("prefix")
        result.slug = lookup.get("slug")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=5, slug="us-central-01a")

    addressing = {
        "site_prefix": "10.1.1.0/24",
        "production_prefix": "10.1.1.0/25",
        "loopback_prefix": "10.1.1.0/27",
        "p2p_prefix": "10.1.1.32/27",
    }

    build_ipam_hierarchy(nb, site, addressing)

    site_prefix_call = next(
        (lookup, defaults)
        for lookup, defaults in prefix_calls
        if lookup.get("prefix") == "10.1.1.0/24"
    )
    _, defaults = site_prefix_call
    assert "role" not in defaults


def test_hold_down_prefix_creates_a_tagged_role_scoped_prefix(monkeypatch):
    nb = MagicMock()
    prefix_calls = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        prefix_calls.append((lookup, defaults))
        result = MagicMock()
        # Distinct ids so a wrong id cannot pass by coincidence.
        if lookup.get("slug") == "hold-down-block":
            result.id = 42  # the tag
        elif lookup.get("slug") == "hold-down":
            result.id = 99  # the role
        else:
            result.id = 1
        result.prefix = lookup.get("prefix") or (defaults or {}).get("prefix")
        result.slug = lookup.get("slug")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=9, slug="us-central-02a")

    addressing = {
        "site_prefix": "10.1.20.0/22",
        "site_aggregate": True,
        "hold_down_prefix": "10.1.20.0/23",
        "production_prefix": "10.1.22.0/24",
        "loopback_prefix": "10.1.22.0/26",
        "p2p_prefix": "10.1.22.64/26",
    }

    result = build_ipam_hierarchy(nb, site, addressing)

    hold_down_call = next(
        (lookup, defaults)
        for lookup, defaults in prefix_calls
        if lookup.get("prefix") == "10.1.20.0/23"
    )
    lookup, defaults = hold_down_call
    # Per-site value, so scoped to site.id.
    assert lookup["scope_type"] == "dcim.site"
    assert lookup["scope_id"] == 9
    assert defaults["tags"] == [42]  # the hold-down-block tag's id
    assert result["hold_down_prefix"].prefix == "10.1.20.0/23"

    tag_lookup = next(lookup for lookup, defaults in prefix_calls if lookup.get("slug") == "hold-down-block")
    assert tag_lookup == {"slug": "hold-down-block"}


def test_no_hold_down_prefix_addressing_key_creates_no_tag_or_extra_prefix(monkeypatch):
    nb = MagicMock()
    prefix_calls = []

    def fake_get_or_create(endpoint, lookup, defaults=None):
        prefix_calls.append(lookup)
        result = MagicMock()
        result.id = 1
        result.prefix = lookup.get("prefix")
        return result

    monkeypatch.setattr(bdm, "get_or_create", fake_get_or_create)
    nb.ipam.rirs.get.return_value = MagicMock(id=1)
    site = MagicMock(id=5, slug="us-central-01a")

    addressing = {
        "site_prefix": "10.1.1.0/24",
        "production_prefix": "10.1.1.0/25",
        "loopback_prefix": "10.1.1.0/27",
        "p2p_prefix": "10.1.1.32/27",
    }

    build_ipam_hierarchy(nb, site, addressing)

    assert not any(lookup.get("slug") == "hold-down-block" for lookup in prefix_calls)


def test_build_cables_creates_a_same_site_cable_from_the_local_devices_dict():
    nb = MagicMock()
    nb.dcim.cables.create.return_value = MagicMock()

    a_iface = MagicMock(id=1, cable=None)
    b_iface = MagicMock(id=2, cable=None)
    devices = {
        "esr-01-us-central-01a": {"interfaces": {"eth2": a_iface}},
        "dsr-01-us-central-01a": {"interfaces": {"eth1": b_iface}},
    }
    data = {
        "devices": {
            "esr-01-us-central-01a": {
                "interfaces": {
                    "eth2": {
                        "connects_to": {"device": "dsr-01-us-central-01a", "interface": "eth1"}
                    }
                }
            },
            "dsr-01-us-central-01a": {
                "interfaces": {
                    "eth1": {
                        "connects_to": {"device": "esr-01-us-central-01a", "interface": "eth2"}
                    }
                }
            },
        }
    }

    build_cables(nb, data, devices)

    nb.dcim.cables.create.assert_called_once()
    nb.dcim.devices.get.assert_not_called()


def test_build_cables_looks_up_cross_site_peer_in_netbox_when_not_in_local_devices_dict():
    # Peer may live in another site's devices dict; no KeyError.
    nb = MagicMock()
    a_iface = MagicMock(id=1, cable=None)
    devices = {"bbr-01-us-central-01a": {"interfaces": {"eth3": a_iface}}}
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "interfaces": {
                    "eth3": {
                        "connects_to": {"device": "bbr-01-us-south-01a", "interface": "eth1"}
                    }
                }
            }
        }
    }

    peer_device = MagicMock(id=99)
    nb.dcim.devices.get.return_value = peer_device
    # MagicMock's name kwarg is not settable; assign after creation.
    peer_iface = MagicMock(id=2, cable=None)
    peer_iface.name = "eth1"
    nb.dcim.interfaces.filter.return_value = [peer_iface]

    build_cables(nb, data, devices)

    nb.dcim.devices.get.assert_called_once_with(name="bbr-01-us-south-01a")
    nb.dcim.cables.create.assert_called_once()
    kwargs = nb.dcim.cables.create.call_args.kwargs
    assert kwargs["a_terminations"] == [{"object_type": "dcim.interface", "object_id": 1}]
    assert kwargs["b_terminations"] == [{"object_type": "dcim.interface", "object_id": 2}]


def test_build_cables_skips_gracefully_when_peer_site_not_yet_built():
    # Peer device may not exist yet; must not crash.
    nb = MagicMock()
    a_iface = MagicMock(id=1, cable=None)
    devices = {"bbr-01-us-central-01a": {"interfaces": {"eth3": a_iface}}}
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "interfaces": {
                    "eth3": {
                        "connects_to": {"device": "bbr-01-us-south-01a", "interface": "eth1"}
                    }
                }
            }
        }
    }
    nb.dcim.devices.get.return_value = None

    build_cables(nb, data, devices)

    nb.dcim.cables.create.assert_not_called()


def test_route_reflector_is_a_boolean_device_custom_field():
    from netbox.build_data_model import CUSTOM_FIELD_DEFS
    defs = {d["name"]: d for d in CUSTOM_FIELD_DEFS}
    assert defs["route_reflector"]["type"] == "boolean"
    assert defs["route_reflector"]["object_types"] == ["dcim.device"]


def test_sr_index_is_an_integer_device_custom_field():
    from netbox.build_data_model import CUSTOM_FIELD_DEFS
    defs = {d["name"]: d for d in CUSTOM_FIELD_DEFS}
    assert defs["sr_index"]["type"] == "integer"
    assert defs["sr_index"]["object_types"] == ["dcim.device"]


def test_build_devices_pushes_every_ladder_and_dci_field():
    # Every device field the templates read is written to NetBox.
    import inspect
    from netbox import build_data_model
    src = inspect.getsource(build_data_model.build_devices)
    for field in ("route_reflector", "l3vni", "vrf_name", "default_gw", "backbone_peers", "net", "sr_index"):
        assert f'custom_fields["{field}"]' in src
