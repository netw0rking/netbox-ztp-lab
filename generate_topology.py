import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
from pathlib import Path

import yaml

from schema.validate import validate_data_model
from ztp_server.render import _env as TEMPLATE_ENV

REPO_ROOT = Path(__file__).parent
# Private secrets repo, cloned out of band. Override with env var.
SECRETS_REPO_PATH = Path(
    os.environ.get("SECRETS_REPO_PATH", str(Path.home() / "git" / "netbox-ztp-lab-secrets"))
)


# SRLinux bootstrap runs via containerlab exec:, not real ZTP.
ZTP_SERVER_HOST = "10.0.0.130"
# us-east-01a OOB network.
US_EAST_01A_OOB_NETWORK = ipaddress.ip_network("10.0.0.0/24")
# Fallback for us-east-01a devices with no OOB interface.
ZTP_SERVER_CONTAINER_NAME = "ztp-server-us-east-01a"


def site_server_host(oob_ip):
    """Last usable address of the oob_ip subnet."""
    network = ipaddress.ip_interface(oob_ip).network
    return str(network.broadcast_address - 1)
SRLINUX_PYTHON = "/opt/srlinux/python/virtual-env/bin/python3"
SRLINUX_BOOTSTRAP_CONTAINER_PATH = "/tmp/ztp_bootstrap.py"
FRR_BOOTSTRAP_LOG_PATH = "/tmp/ztp_bootstrap.log"
# Upstream FRR image -> this repo's SSH-enabled build.
FRR_SSH_IMAGES = {
    "frrouting/frr:latest": "netbox-ztp-lab-frr:8.4.1",
    "quay.io/frrouting/frr:10.5.1": "netbox-ztp-lab-frr:10.5.1",
}
# Bootstrap script in Python, for retry logic.
FRR_BOOTSTRAP_CONTAINER_PATH = "/tmp/ztp_bootstrap.py"
# Bind-mounted so bgpd runs from first boot.
FRR_DAEMONS_HOST_PATH = REPO_ROOT / "configs" / "frr-daemons"

# Daemons file for bbr devices (isisd enabled).
FRR_DAEMONS_BBR_HOST_PATH = REPO_ROOT / "configs" / "frr-daemons-bbr"

# FRR runs as a plain linux-kind container.
PLATFORM_KIND = {"ceos": "arista_ceos", "srlinux": "nokia_srlinux", "frr": "linux", "linux": "linux"}

# Source of truth for container images.
# ceos pinned to 4.32.0F for cgroups v2 hosts.
PLATFORM_IMAGE = {
    "ceos": "harsha258/ceos:4.32.0F",
    "srlinux": "ghcr.io/nokia/srlinux:latest",
    "frr": "frrouting/frr:latest",
    "linux": "netbox-ztp-lab-ztp-server:latest",
}

# Host devices have no platform; set the image explicitly.
HOST_IMAGE = "alpine:latest"

IDENTITY_BIND_PATH = {
    "ceos": "/mnt/flash/ceos-config",
    "srlinux": "/etc/opt/srlinux/ztp/chassis_id_suffix",
}


CEOS_ETHERNET_RE = re.compile(r"^Ethernet(\d+)$")


def clab_endpoint_name(if_name, platform, role=None, dev_name=None):
    """Map a NOS interface name to its containerlab endpoint name."""
    if role == "mgmt-switch":
        digest = hashlib.sha1(f"{dev_name}:{if_name}".encode()).hexdigest()[:8]
        return f"eth-{digest}"
    if platform == "ceos":
        match = CEOS_ETHERNET_RE.match(if_name)
        if match:
            return f"eth{match.group(1)}"
    return if_name


# Max Linux netdev name length (IFNAMSIZ - 1).
OVS_BRIDGE_NAME_MAX = 15


