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
# The separate, private netbox-ztp-lab-secrets repo - never a subdirectory
# of this repo, never committed here. Cloned once, out of band, to this
# fixed sibling path (matching where this repo's own plain checkout
# already lives). The one-time deploy-key setup this needs is done out of
# band. Overridable via env var
# for anyone whose checkout layout differs.
SECRETS_REPO_PATH = Path(
    os.environ.get("SECRETS_REPO_PATH", str(Path.home() / "git" / "netbox-ztp-lab-secrets"))
)


# Genuine DHCP-triggered ZTP is unreachable for SRLinux in containerlab -
# confirmed live that its ztpd daemon never runs at all (not started by
# systemd - PID 1 is `tini`, not systemd - and absent from sr_app_mgr's
# own process manifest). Real ZTP is gated by a GRUB-level auto-boot flag
# read from the kernel command line, a hardware/VM boot-time mechanism
# with no per-container equivalent (see ztp_srlinux.py.j2's own header
# comment for the full root cause). Fixed by rendering that exact
# template directly here and triggering it via containerlab's own
# `exec:` directive instead - same fetch-and-apply outcome, different
# trigger. ZTP_SERVER_HOST mirrors main.py's own default of the same name.
ZTP_SERVER_HOST = "10.0.0.130"
# us-east-01a's own OOB space - the boundary that scopes the last-
# usable-IP derivation below away from this lab's devices. Note: us-east-01a's own server IP
# (10.0.0.130) is not its own subnet's last usable address, and never
# will be - it predates this convention and stays as-is.
US_EAST_01A_OOB_NETWORK = ipaddress.ip_network("10.0.0.0/24")
# Fallback for devices with no OOB interface of their own. containerlab
# injects a working /etc/hosts entry for every node into every other
# node, resolvable on the shared containerlab Docker mgmt network -
# confirmed live (`docker exec <any-node> cat /etc/hosts` showed this
# hostname mapped to ztp-server's container-mgmt IP). This is us-east-
# 01a's own fallback only, for a device on that lab with no OOB
# interface of its own. Every backbone-fabric-lab site now has its own
# OOB network and its own ztp-server, so every device there
# always has a real oob_interface and never reaches this branch.
ZTP_SERVER_CONTAINER_NAME = "ztp-server-us-east-01a"


def site_server_host(oob_ip):
    """Last usable address of oob_ip's own subnet - the static address
    every backbone-fabric-lab site's own ztp-server device is given in
    NetBox, independently derived
    here by every other device in that same site."""
    network = ipaddress.ip_interface(oob_ip).network
    return str(network.broadcast_address - 1)
SRLINUX_PYTHON = "/opt/srlinux/python/virtual-env/bin/python3"
SRLINUX_BOOTSTRAP_CONTAINER_PATH = "/tmp/ztp_bootstrap.py"
FRR_BOOTSTRAP_LOG_PATH = "/tmp/ztp_bootstrap.log"
# Upstream FRR image (NetBox vendor_image, or the platform default) ->
# this repo's SSH-enabled build of the same FRR version
# (images/frr/Dockerfile). Upstream images have no sshd, so without this
# no users.yaml login works on any FRR node.
FRR_SSH_IMAGES = {
    "frrouting/frr:latest": "netbox-ztp-lab-frr:8.4.1",  # Docker Hub, frozen at 8.4.1
    "quay.io/frrouting/frr:10.5.1": "netbox-ztp-lab-frr:10.5.1",
}
# frrouting/frr's own image ships python3 (confirmed live: no curl, but
# wget and python3 are both present) - used here instead of a shell
# script for the same structured-retry-logic reasons SRLinux's bootstrap
# already uses Python over bash.
FRR_BOOTSTRAP_CONTAINER_PATH = "/tmp/ztp_bootstrap.py"
# Bind-mounted over the image's own default /etc/frr/daemons (bgpd=no)
# so bgpd is already running from first boot - see configs/frr-daemons'
# own header comment for why this has to happen before container start
# rather than being toggled live by ztp_bootstrap.py.
FRR_DAEMONS_HOST_PATH = REPO_ROOT / "configs" / "frr-daemons"

