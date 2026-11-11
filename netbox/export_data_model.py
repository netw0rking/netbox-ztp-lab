import json
import subprocess
import sys
from pathlib import Path

from netbox.nb_client import get_client
from schema.validate import validate_data_model

REPO_ROOT = Path(__file__).parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "netbox-export.json"
SITE_SLUGS = [
    "us-east-01a",
    "us-central-01a",
    "us-west-01a",
    # ladder backbone POPs, R1..R10
    "us-central-02a",
    "us-east-02a",
    "us-north-02a",
    "us-west-02a",
    "us-west-04a",
    "us-central-03a",
    "us-east-03a",
    "us-south-02a",
    "us-west-03a",
    "us-west-05a",
]


def export_data_model(nb, site_slugs):
    all_nb_devices = []
    for site_slug in site_slugs:
        site = nb.dcim.sites.get(slug=site_slug)
        if site is None:
            raise RuntimeError(f"site '{site_slug}' not found in NetBox - run build_data_model.py first")
        all_nb_devices.extend(nb.dcim.devices.filter(site_id=site.id))

    # Global across sites, so cross-site cable peers resolve.
    device_by_id = {d.id: d for d in all_nb_devices}
    devices = {}

    for nb_device in all_nb_devices:
        entry = {"role": nb_device.role.slug}
        if nb_device.platform:
            entry["platform"] = nb_device.platform.slug
        if nb_device.custom_fields.get("bgp_asn") is not None:
            entry["bgp_asn"] = nb_device.custom_fields["bgp_asn"]
        if nb_device.custom_fields.get("serial_number"):
            entry["serial_number"] = nb_device.custom_fields["serial_number"]
        if nb_device.custom_fields.get("overlay_vlan_id") is not None:
            entry["overlay_vlan_id"] = nb_device.custom_fields["overlay_vlan_id"]
        if nb_device.custom_fields.get("overlay_vni") is not None:
            entry["overlay_vni"] = nb_device.custom_fields["overlay_vni"]
        if nb_device.custom_fields.get("overlay_test_ip"):
            entry["overlay_test_ip"] = nb_device.custom_fields["overlay_test_ip"]
        if nb_device.custom_fields.get("net"):
            entry["net"] = nb_device.custom_fields["net"]
        if nb_device.custom_fields.get("backbone_peers"):
            entry["backbone_peers"] = nb_device.custom_fields["backbone_peers"]
        if nb_device.custom_fields.get("vendor_image"):
            entry["vendor_image"] = nb_device.custom_fields["vendor_image"]
        if nb_device.custom_fields.get("srv6_locator"):
            entry["srv6_locator"] = nb_device.custom_fields["srv6_locator"]
        if nb_device.custom_fields.get("sr_policies"):
            entry["sr_policies"] = nb_device.custom_fields["sr_policies"]
        # Routed EVPN Type-5 / DCI fields, read back from NetBox.
        if nb_device.custom_fields.get("l3vni") is not None:
            entry["l3vni"] = nb_device.custom_fields["l3vni"]
        if nb_device.custom_fields.get("vrf_name"):
            entry["vrf_name"] = nb_device.custom_fields["vrf_name"]
        if nb_device.custom_fields.get("default_gw"):
            entry["default_gw"] = nb_device.custom_fields["default_gw"]
        if nb_device.custom_fields.get("route_reflector"):
            entry["route_reflector"] = True
        if nb_device.custom_fields.get("sr_index") is not None:
            entry["sr_index"] = nb_device.custom_fields["sr_index"]
        if nb_device.role.slug == "bbr":
            # Scope to this device's site; the Tag must exist first.
            hold_down_tag = nb.extras.tags.get(slug="hold-down-block")
            hold_down_prefixes = (
                list(
                    nb.ipam.prefixes.filter(
                        tag="hold-down-block", scope_type="dcim.site", scope_id=nb_device.site.id
                    )
                )
                if hold_down_tag is not None
                else []
            )
            if hold_down_prefixes:
                entry["hold_down_block"] = hold_down_prefixes[0].prefix
        if nb_device.primary_ip4:
            entry["loopback"] = nb_device.primary_ip4.address

        mgmt_intf = nb.dcim.interfaces.get(device_id=nb_device.id, name="mgmt0")
        if mgmt_intf:
            mgmt_ip = nb.ipam.ip_addresses.get(
                assigned_object_type="dcim.interface", assigned_object_id=mgmt_intf.id
            )
            if mgmt_ip:
                entry["mgmt_ip"] = mgmt_ip.address

        interfaces = {}
        for iface in nb.dcim.interfaces.filter(device_id=nb_device.id):
            cable_id = getattr(iface, "cable", None)
            if not cable_id:
                # Placeholder interfaces, not part of the cabled wiring.
                continue

            if_entry = {}
            ip = nb.ipam.ip_addresses.get(
                assigned_object_type="dcim.interface", assigned_object_id=iface.id
            )
            if ip:
                if_entry["ip"] = ip.address

            cable = nb.dcim.cables.get(cable_id.id)
            other_end = None
            for termination in list(cable.a_terminations) + list(cable.b_terminations):
                # Compare termination .id directly; it is the interface id.
                if termination.id != iface.id:
                    other_end = termination
            if other_end:
                peer_iface = nb.dcim.interfaces.get(other_end.id)
                peer_device = device_by_id.get(peer_iface.device.id)
                connects_to = {"device": peer_device.name, "interface": peer_iface.name}

                # Link role: fabric, oob, host-facing, or backbone ring.
                peer_role = peer_device.role.slug if peer_device.role else None
                if peer_role == "host":
                    if_entry["role"] = "access" if if_entry.get("ip") else "overlay"
                elif nb_device.site.id != peer_device.site.id:
                    if_entry["role"] = "backbone"
                else:
                    self_asn = nb_device.custom_fields.get("bgp_asn")
                    peer_asn = peer_device.custom_fields.get("bgp_asn")
                    is_fabric = self_asn is not None and peer_asn is not None
                    if_entry["role"] = "fabric" if is_fabric else "oob"

                    if is_fabric:
                        peer_ip = nb.ipam.ip_addresses.get(
                            assigned_object_type="dcim.interface", assigned_object_id=peer_iface.id
                        )
                        if peer_ip:
                            connects_to["peer_ip"] = peer_ip.address
                        connects_to["peer_asn"] = peer_asn

                if_entry["connects_to"] = connects_to

            interfaces[iface.name] = if_entry

        entry["interfaces"] = interfaces
        devices[nb_device.name] = entry

    return {"site": ",".join(site_slugs), "devices": devices}


def write_and_maybe_pr(data):
    errors = validate_data_model(data)
    if errors:
        print("Exported data model failed validation:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    new_content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    old_content = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else None

    if new_content == old_content:
        print("No changes since last export.")
        return

    OUTPUT_PATH.write_text(new_content)
    branch = "netbox-export-update"
    subprocess.run(["git", "checkout", "-B", branch], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "add", str(OUTPUT_PATH)], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Update data/netbox-export.json from NetBox"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["git", "push", "-u", "origin", branch, "--force"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        [
            "gh", "pr", "create",
            "--title", "Update NetBox data model export",
            "--body", "Automated export from netbox/export_data_model.py",
            "--base", "main",
            "--head", branch,
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    print(f"Opened PR from branch '{branch}'.")


def main(argv=None):
    """CLI entry point. Also used by `ztp generate-data`."""
    nb = get_client()
    data = export_data_model(nb, SITE_SLUGS)
    write_and_maybe_pr(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
