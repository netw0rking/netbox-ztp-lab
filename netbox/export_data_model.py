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
    # ladder backbone POPs, R1..R10 (one bbr each)
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

    # Global across every exported site (not per-site) so that cross-site
    # cable-peer resolution below finds peers regardless of which site they
    # belong to - e.g. a backbone ring link between two different sites'
    # BBRs.
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
        # Routed EVPN Type-5 / DCI fields: built into NetBox all along but
        # never read back, so they never survived a round-trip.
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
            # Not a device custom field. The Prefix itself is the single source
            # of truth; scope_id here MUST stay this device's own site
            # - matching by tag alone across every site would leak one
            # site's hold-down block onto another's bbr device.
            #
            # Check the Tag object itself exists before filtering by it -
            # confirmed live: no site currently uses the new addressing
            # scheme (deliberately), so the
            # "hold-down-block" Tag has never actually been created in
            # NetBox. `nb.ipam.prefixes.filter(tag="hold-down-block", ...)`
            # validates the tag slug against NetBox's own live choices and
            # raises a 400 ("not one of the available choices") when it
            # doesn't exist yet, rather than just returning no matches -
            # every unit test here mocks this call, so none of them could
            # have caught this real NetBox validation behavior.
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
                # Not part of the fabric/OOB wiring - e.g. the loopback0
                # placeholder interface used solely to hold a
                # primary_ip4 (NetBox requires primary_ip4 be assigned to
                # one of the device's own interfaces), or the mgmt0/Vlan1
                # placeholder interfaces used the same way for the
                # containerlab mgmt IP / OOB SVI IP. The canonical data
                # model's "interfaces" dict only ever contains cabled
                # interfaces; the loopback and mgmt_ip addresses
                # themselves are already captured above.
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
                # a_terminations/b_terminations entries are the resolved
                # terminating objects themselves (an Interfaces record, same
                # shape confirmed live for the cable-creation code) -
                # not a dict with a flat "object_id" key. Compare .id
                # directly (confirmed live: termination.id is the
                # interface's own id, e.g. 58/66 for a real fabric cable).
                if termination.id != iface.id:
                    other_end = termination
            if other_end:
                peer_iface = nb.dcim.interfaces.get(other_end.id)
                peer_device = device_by_id.get(peer_iface.device.id)
                connects_to = {"device": peer_device.name, "interface": peer_iface.name}

                # A link is "fabric" iff both ends are BGP-speaking (spine/
                # leaf) devices - not merely because this end carries an IP.
                # Confirmed live: ztp-server-us-east-01a's eth1 carries an IP
                # but is an oob link (its peer, mgmt-switch-us-east-01a, has
                # no bgp_asn); using if_entry.get("ip") here mis-classified
                # it as "fabric". peer_ip/peer_asn are likewise fabric-only -
                # confirmed live: mgmt-switch-us-east-01a's switch-port links
                # to the spines/leafs (oob) otherwise leaked the peer's
                # bgp_asn, and the mgmt-switch-us-east-01a->ztp-server-us-east-01a
                # oob link leaked ztp-server-us-east-01a's eth1 IP as a
                # spurious "peer_ip".
                #
                # A link to a "host"-role device is neither fabric nor oob -
                # it's this lab's test endpoint, either "overlay" (L2-bridged,
                # no address of its own on this end) or "access" (routed
                # EVPN Type-5, this end has its own IP). Confirmed live: the
                # old two-way fabric/oob split silently mis-classified
                # esr-01-us-central-01a's host-facing link as "oob" (host has
                # no bgp_asn), and generate_topology.py's FRR branch drops
                # any interface matching none of its four known roles - the
                # host-facing port was never enslaved into the VXLAN bridge,
                # so host-01 couldn't reach anything across the fabric.
                # A link between two devices in DIFFERENT sites is a
                # backbone-ring link - IS-IS-only,
                # no peer_ip/peer_asn (the real EVPN exchange rides a
                # separate multihop eBGP session, backbone_peers, not
                # per-link BGP). Confirmed live deploying the 4-site ring:
                # both ring BBRs have bgp_asn set (same as any "fabric"
                # pair), so the old fabric/oob-only split mis-classified
                # a ring link as "fabric" - generate_topology.py's
                # per-interface IS-IS enablement is gated on
                # role == "backbone", so it silently never rendered and
                # isisd never formed an adjacency at all.
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
    """CLI entry point - also what `ztp generate-data` (see ztp_cli/cli.py
   )
    calls directly, rather than shelling out to this file as a
    subprocess. Takes no arguments (argv accepted only for a consistent
    `main(argv)` shape across this project's CLI entry points)."""
    nb = get_client()
    data = export_data_model(nb, SITE_SLUGS)
    write_and_maybe_pr(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