# Bound instead of FRR_DAEMONS_HOST_PATH for role == "bbr" devices only -
# ring BBRs need isisd actually running as soon as they get ANY IS-IS
# config (the `router isis 1` block in frr_config.j2), which the
# plain daemons file never enables (isisd=no, always). Gated on role
# alone, not on srv6_locator being set - confirmed live: the ring
# deploy (plain IS-IS + multihop EVPN, no SRv6 yet) failed "show isis
# neighbor" with "isisd is not running" under the plain daemons file.
FRR_DAEMONS_BBR_HOST_PATH = REPO_ROOT / "configs" / "frr-daemons-bbr"

# No dedicated "frr" containerlab kind exists (confirmed live against
# containerlab 0.79.0's own supported-kinds list) - FRR runs as a plain
# "linux" kind container using the frrouting/frr image directly (see
# PLATFORM_IMAGE below), the same kind ztp-server itself uses.
PLATFORM_KIND = {"ceos": "arista_ceos", "srlinux": "nokia_srlinux", "frr": "linux", "linux": "linux"}

# vendor_image has no NetBox field to live in (Tasks 3-4 never gave it one -
# confirmed live: the real NetBox export has no vendor_image
# on any device, since it's deployment/container metadata, not inventory
# data). This is the source of truth for container images; dev.get(...) is
# checked first only so the canonical test fixture's explicit values (which
# match this table) keep working unchanged.
#
# ceos is pinned to 4.32.0F, not the original 4.28.8M - confirmed live
# during live testing that 4.28.8M's front-panel interfaces
# never register with EOS's own Sysdb/Fru layer at all (visible at the
# kernel level, invisible to `show interfaces`) on a host running cgroups
# v2, which containerlab/cEOS's own docs document as a hard requirement
# for any cEOS release before 4.32.0F. 4.32.0F+ auto-detects cgroups v1/v2
# and needs no workaround. This specific image tag (harsha258/ceos) is an
# unofficial third-party republish, not Arista's own registry - it was
# used here only because the user explicitly accepted that provenance
# risk after being told about it; replace with an official
# arista.com-sourced 4.32.0F+ image if/when one is imported the same way
# 4.28.8M originally was (see README).
PLATFORM_IMAGE = {
    "ceos": "harsha258/ceos:4.32.0F",
    "srlinux": "ghcr.io/nokia/srlinux:latest",
    "frr": "frrouting/frr:latest",
    "linux": "netbox-ztp-lab-ztp-server:latest",
}

# "host" role devices have no
# "platform" field at all (they're not a NOS - same shape as ztp-server),
# so they'd otherwise silently fall through PLATFORM_IMAGE's own "linux"
# default straight to ztp-server's own image. alpine's busybox `ip` and
# `ping` applets are enough for the P2P-routed and VXLAN-bridged
# connectivity tests these devices exist for.
HOST_IMAGE = "alpine:latest"

IDENTITY_BIND_PATH = {
    "ceos": "/mnt/flash/ceos-config",
    "srlinux": "/etc/opt/srlinux/ztp/chassis_id_suffix",
}


CEOS_ETHERNET_RE = re.compile(r"^Ethernet(\d+)$")


