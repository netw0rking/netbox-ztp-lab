"""Host-side SR-MPLS setup, run after containerlab deploy."""
import json
import subprocess
import sys

MPLS_PLATFORM_LABELS = 1048575  # 2^20 - 1


def find_backbone_interfaces(data):
    """{device_name: [if_name, ...]} for each bbr backbone interface."""
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
    """Load MPLS kernel modules if not already loaded."""
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
    """device_interfaces: {device_name: [if_name, ...]}."""
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
