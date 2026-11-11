"""Host-side deploy helper, run before `containerlab deploy` - NOT part of
the containerized `ztp` CLI (that project deliberately has no `build`
subcommand: a real topology deploy needs the Docker socket + containerlab
itself inside the container, see ztp_cli/cli.py's own docstring).

containerlab's `ovs-bridge` kind attaches to an *existing* Open vSwitch
bridge - it does not create one (confirmed live: `containerlab deploy`
fails outright with "Bridge ... referenced in topology but does not exist"
otherwise). Bridge names are derived from whatever topology file is about
to be deployed, never hardcoded - a renamed site or a new multi-site lab
needs no changes here, just a different topology file on the command line.

Requires a NOPASSWD sudoers rule for `ovs-vsctl` specifically (not full
sudo).
"""
import subprocess
import sys

import yaml


def find_ovs_bridge_names(topology):
    """Every node name in the topology whose kind is ovs-bridge, in the
    order they appear. dict iteration order is insertion order (guaranteed
    since Python 3.7), which is also PyYAML's load order - deterministic,
    not sorted, so output order matches the topology file's own order."""
    nodes = topology.get("topology", {}).get("nodes", {})
    return [name for name, node in nodes.items() if node.get("kind") == "ovs-bridge"]


def ensure_bridges(names, run=subprocess.run):
    """Idempotent: `ovs-vsctl br-exists` first (exit 2 means missing, per
    ovs-vsctl's own documented exit codes), only `add-br` when actually
    absent - so this is safe to run on every deploy, not just the first
    one, without erroring on a bridge that's already there."""
    for name in names:
        result = run(["sudo", "ovs-vsctl", "br-exists", name])
        if result.returncode != 0:
            print(f"Creating missing OVS bridge: {name}", file=sys.stderr)
            run(["sudo", "ovs-vsctl", "add-br", name], check=True)
        else:
            print(f"OVS bridge already exists: {name}", file=sys.stderr)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: ensure_ovs_bridges.py <topology.clab.yml>", file=sys.stderr)
        return 1
    with open(argv[0]) as f:
        topology = yaml.safe_load(f)
    names = find_ovs_bridge_names(topology)
    if not names:
        print("No ovs-bridge nodes in this topology - nothing to do.", file=sys.stderr)
        return 0
    ensure_bridges(names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