def clab_endpoint_name(if_name, platform, role=None, dev_name=None):
    """Translate a device's real NOS interface name to the name
    containerlab expects as a link endpoint, where the two differ.

    arista_ceos: containerlab's convention for this kind is ethN at the
    veth/kernel level - cEOS's own boot-time Fru agent watches for kernel
    devices literally named ethN and remaps them to EthernetN internally.
    Confirmed live: an isolated 2-node cEOS link named "eth1" in the
    topology file registered as "Ethernet1 is up, line protocol is up
    (connected)"; the same link named "Ethernet1" directly (this
    function's previous behavior - just passing the real interface name
    through unchanged) left Fru's own enumeration log showing only
    eth0/management, and the front-panel port never appeared in
    `show interfaces status` even across a container restart with the
    link already attached. NetBox interface names for cEOS devices are
    always the plain "EthernetN" form (no sub-interfaces in this data
    model), so the digits carry through unchanged.
    nokia_srlinux: e1-1/e1-2/... already IS containerlab's expected
    endpoint convention for this kind (it matches the real interface
    name directly), so no translation is needed or applied.
    mgmt-switch (ovs-bridge kind): every port on every ovs-bridge node in
    one topology file attaches directly into the ROOT network namespace,
    not a per-node namespace the way every other kind gets - confirmed
    live: two different mgmt-switches each declaring a port named
    "Ethernet2" collided outright ("Root network namespace endpoint
    "Ethernet2" defined by multiple nodes"), the first time more than one
    mgmt-switch was ever deployed at once (this repo had exactly one,
    ever, until this plan's per-site ztp-servers added 4 more). A short,
    deterministic hash of (device name, interface name) is globally
    unique across the whole topology regardless of how many sites reuse
    the same port-numbering convention, and comfortably fits Linux's
    IFNAMSIZ limit - the same approach clab_node_name already uses for
    the node name itself.
    """
    if role == "mgmt-switch":
        digest = hashlib.sha1(f"{dev_name}:{if_name}".encode()).hexdigest()[:8]
        return f"eth-{digest}"
    if platform == "ceos":
        match = CEOS_ETHERNET_RE.match(if_name)
        if match:
            return f"eth{match.group(1)}"
    return if_name


# Linux kernel netdev name limit (IFNAMSIZ 16, minus the null terminator).
OVS_BRIDGE_NAME_MAX = 15


def clab_node_name(name, role):
    """The name containerlab uses for this device, both as the `nodes:`
    mapping key and in every `links:` endpoint referencing it - normally
    identical to the real NetBox device name, except for ovs-bridge-kind
    devices (role "mgmt-switch"), where the node name literally IS the
    real Linux kernel interface name Open vSwitch creates for it.
    Confirmed live: `mgmt-switch-us-east-01a` (24 chars) failed every
    deploy with "failed to query port mgmt-switch-us-east-01a: Invalid
    argument" / "could not add network device ... to ofproto (Invalid
    argument)" - a Linux IFNAMSIZ (15-character) rejection, nothing to do
    with the host's OVS install. containerlab's own docs confirm there is
    no separate bridge-name field to set instead - the node name and the
    OVS bridge name are the same thing.
    """
    if role != "mgmt-switch" or len(name) <= OVS_BRIDGE_NAME_MAX:
        return name
    # sha1, not a hand-rolled abbreviation: this project's own naming
    # convention has every site share a "mgmt-switch-us-" prefix, so a
    # plain truncation would collide across every site's mgmt-switch (see
    # test_clab_node_name_does_not_collide_across_similar_site_names) -
    # deterministic and collision-safe regardless of what naming
    # convention future sites use, with no knowledge of that convention
    # needed here.
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


# FRR has no identity-bind mechanism of its own (no hardware-serial ZTP
# concept the way IDENTITY_BIND_PATH's other two platforms do - cEOS's
# ceos-config file, SRLinux's chassis_id_suffix file) - its serial is
# baked directly into its own rendered bootstrap script's content
# instead (see the "frr" branch below), so it never needs an entry in
# IDENTITY_BIND_PATH itself, only in this set that gates which devices
# this whole function processes at all.
BOOTSTRAPPABLE_PLATFORMS = set(IDENTITY_BIND_PATH) | {"frr"}


