"""Build NetBox records for the backbone+fabric lab."""
from pathlib import Path

from netbox.build_data_model import (
    build_cables,
    build_devices,
    build_ipam_hierarchy,
    build_schema_objects,
    load_fixture,
)
from netbox.nb_client import get_client

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

# Addressing for fabric and ladder backbone sites.
SITES = {
    "us-central-01a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_central_01a.json",
        "addressing": {
            "site_prefix": "10.1.1.0/24",
            "production_prefix": "10.1.1.0/25",
            "loopback_prefix": "10.1.1.0/27",
            "p2p_prefix": "10.1.1.32/27",
            "mgmt_prefix": "10.1.1.128/25",
        },
    },
    "us-west-01a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_west_01a.json",
        "addressing": {
            "site_prefix": "10.1.2.0/24",
            "production_prefix": "10.1.2.0/25",
            "loopback_prefix": "10.1.2.0/27",
            "p2p_prefix": "10.1.2.32/27",
            "mgmt_prefix": "10.1.2.128/25",
        },
    },
    "us-central-02a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_central_02a.json",
        "addressing": {
            "site_prefix": "10.200.1.0/24",
            "production_prefix": "192.168.1.1/32",
            "loopback_prefix": "192.168.1.1/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.1.0/29",
        },
    },
    "us-east-02a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_east_02a.json",
        "addressing": {
            "site_prefix": "10.200.2.0/24",
            "production_prefix": "192.168.1.2/32",
            "loopback_prefix": "192.168.1.2/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.2.0/29",
        },
    },
    "us-north-02a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_north_02a.json",
        "addressing": {
            "site_prefix": "10.200.3.0/24",
            "production_prefix": "192.168.1.3/32",
            "loopback_prefix": "192.168.1.3/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.3.0/29",
        },
    },
    "us-west-02a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_west_02a.json",
        "addressing": {
            "site_prefix": "10.200.4.0/24",
            "production_prefix": "192.168.1.4/32",
            "loopback_prefix": "192.168.1.4/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.4.0/29",
        },
    },
    "us-west-04a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_west_04a.json",
        "addressing": {
            "site_prefix": "10.200.5.0/24",
            "production_prefix": "192.168.1.5/32",
            "loopback_prefix": "192.168.1.5/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.5.0/29",
        },
    },
    "us-central-03a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_central_03a.json",
        "addressing": {
            "site_prefix": "10.200.6.0/24",
            "production_prefix": "192.168.1.6/32",
            "loopback_prefix": "192.168.1.6/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.6.0/29",
        },
    },
    "us-east-03a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_east_03a.json",
        "addressing": {
            "site_prefix": "10.200.7.0/24",
            "production_prefix": "192.168.1.7/32",
            "loopback_prefix": "192.168.1.7/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.7.0/29",
        },
    },
    "us-south-02a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_south_02a.json",
        "addressing": {
            "site_prefix": "10.200.8.0/24",
            "production_prefix": "192.168.1.8/32",
            "loopback_prefix": "192.168.1.8/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.8.0/29",
        },
    },
    "us-west-03a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_west_03a.json",
        "addressing": {
            "site_prefix": "10.200.9.0/24",
            "production_prefix": "192.168.1.9/32",
            "loopback_prefix": "192.168.1.9/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.9.0/29",
        },
    },
    "us-west-05a": {
        "fixture": FIXTURES_DIR / "backbone_fabric_us_west_05a.json",
        "addressing": {
            "site_prefix": "10.200.10.0/24",
            "production_prefix": "192.168.1.10/32",
            "loopback_prefix": "192.168.1.10/32",
            "p2p_prefix": "10.96.0.0/12",
            "mgmt_prefix": "10.200.10.0/29",
        },
    },
}

SITE_DESCRIPTION = (
    "Virtual lab (containerlab) - backbone+fabric lab, see Comments for "
    "lab name, repo path, topology file, and deploy/destroy commands."
)


def site_comments(site_slug):
    return f"""Virtual lab (containerlab), not a physical site.
Lab name: backbone-fabric
Repo: ~/git/netbox-ztp-lab
Topology file: ~/git/netbox-ztp-lab/topology/backbone-fabric.clab.yml
  (generated by generate_topology.py from this site's devices/cables -
   do not hand-edit)
Deploy: cd ~/git/netbox-ztp-lab && containerlab deploy -t topology/backbone-fabric.clab.yml
Destroy: containerlab destroy -t topology/backbone-fabric.clab.yml
Each site has its own ztp-server.
See repo README.md for full ZTP pipeline details."""


def main():
    nb = get_client()
    for site_slug, cfg in SITES.items():
        data = load_fixture(cfg["fixture"])
        schema = build_schema_objects(
            nb, data, site_slug, SITE_DESCRIPTION, site_comments(site_slug)
        )
        print(f"Site: {schema['site'].name} (id={schema['site'].id})")

        ipam = build_ipam_hierarchy(nb, schema["site"], cfg["addressing"])
        print(f"  Aggregate: {ipam['aggregate'].prefix}, Site: {ipam['site_prefix'].prefix}")

        devices = build_devices(nb, data, schema)
        print(f"  Devices: {list(devices.keys())}")

        build_cables(nb, data, devices)
        print("  Cables: created/verified for all connects_to pairs")


if __name__ == "__main__":
    main()