def clab_node_name(name, role):
    """Containerlab node name; long mgmt-switch names are hashed."""
    if role != "mgmt-switch" or len(name) <= OVS_BRIDGE_NAME_MAX:
        return name
    # Short deterministic name for ovs-bridge nodes.
    digest = hashlib.sha1(name.encode()).hexdigest()[:8]
    return f"ovsbr-{digest}"


def build_links(devices):
    links = []
    seen = set()
    for dev_name, dev in devices.items():
        for if_name, intf in dev.get("interfaces", {}).items():
            connects_to = intf.get("connects_to")
            if not connects_to:
                continue
            peer_dev = connects_to["device"]
            peer_if = connects_to["interface"]
            key = frozenset({(dev_name, if_name), (peer_dev, peer_if)})
            if key in seen:
                continue
            seen.add(key)
            local_ep = clab_endpoint_name(if_name, dev.get("platform"), dev.get("role"), dev_name)
            peer_dev_info = devices.get(peer_dev, {})
            peer_ep = clab_endpoint_name(
                peer_if, peer_dev_info.get("platform"), peer_dev_info.get("role"), peer_dev
            )
            local_name = clab_node_name(dev_name, dev.get("role"))
            peer_name = clab_node_name(peer_dev, peer_dev_info.get("role"))
            links.append({"endpoints": [f"{local_name}:{local_ep}", f"{peer_name}:{peer_ep}"]})
    return links


# Gates which devices get bootstrapped. FRR serial is in the script.
BOOTSTRAPPABLE_PLATFORMS = set(IDENTITY_BIND_PATH) | {"frr"}


# NetBox site -> containerlab lab. One topology file per lab.
LAB_SITES = {
    "us-east-01a": ("us-east-01a",),
    "backbone-fabric": (
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
    ),
}
# Per-lab containerlab mgmt network, avoids Docker pool overlap.
LAB_MGMT = {
    "backbone-fabric": {
        "network": "clab-backbone-fabric",
        "ipv4-subnet": "172.20.21.0/24",
        "ipv6-subnet": "3fff:172:20:21::/64",
    },
}
SITE_LAB = {site: lab for lab, sites in LAB_SITES.items() for site in sites}
# Site comes from the device name suffix.
DEVICE_SITE_RE = re.compile(r"-([a-z]{2}-[a-z]+-\d{2}[a-z])$")


def split_devices_by_lab(devices):
    """Group devices by lab. Raises on unmapped sites or cross-lab cables."""
    labs = {}
    device_lab = {}
    for name, dev in devices.items():
        m = DEVICE_SITE_RE.search(name)
        site = m.group(1) if m else None
        if site not in SITE_LAB:
            raise ValueError(f"{name}: site {site!r} is not mapped to a lab in LAB_SITES")
        device_lab[name] = SITE_LAB[site]
        labs.setdefault(SITE_LAB[site], {})[name] = dev
    for name, dev in devices.items():
        for if_name, intf in dev.get("interfaces", {}).items():
            peer = (intf.get("connects_to") or {}).get("device")
            if peer in device_lab and device_lab[peer] != device_lab[name]:
                raise ValueError(
                    f"{name}:{if_name} is cabled to {peer}, which is in lab "
                    f"{device_lab[peer]!r}, not {device_lab[name]!r} - labs must be separate"
                )
    return labs


# us-east-01a DHCP settings. Only site with DHCP-ZTP clients.
US_EAST_01A_DHCP_RANGE = "10.0.0.140,10.0.0.199,255.255.255.128,12h"
US_EAST_01A_DHCP_ROUTER = "10.0.0.129"
# Platforms that really DHCP for ZTP (FRR is bootstrapped via exec:).
DHCP_ZTP_PLATFORMS = {"ceos", "srlinux"}
DNSMASQ_SITE_CONF_CONTAINER_PATH = "/etc/dnsmasq.site.conf"


def _oob_ip(dev):
    for intf in dev.get("interfaces", {}).values():
        if intf.get("role") == "oob" and intf.get("ip"):
            return intf["ip"]
    return None