# Which containerlab lab each NetBox site belongs to. us-east-01a and the
# backbone-fabric lab are two completely separate labs - one topology
# file (and one containerlab deploy/destroy) each, never one combined
# file. Until 2026-10-04 this script wrote both into a single
# clos-fabric.clab.yml, so every redeploy of one lab also tore down and
# rebuilt the other.
LAB_SITES = {
    "us-east-01a": ("us-east-01a",),
    "backbone-fabric": (
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
    ),
}
# Each lab's own containerlab mgmt network, so the two never share
# Docker's address pool. us-east-01a derives its subnet from its pinned
# mgmt_ip values below (the original "clab" network, unchanged);
# backbone-fabric pins none, so it gets a dedicated network here.
# ipv6-subnet is explicit because containerlab would otherwise reuse its
# default 3fff:172:20:20::/64, already claimed by "clab".
LAB_MGMT = {
    "backbone-fabric": {
        "network": "clab-backbone-fabric",
        "ipv4-subnet": "172.20.21.0/24",
        "ipv6-subnet": "3fff:172:20:21::/64",
    },
}
SITE_LAB = {site: lab for lab, sites in LAB_SITES.items() for site in sites}
# Every device name ends in its NetBox site slug (<role>-<nn>-<site>,
# e.g. bbr-01-us-central-01a) - the export carries no per-device site
# field, so the site is read from the name.
DEVICE_SITE_RE = re.compile(r"-([a-z]{2}-[a-z]+-\d{2}[a-z])$")


def split_devices_by_lab(devices):
    """{lab name: {device name: device}}. Raises on a device whose site
    isn't mapped to a lab, or a connects_to cable between two labs."""
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


