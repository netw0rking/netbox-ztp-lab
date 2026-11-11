# netbox/reconcile_backbone_fabric_lab.py
"""Removes what build_backbone_fabric_lab.py leaves behind in NetBox.

The builder only ever creates or updates. When the lab's design changes
, that leaves:
  - retired sites (us-south-01a, us-north-01a) with their devices, IPs
    and site-scoped prefixes,
  - cables on a fixture interface whose far end isn't the fixture's
    connects_to - incl. ones left one-ended by a deleted device,
  - IPs on a fixture interface that the fixture no longer lists (two IPs
    on one interface break the exporter's peer_ip lookup),
  - custom fields a fixture device no longer sets (the exporter would
    keep exporting e.g. overlay_vni for an ESR now in the routed VRF).

Dry run by default - prints every action. --apply executes them.
"""
import argparse

from netbox.build_backbone_fabric_lab import SITES
from netbox.build_data_model import CUSTOM_FIELD_DEFS, load_fixture
from netbox.nb_client import get_client

RETIRED_SITES = ["us-south-01a", "us-north-01a"]
CUSTOM_FIELD_NAMES = [d["name"] for d in CUSTOM_FIELD_DEFS]


def _is_set(value):
    return value not in (None, "", [], {})


def plan_actions(nb, sites, retired_sites):
    """List of (kind, object, detail) tuples, in a safe execution order."""
    actions = []
    for slug in retired_sites:
        site = nb.dcim.sites.get(slug=slug)
        if site is None:
            continue
        for dev in nb.dcim.devices.filter(site_id=site.id):
            for ip in nb.ipam.ip_addresses.filter(device_id=dev.id):
                actions.append(("delete-ip", ip, f"{dev.name} {ip.address}"))
            actions.append(("delete-device", dev, dev.name))
        for prefix in nb.ipam.prefixes.filter(scope_type="dcim.site", scope_id=site.id):
            actions.append(("delete-prefix", prefix, prefix.prefix))
        actions.append(("delete-site", site, slug))

    for cfg in sites.values():
        data = load_fixture(cfg["fixture"])
        for name, dev in data["devices"].items():
            nb_dev = nb.dcim.devices.get(name=name)
            if nb_dev is None:
                continue
            for field in CUSTOM_FIELD_NAMES:
                if field not in dev and _is_set(nb_dev.custom_fields.get(field)):
                    actions.append(("clear-field", nb_dev, field))
            for if_name, intf in dev.get("interfaces", {}).items():
                nb_if = nb.dcim.interfaces.get(device_id=nb_dev.id, name=if_name)
                if nb_if is None:
                    continue
                # A cable whose far end isn't what the fixture says - incl.
                # one left dangling when the far device was deleted
                # (NetBox keeps it, one-ended) - blocks build_cables, which
                # skips any interface that already has a cable.
                want = intf.get("connects_to")
                if getattr(nb_if, "cable", None):
                    peers = nb_if.link_peers or []
                    actual = {(p.device.name, p.name) for p in peers if hasattr(p, "device")}
                    if not want or actual != {(want["device"], want["interface"])}:
                        actions.append(("delete-cable", nb.dcim.cables.get(nb_if.cable.id),
                                        f"{name} {if_name}"))
                for ip in nb.ipam.ip_addresses.filter(interface_id=nb_if.id):
                    if ip.address != intf.get("ip"):
                        actions.append(("delete-ip", ip, f"{name} {if_name}"))
    return actions


def apply_actions(actions):
    for kind, obj, detail in actions:
        if kind == "clear-field":
            obj.custom_fields[detail] = None
            obj.save()
        else:
            obj.delete()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = parser.parse_args(argv)
    nb = get_client()
    actions = plan_actions(nb, SITES, RETIRED_SITES)
    for kind, obj, detail in actions:
        label = obj.name if kind == "clear-field" else ""
        print(f"{kind:14} {label} {detail}".rstrip())
    if not actions:
        print("Nothing to reconcile.")
    elif args.apply:
        apply_actions(actions)
        print(f"Applied {len(actions)} actions.")
    else:
        print(f"Dry run: {len(actions)} actions. Re-run with --apply to execute.")


if __name__ == "__main__":
    main()