def write_dnsmasq_configs(devices, output_dir):
    """Render a dnsmasq config per server device. Returns {name: Path}."""
    output_dir = Path(output_dir)
    template = TEMPLATE_ENV.get_template("dnsmasq.conf.j2")
    paths = {}
    for name, dev in devices.items():
        if dev.get("role") != "server":
            continue
        server_oob = _oob_ip(dev)
        if not server_oob:
            continue
        server_if = ipaddress.ip_interface(server_oob)
        dhcp_clients = [
            other for other, odev in devices.items()
            if odev.get("platform") in DHCP_ZTP_PLATFORMS
            and _oob_ip(odev)
            and ipaddress.ip_interface(_oob_ip(odev)).ip in server_if.network
        ]
        dhcp_range = dhcp_router = None
        if server_if.ip in US_EAST_01A_OOB_NETWORK:
            dhcp_range, dhcp_router = US_EAST_01A_DHCP_RANGE, US_EAST_01A_DHCP_ROUTER
        elif dhcp_clients:
            raise ValueError(
                f"{name}: DHCP-ZTP devices {sorted(dhcp_clients)} share its OOB "
                f"subnet {server_if.network}, but no DHCP range is modeled for it"
            )
        device_dir = output_dir / name
        device_dir.mkdir(parents=True, exist_ok=True)
        path = device_dir / "dnsmasq.conf"
        path.write_text(template.render(
            server_ip=str(server_if.ip), dhcp_range=dhcp_range, dhcp_router=dhcp_router,
        ))
        paths[name] = path
    return paths


