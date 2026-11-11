"""Host-side deploy helper for SR-MPLS (NOT part of the containerized
`ztp` CLI). Unlike scripts/ensure_ovs_bridges.py, this one must be run
AFTER `containerlab deploy`, not before - it ensures two things this
project's images don't carry by default, and one of them needs the
containers to already exist:

1. The host kernel's mpls_router/mpls_iptunnel modules - host-wide,
   container-independent. Confirmed live: `modinfo`/
   `modprobe` both work cleanly on this host's kernel
   (6.8.0-142-generic), no reboot needed.
2. Per-container sysctls (net.mpls.platform_labels, and
   net.mpls.conf.<if>.input=1 on every backbone-role interface), set
   via `docker exec <device> sysctl ...` - this is why the script as a
   whole can only run AFTER `containerlab deploy`: there's no
   container for `docker exec` to reach into until deploy creates one.
   Each container gets its OWN independent copy of the net.mpls.*
   sysctl tree once the host module is loaded (confirmed live: a
   disposable test netns had working net.mpls.* sysctls immediately
   after the host-level modprobe). Without these, FRR's isisd/zebra
   can have segment-routing fully configured and still never actually
   program a kernel MPLS route - confirmed live on a real
   two-BBR test: segment-routing enabled correctly on both ends (SRGB
   allocated, visible via `show isis segment-routing node`), but a
   third, non-adjacent BBR's route to the SR-enabled loopback stayed
   plain IP with no mpls encapsulation, consistent with the per-
   container sysctls still being unset everywhere in that test.

main() runs both steps in that order on every invocation - the
modprobe step is idempotent (a no-op once the host modules are
loaded), so re-running it after deploy alongside the sysctl step costs
nothing. The per-container sysctls are runtime netns state: they don't
survive a container being recreated, so this script needs to be
re-run after EVERY `containerlab deploy` of this lab, not just the
first one ever.

"""
import json
import subprocess
import sys

MPLS_PLATFORM_LABELS = 1048575  # 2^20 - 1, the practical max - confirmed live


def find_backbone_interfaces(data):
    """{device_name: [if_name, ...]} for every role=='bbr' device's own
    role=='backbone' interfaces - scoped to exactly what SR-MPLS forwarding
    needs, not every interface on the box (oob/fabric interfaces don't
    carry backbone-ring traffic and don't need this)."""
    result = {}
    for name, device in data.get("devices", {}).items():
        if device.get("role") != "bbr":
            continue
        backbone_ifs = [
            if_name
            for if_name, intf in device.get("interfaces", {}).items()
            if intf.get("role") == "backbone"
        ]
        if backbone_ifs:
            result[name] = backbone_ifs
    return result


def ensure_mpls_modules(run=subprocess.run):
    """Idempotent: checks lsmod-equivalent output first, only modprobes
    a module actually missing - same "check before mutate" shape as
    ensure_ovs_bridges.ensure_bridges's own br-exists check."""
    loaded = run(["lsmod"], capture_output=True, text=True).stdout
    for module in ("mpls_router", "mpls_iptunnel"):
        if module in loaded:
            print(f"{module} already loaded", file=sys.stderr)
        else:
            print(f"Loading missing kernel module: {module}", file=sys.stderr)
            run(["sudo", "modprobe", module], check=True)


def _read_sysctl(container, name, run):
    result = run(["docker", "exec", container, "sysctl", "-n", name], capture_output=True, text=True)
    return result.stdout.strip()


def ensure_mpls_sysctls(device_interfaces, run=subprocess.run):
    """device_interfaces: {device_name: [if_name, ...]}, from
    find_backbone_interfaces. Idempotent per sysctl, same shape as
    ensure_mpls_modules: read current value, skip the write if it
    already matches."""
    for device, interfaces in device_interfaces.items():
        current = _read_sysctl(device, "net.mpls.platform_labels", run)
        if current != str(MPLS_PLATFORM_LABELS):
            run(
                ["docker", "exec", device, "sysctl", "-w",
                 f"net.mpls.platform_labels={MPLS_PLATFORM_LABELS}"],
                check=True,
            )
        else:
            print(f"{device}: net.mpls.platform_labels already set", file=sys.stderr)

        for if_name in interfaces:
            sysctl_name = f"net.mpls.conf.{if_name}.input"
            current = _read_sysctl(device, sysctl_name, run)
            if current != "1":
                run(["docker", "exec", device, "sysctl", "-w", f"{sysctl_name}=1"], check=True)
            else:
                print(f"{device}/{if_name}: {sysctl_name} already set", file=sys.stderr)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: ensure_mpls_enabled.py <netbox-export.json>", file=sys.stderr)
        return 1
    with open(argv[0]) as f:
        data = json.load(f)
    device_interfaces = find_backbone_interfaces(data)
    if not device_interfaces:
        print("No bbr-role devices with backbone interfaces - nothing to do.", file=sys.stderr)
        return 0
    ensure_mpls_modules()
    ensure_mpls_sysctls(device_interfaces)
    return 0


if __name__ == "__main__":
    sys.exit(main())