# us-east-01a's DHCP settings, carried over verbatim from the original
# static dhcp/dnsmasq.conf - the only site with real DHCP-ZTP clients
# (its cEOS leafs). Every other site's server serves no DHCP at all.
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
    """Render one dnsmasq config per "server" role device, scoped to its
    own OOB subnet. Confirmed live 2026-10-03: every per-site ztp-server
    was loading the single static dhcp/dnsmasq.conf, offering
    us-east-01a's 10.0.0.140-199 range on its own unrelated subnet.
    Returns {server name: rendered file Path}."""
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
            # Absolute path: containerlab resolves relative bind paths
            # against the topology file's own directory, not the CWD the
            # script ran from - confirmed live (a naive path here produced
            # a doubled "topology/topology/..." lookup and a deploy
            # failure). topology/ is generated, gitignored output, never
            # expected to be portable across hosts, so an absolute path
            # is the robust choice.
            binds[name] = [f"{file_path.resolve()}:{IDENTITY_BIND_PATH[platform]}"]
        else:
            binds[name] = []

        if platform == "srlinux":
            # See this module's ZTP_SERVER_HOST comment above - genuine
            # DHCP-triggered ZTP never reaches this script for SRLinux, so
            # it's rendered and bound in here, triggered via `exec:`
            # instead of relying on ztpd to ever fetch/run it itself.
            #
            # Real ZTP would also be what assigns this device's OOB-facing
            # interface an IP in the first place (via its own DHCP client) -
            # since that daemon never runs, the interface has no address at
            # all otherwise. Confirmed live: the very first version of this
            # exec: bootstrap failed every one of its 10 retries to reach
            # ztp-server, because e1-1 had never been given an address by
            # anything. Fixed the same way ztp-server's own OOB IP is
            # assigned (a static address, sourced from NetBox rather than
            # DHCP) - find this device's oob-role interface and give the
            # script its name + address so it can configure it via sr_cli
            # before ever attempting to fetch anything.
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
            # No dedicated containerlab kind, no ZTP mechanism of its own,
            # no DHCP client in the image - same "exec:-triggered
            # bootstrap does everything" shape as SRLinux above, but the
            # device's serial is baked directly into the rendered script
            # itself (no chassis-identity file/API to inject it via, the
            # way cEOS's ceos-config / SRLinux's chassis_id_suffix work) -
            # see ztp_frr.py.j2's own header comment.
            oob_interface = oob_ip = None
            fabric_interfaces = []
            # "overlay" role: an L2-bridged host-facing port (leaf-02's
            # eth4 - see the L2 branch in ztp_frr.py.j2), just needs
            # enslaving into the existing VXLAN bridge, no address of its
            # own. "access" role: a routed EVPN Type-5 host-facing P2P
            # link (leaf-06/07's eth4), needs its own address AND
            # enslaving into vrf_name - see the L3VNI branch there.
            overlay_interfaces = []
            access_interfaces = []
            for if_name, intf in dev.get("interfaces", {}).items():
                if intf.get("role") == "oob" and intf.get("ip"):
                    oob_interface, oob_ip = if_name, intf["ip"]
                elif intf.get("role") in ("fabric", "backbone") and intf.get("ip"):
                    # "backbone" (ring) interfaces need the exact same
                    # `ip addr add` treatment as "fabric" ones - both are
                    # numbered P2P links, just without per-link BGP
                    # peering (that's the actual distinction rendered in
                    # frr_config.j2, which reads role directly and is
                    # unaffected by this list). Confirmed live: after
                    # export_data_model.py started correctly classifying
                    # ring links as "backbone", they matched none of
                    # this loop's four branches at all, so ztp_frr.py.j2
                    # never assigned them an address - IS-IS hellos (and
                    # even a plain ping) had no chance over an unnumbered
                    # link.
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
            # Detached via setsid, not a foreground exec:. containerlab
            # runs exec: synchronously inside the worker that creates the
            # node, and this bootstrap retries for up to 30 minutes until
            # its site's ztp-server answers - so a foreground run blocks
            # the very workers that would go on to create that server.
            # Confirmed live 2026-10-04: a backbone-fabric deploy stalled
            # at 9/22 nodes, near-zero load, with ESRs waiting on a
            # ztp-server-us-central-01a that no free worker could create;
            # the "~17 min exec queue" PR #44 widened the retry budget for
            # was this same stall. Detached, exec: returns immediately
            # (verified live: 0.13s, process survives and completes) and
            # the bootstrap - idempotent since PR #45 - finishes on its
            # own once its server is up. Results land in
            # /tmp/ztp_bootstrap.log inside the node, not the deploy log.
            # (Daemon restarts are still never done here - see
            # ztp_frr.py.j2's `vtysh -f` comment for why that killed PID 1.)
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
        # ZTP'd cEOS devices (spine/leaf's cEOS members - identified by
        # having an identity bind, same set the original BBR work targeted)
        # need containerlab to NOT write its own default startup-config,
        # or real EOS never enters ZeroTouch mode at all - confirmed live
        # during live testing (containerlab's docs literally
        # call this out as the flag for "Zero-Touch Provisioning labs").
        # mgmt-switch is excluded even though it's also cEOS: it has no
        # identity bind (not ZTP'd) and needs its real static config.
        if name in identity_binds and platform == "ceos":
            node["suppress-startup-config"] = True
        # mgmt-switch is a pure L2 forwarding plane, nothing more -
        # containerlab's native `ovs-bridge` kind covers that with no OS/
        # container boot at all, in place of the arista_ceos VM this
        # previously ran as (which cost real CPU/RAM just to switch
        # frames, and needed its own static-config + post-boot admin-SSH-
        # merge workaround below to be usable at all). Trade-off: an
        # ovs-bridge has no CLI/OS of its own, so SSH/admin access to the
        # switch itself is gone - traded away deliberately here for the
        # capacity to run leaf-06/leaf-07 + hosts instead. `mgmt-ipv4`
        # doesn't apply either (no container backing it, so it's left
        # unset for this device by the mgmt_ip check above never firing -
        # this device's fixture entry has no mgmt_ip at all).
        if dev.get("role") == "mgmt-switch":
            node["kind"] = "ovs-bridge"
            node.pop("image", None)
        # "host" role devices (leaf-01/02's L2-bridged VXLAN test hosts,
        # leaf-06/07's routed EVPN Type-5 test hosts)
        # aren't a NOS either: same "no applied config, so assign this
        # device's own interface IPs via exec:" shape as the "server" role
        # below, plus a default route toward the leaf for the routed
        # (P2P/"access") hosts - the L2-bridged ones (interface role
        # "overlay") have no gateway to route through, they're directly
        # on the VXLAN-stretched broadcast domain, so `default_gw` is
        # simply absent from their fixture entry.
        if dev.get("role") == "host":
            node["image"] = HOST_IMAGE
            exec_cmds = [
                f"ip addr add {intf['ip']} dev {if_name}"
                for if_name, intf in dev.get("interfaces", {}).items()
                if intf.get("ip")
            ]
            if dev.get("default_gw"):
                # replace, not add: every container already has Docker's
                # own default route via eth0, so "add" fails with "File
                # exists" and the host never routes via its leaf.
                exec_cmds.append(f"ip route replace default via {dev['default_gw']}")
            if exec_cmds:
                node["exec"] = exec_cmds
        # "server" role devices (currently just ztp-server) aren't a NOS -
        # nothing ever applies their own rendered config, so a static "ip"
        # on one of their interfaces (e.g. ztp-server's OOB address) is
        # never actually assigned anywhere else. Confirmed live: without
        # this, ztp-server's eth1 had its MAC but no IP at all, so nothing
        # on the OOB network could ever reach it. ZTP'd devices (spine/
        # leaf) get their interface IPs from their own applied config
        # instead, so this only applies to "server" role devices.
        if dev.get("role") == "server":
            exec_cmds = [
                f"ip addr add {intf['ip']} dev {if_name}"
                for if_name, intf in dev.get("interfaces", {}).items()
                if intf.get("ip")
            ]
            if exec_cmds:
                node["exec"] = exec_cmds
        if dev.get("role") == "server":
            # ztp-server's GitOps cron-pull (see deploy/entrypoint.sh)
            # needs a read-only GitHub deploy key + known_hosts, and the
            # rendered cEOS config's admin login needs the structured
            # users list (see ceos_config.j2, main.py's USERS_YAML_PATH)
            # - all three now live in the separate, private
            # netbox-ztp-lab-secrets repo (see SECRETS_REPO_PATH above),
            # not this repo's own deploy/ or .env. Each is optional and
            # only bound if present on disk - a missing bind source fails
            # the whole containerlab deploy outright, and none of these
            # files are guaranteed to exist on a fresh clone (the secrets
            # repo may not even be cloned locally at all).
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
        # containerlab's own Docker-managed mgmt network (what
        # `containerlab inspect` shows per-node, distinct from this
        # design's own fabric OOB network) is otherwise dynamically
        # assigned by Docker's IPAM on every redeploy - not recorded
        # anywhere and not guaranteed stable across redeploys. NetBox now
        # models this too (see the container-mgmt Prefix), so every
        # device with a recorded mgmt_ip gets it pinned statically here.
        if dev.get("mgmt_ip"):
            node["mgmt-ipv4"] = dev["mgmt_ip"].split("/")[0]
        nodes[clab_node_name(name, dev.get("role"))] = node

    topology = {
        "name": lab_name,
        # Without this, containerlab names every container
        # "clab-<lab name>-<name>" by default - confirmed via
        # containerlab's own topology-definition-file docs: an empty
        # string here (as opposed to leaving the field out entirely, or
        # the "__lab-name" magic value, which would still prefix
        # "<lab name>-<name>") drops all prefixing, giving containers
        # exactly the same name as this data model's own device names
        # (e.g. "spine-01-us-east-01a"), matching what NetBox and every
        # device's own LLDP/hostname already show.
        "prefix": "",
        "topology": {"nodes": nodes, "links": build_links(devices)},
    }

    # Pin the mgmt network's own subnet too, not just each node's address
    # within it - otherwise containerlab would pick its own subnet (which
    # may not match what NetBox recorded) independently of the per-node
    # mgmt-ipv4 addresses above. Derived from any one device's mgmt_ip
    # rather than hardcoded, so this stays in sync with NetBox automatically.
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
    """CLI entry point - also what `ztp build` (see ztp_cli/cli.py) calls
    directly, rather than shelling out to this file as a subprocess."""
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