def write_identity_files(devices, output_dir):
    output_dir = Path(output_dir)
    binds = {}
    exec_cmds = {}
    for name, dev in devices.items():
        serial = dev.get("serial_number")
        platform = dev.get("platform")
        if not serial or platform not in BOOTSTRAPPABLE_PLATFORMS:
            continue

        device_dir = output_dir / name
        device_dir.mkdir(parents=True, exist_ok=True)

        if platform in IDENTITY_BIND_PATH:
            if platform == "ceos":
                filename = "ceos-config"
                content = f"SERIALNUMBER={serial}\n"
            else:
                filename = "chassis_id_suffix"
                content = f"{serial}\n"

            file_path = device_dir / filename
            file_path.write_text(content)
            # containerlab resolves relative bind paths from the topology dir.
            binds[name] = [f"{file_path.resolve()}:{IDENTITY_BIND_PATH[platform]}"]
        else:
            binds[name] = []

        if platform == "srlinux":
            # Rendered and bound here, run via exec: (no ZTP daemon).
            oob_interface = oob_ip = None
            for if_name, intf in dev.get("interfaces", {}).items():
                if intf.get("role") == "oob" and intf.get("ip"):
                    oob_interface, oob_ip = if_name, intf["ip"]
                    break

            bootstrap_template = TEMPLATE_ENV.get_template("ztp_srlinux.py.j2")
            bootstrap_content = bootstrap_template.render(
                server_host=ZTP_SERVER_HOST, oob_interface=oob_interface, oob_ip=oob_ip
            )
            bootstrap_path = device_dir / "ztp_bootstrap.py"
            bootstrap_path.write_text(bootstrap_content)
            binds[name].append(f"{bootstrap_path.resolve()}:{SRLINUX_BOOTSTRAP_CONTAINER_PATH}")
            exec_cmds[name] = [f"{SRLINUX_PYTHON} {SRLINUX_BOOTSTRAP_CONTAINER_PATH}"]

        elif platform == "frr":
            # Serial is baked into the rendered bootstrap script.
            oob_interface = oob_ip = None
            fabric_interfaces = []
            # overlay: L2 host port, enslaved to the VXLAN bridge.
            # access: routed host port, needs an address and a VRF.
            overlay_interfaces = []
            access_interfaces = []
            for if_name, intf in dev.get("interfaces", {}).items():
                if intf.get("role") == "oob" and intf.get("ip"):
                    oob_interface, oob_ip = if_name, intf["ip"]
                elif intf.get("role") in ("fabric", "backbone") and intf.get("ip"):
                    # Ring links are numbered like fabric links.
                    fabric_interfaces.append((if_name, intf["ip"]))
                elif intf.get("role") == "overlay":
                    overlay_interfaces.append(if_name)
                elif intf.get("role") == "access" and intf.get("ip"):
                    access_interfaces.append((if_name, intf["ip"]))

            if oob_interface and ipaddress.ip_interface(oob_ip).ip in US_EAST_01A_OOB_NETWORK:
                server_host = ZTP_SERVER_HOST
            elif oob_interface:
                server_host = site_server_host(oob_ip)
            else:
                server_host = ZTP_SERVER_CONTAINER_NAME

            bootstrap_template = TEMPLATE_ENV.get_template("ztp_frr.py.j2")
            bootstrap_content = bootstrap_template.render(
                server_host=server_host,
                serial=serial,
                oob_interface=oob_interface,
                oob_ip=oob_ip,
                fabric_interfaces=fabric_interfaces,
                overlay_interfaces=overlay_interfaces,
                access_interfaces=access_interfaces,
                loopback=dev.get("loopback"),
                overlay_vni=dev.get("overlay_vni"),
                overlay_vlan_id=dev.get("overlay_vlan_id"),
                overlay_test_ip=dev.get("overlay_test_ip"),
                l3vni=dev.get("l3vni"),
                vrf_name=dev.get("vrf_name"),
                sr_policies=dev.get("sr_policies"),
            )
            bootstrap_path = device_dir / "ztp_bootstrap.py"
            bootstrap_path.write_text(bootstrap_content)
            binds[name].append(f"{bootstrap_path.resolve()}:{FRR_BOOTSTRAP_CONTAINER_PATH}")
            daemons_path = FRR_DAEMONS_BBR_HOST_PATH if dev.get("role") == "bbr" else FRR_DAEMONS_HOST_PATH
            binds[name].append(f"{daemons_path.resolve()}:/etc/frr/daemons")
            # Detached so exec: does not block the node worker.
            exec_cmds[name] = [
                f"sh -c 'setsid python3 {FRR_BOOTSTRAP_CONTAINER_PATH} > {FRR_BOOTSTRAP_LOG_PATH} 2>&1 &'"
            ]

    return binds, exec_cmds


def generate_topology(data, output_path, identity_dir, lab_name="us-east-01a"):
    devices = data["devices"]
    errors = validate_data_model(data)
    if errors:
        raise ValueError("data model invalid:\n" + "\n".join(errors))

    identity_binds, srlinux_exec_cmds = write_identity_files(devices, identity_dir)
    dnsmasq_confs = write_dnsmasq_configs(devices, identity_dir)

    nodes = {}
    for name, dev in devices.items():
        platform = dev.get("platform", "linux")
        image = dev.get("vendor_image") or PLATFORM_IMAGE.get(platform, PLATFORM_IMAGE["linux"])
        if platform == "frr":
            if image not in FRR_SSH_IMAGES:
                raise ValueError(
                    f"{name}: no SSH-enabled build for FRR image {image!r} - add it to "
                    "FRR_SSH_IMAGES and build it from images/frr/Dockerfile"
                )
            image = FRR_SSH_IMAGES[image]
        node = {"kind": PLATFORM_KIND.get(platform, "linux"), "image": image}
        if name in identity_binds:
            node["binds"] = identity_binds[name]
        if name in srlinux_exec_cmds:
            node["exec"] = srlinux_exec_cmds[name]
        # Suppress startup-config so cEOS enters ZTP mode.
        if name in identity_binds and platform == "ceos":
            node["suppress-startup-config"] = True
        # ovs-bridge: pure L2 switch, no OS or container boot.
        if dev.get("role") == "mgmt-switch":
            node["kind"] = "ovs-bridge"
            node.pop("image", None)
        # Hosts: assign IPs via exec:, plus a default route if set.
        if dev.get("role") == "host":
            node["image"] = HOST_IMAGE
            exec_cmds = [
                f"ip addr add {intf['ip']} dev {if_name}"
                for if_name, intf in dev.get("interfaces", {}).items()
                if intf.get("ip")
            ]
            if dev.get("default_gw"):
                # replace, not add: Docker already sets a default route.
                exec_cmds.append(f"ip route replace default via {dev['default_gw']}")
            if exec_cmds:
                node["exec"] = exec_cmds
        # Servers: IPs are not applied by any config, so set them here.
        if dev.get("role") == "server":
            exec_cmds = [
                f"ip addr add {intf['ip']} dev {if_name}"
                for if_name, intf in dev.get("interfaces", {}).items()
                if intf.get("ip")
            ]
            if exec_cmds:
                node["exec"] = exec_cmds
        if dev.get("role") == "server":
            # Optional secrets, bound only if present on disk.
            server_binds = []
            if name in dnsmasq_confs:
                server_binds.append(
                    f"{dnsmasq_confs[name].resolve()}:{DNSMASQ_SITE_CONF_CONTAINER_PATH}:ro"
                )
            deploy_key = SECRETS_REPO_PATH / "deploy" / "ztp_server_deploy_key"
            known_hosts = SECRETS_REPO_PATH / "deploy" / "known_hosts"
            users_yaml = SECRETS_REPO_PATH / "users.yaml"
            if deploy_key.exists() and known_hosts.exists():
                server_binds.append(f"{deploy_key.resolve()}:/root/.ssh_deploy_key:ro")
                server_binds.append(f"{known_hosts.resolve()}:/root/.ssh_known_hosts:ro")
            if users_yaml.exists():
                server_binds.append(f"{users_yaml.resolve()}:/root/secrets/users.yaml:ro")
            if server_binds:
                node["binds"] = server_binds
        # Pin the containerlab mgmt IP recorded in NetBox.
        if dev.get("mgmt_ip"):
            node["mgmt-ipv4"] = dev["mgmt_ip"].split("/")[0]
        nodes[clab_node_name(name, dev.get("role"))] = node

    topology = {
        "name": lab_name,
        # Empty prefix: container names match device names.
        "prefix": "",
        "topology": {"nodes": nodes, "links": build_links(devices)},
    }

    # Pin the mgmt network subnet, derived from a device mgmt_ip.
    mgmt_ips = [dev["mgmt_ip"] for dev in devices.values() if dev.get("mgmt_ip")]
    if lab_name in LAB_MGMT:
        topology["mgmt"] = dict(LAB_MGMT[lab_name])
    elif mgmt_ips:
        network = ipaddress.ip_interface(mgmt_ips[0]).network
        topology["mgmt"] = {"network": "clab", "ipv4-subnet": str(network)}

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.dump(topology, sort_keys=False))
    return topology


def main(argv=None):
    """CLI entry point. Also used by `ztp build`."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/netbox-export.json")
    parser.add_argument("--check", action="store_true", help="validate only, don't write files")
    args = parser.parse_args(argv)

    with open(args.data) as f:
        data = json.load(f)

    errors = validate_data_model(data)
    if errors:
        print("Data model validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.check:
        print("Data model valid. (--check: not writing topology files)")
        return 0

    for lab_name, lab_devices in split_devices_by_lab(data["devices"]).items():
        output_path = Path(f"topology/{lab_name}.clab.yml")
        generate_topology(
            {**data, "devices": lab_devices}, output_path, Path("topology/identity"), lab_name
        )
        print(f"Wrote {output_path} ({len(lab_devices)} devices)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
