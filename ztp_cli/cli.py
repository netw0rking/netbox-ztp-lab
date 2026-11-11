"""The `ztp` console-script - one packaged CLI in place of running loose
scripts directly (`python3 -m netbox.export_data_model`, a raw
`exec uvicorn ...`).

Every subcommand is about the ztp-server's own process (and the dnsmasq
service it depends on for ZTP to work at all) - none of them touch the
separate containerlab lab topology (that stays a plain
`generate_topology.py`, run by whoever manages the lab, entirely outside
this package's CLI.

No `build` subcommand: a genuine "stop the old container, rm it and its
image, rebuild from the current checkout, start a new one" needs the
Docker socket mounted into this container AND containerlab itself
installed in it (a hand-rolled `docker run` would not correctly restore
this node's veth/OOB wiring the way `containerlab deploy --node-filter
... --reconfigure` does) - deliberately deferred rather than shipped half-right.
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

# Where the web service's own stdout/stderr go (see _spawn_uvicorn) -
# deliberately its own file rather than reusing the container's stdout,
# so `ztp logs` can tail just this service's output without sshd/cron/
# dnsmasq noise mixed in. Trade-off: this output no longer appears in
# `docker logs`/`containerlab logs` for this container.
LOG_PATH = Path("/var/log/ztp-app.log")

# dnsmasq is what actually makes ZTP work (cEOS/SRLinux find this
# ztp-server at all via DHCP options 67/239 - see dhcp/dnsmasq.conf) -
# entrypoint.sh runs it in a `while true; do dnsmasq ...; sleep 5; done`
# self-healing loop, so `ztp dhcp-restart` doesn't need any new
# supervision code of its own: killing the current process is enough,
# the loop already respawns it.
DHCP_CONF_PATH = Path("/app/dhcp/dnsmasq.conf")
DHCP_LEASE_PATH = Path("/var/lib/misc/dnsmasq.leases")


def _cmd_generate_data(args):
    from netbox.export_data_model import main as export_data_main

    return export_data_main([])


def _inside_a_container():
    return Path("/.dockerenv").exists() or bool(os.environ.get("ZTP_FORCE_REBUILD"))


def _signal_pid_1(sig, action_description):
    # Shared by rebuild and stop - both work by signaling PID 1 (the
    # `ztp start` supervisor, see _supervise below), just with different
    # signals/handlers. Refuses outside a container so this can't
    # accidentally signal a real machine's actual init process.
    if not _inside_a_container():
        print(
            "refusing to signal PID 1 - this doesn't look like a container "
            "(no /.dockerenv). Set ZTP_FORCE_REBUILD=1 to override.",
            file=sys.stderr,
        )
        return 1
    print(f"{action_description}...", file=sys.stderr)
    os.kill(1, sig)
    return 0


def _cmd_rebuild(args):
    # Soft reset: restart just the FastAPI process, in place, without
    # exiting the container - sshd/cron/dnsmasq are untouched, and
    # nothing needs re-validating since it's the same data the service
    # was already running against.
    return _signal_pid_1(
        signal.SIGHUP, "Soft reset: sending SIGHUP to PID 1 to restart the FastAPI process"
    )


def _cmd_stop(args):
    # Full stop: terminates the uvicorn child and exits the supervisor
    # itself (PID 1), so the container stops - unlike SIGHUP/rebuild,
    # there's no respawn. Also doubles as the fix for `docker stop`/
    # `containerlab stop` on this container: PID 1 inside a container is
    # immune to a signal's default disposition unless it installs a
    # handler, so without this, SIGTERM from `docker stop` was a no-op
    # until Docker's grace period expired and it escalated to SIGKILL.
    return _signal_pid_1(signal.SIGTERM, "Stopping: sending SIGTERM to PID 1 to stop the container")


def _spawn_uvicorn(host, port):
    log_file = open(LOG_PATH, "a")
    proc = subprocess.Popen(
        ["uvicorn", "ztp_server.main:app", "--host", host, "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    # Popen dup()'d the fd into the child - our copy isn't needed after
    # that, and leaving it open would leak one fd per restart/rebuild.
    log_file.close()
    return proc


def _cmd_logs(args):
    if not LOG_PATH.exists():
        print(
            f"No logs yet - {LOG_PATH} doesn't exist. The service hasn't logged anything.",
            file=sys.stderr,
        )
        return 1
    tail_cmd = ["tail", "-n", str(args.lines)]
    if args.follow:
        tail_cmd.append("-f")
    tail_cmd.append(str(LOG_PATH))
    return subprocess.call(tail_cmd)


def _find_dnsmasq_pids(proc_dir="/proc"):
    # Scanning /proc directly rather than shelling out to `pgrep` -
    # portable (no extra package needed in the image) and trivially
    # testable against a fake /proc-shaped directory.
    pids = []
    for entry in os.listdir(proc_dir):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_dir, entry, "cmdline"), "rb") as f:
                cmdline = f.read()
        except OSError:
            continue
        argv0 = cmdline.split(b"\x00", 1)[0].decode(errors="replace")
        if os.path.basename(argv0) == "dnsmasq":
            pids.append(int(entry))
    return pids


def _parse_dhcp_conf(conf_path):
    interface = None
    dhcp_range = None
    try:
        lines = conf_path.read_text().splitlines()
    except OSError:
        return None, None
    for line in lines:
        if line.startswith("interface="):
            interface = line.split("=", 1)[1]
        elif line.startswith("dhcp-range="):
            dhcp_range = line.split("=", 1)[1]
    return interface, dhcp_range


def _read_dhcp_leases(lease_path):
    try:
        return [line for line in lease_path.read_text().splitlines() if line]
    except OSError:
        return []


def _cmd_dhcp_status(args):
    pids = _find_dnsmasq_pids()
    if pids:
        print(f"dnsmasq: running (pid {pids[0]})")
    else:
        print("dnsmasq: NOT running")
    interface, dhcp_range = _parse_dhcp_conf(DHCP_CONF_PATH)
    print(f"interface: {interface or 'unknown'}")
    print(f"range: {dhcp_range or 'unknown'}")
    leases = _read_dhcp_leases(DHCP_LEASE_PATH)
    print(f"leases: {len(leases)}")
    for lease in leases:
        print(f"  {lease}")
    return 0


def _cmd_dhcp_restart(args):
    pids = _find_dnsmasq_pids()
    if not pids:
        print(
            "dnsmasq is not running - nothing to restart "
            "(check entrypoint.sh's self-healing loop)",
            file=sys.stderr,
        )
        return 1
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    print(
        f"Sent SIGTERM to dnsmasq (pid {', '.join(str(p) for p in pids)}) - "
        "entrypoint.sh's restart loop will respawn it within ~5s.",
        file=sys.stderr,
    )
    return 0


def _supervise(host, port, restart_requested=None, stop_requested=None, poll_interval=1.0):
    """Run uvicorn as a child process (not exec'd directly) specifically
    so this process can stay PID 1 and outlive a soft restart - `ztp
    rebuild` signals *this* process, not uvicorn itself, so uvicorn can
    be killed and respawned without the container ever exiting. `ztp
    stop` signals it too, but to exit for good instead of respawning.
    `restart_requested`/`stop_requested` are injectable (pre-built
    threading.Event) purely for tests - real callers always let this
    wire up its own SIGHUP/SIGTERM handlers.
    """
    if restart_requested is None:
        restart_requested = threading.Event()
        signal.signal(signal.SIGHUP, lambda signum, frame: restart_requested.set())
    if stop_requested is None:
        stop_requested = threading.Event()
        signal.signal(signal.SIGTERM, lambda signum, frame: stop_requested.set())

    proc = _spawn_uvicorn(host, port)
    while True:
        if stop_requested.is_set():
            print("Stop requested - terminating the FastAPI process...", file=sys.stderr)
            proc.terminate()
            proc.wait()
            return 0
        if restart_requested.is_set():
            restart_requested.clear()
            print("Soft restart requested - restarting the FastAPI process...", file=sys.stderr)
            proc.terminate()
            proc.wait()
            proc = _spawn_uvicorn(host, port)
            continue
        try:
            return proc.wait(timeout=poll_interval)
        except subprocess.TimeoutExpired:
            continue


def _cmd_start(args):
    print(f"Starting ZTP service on {args.host}:{args.port} ...", file=sys.stderr)
    return _supervise(args.host, args.port)


def build_parser():
    parser = argparse.ArgumentParser(prog="ztp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_data = subparsers.add_parser(
        "generate-data", help="pull the current data model from NetBox, commit + push + open a PR"
    )
    generate_data.set_defaults(func=_cmd_generate_data)

    start = subparsers.add_parser("start", help="run the FastAPI ZTP config-server")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8000)
    start.set_defaults(func=_cmd_start)

    rebuild = subparsers.add_parser(
        "rebuild", help="soft reset - restart just the FastAPI process, in place"
    )
    rebuild.set_defaults(func=_cmd_rebuild)

    stop = subparsers.add_parser("stop", help="stop the FastAPI process and exit the container")
    stop.set_defaults(func=_cmd_stop)

    logs = subparsers.add_parser("logs", help="tail the web service's own log file")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(func=_cmd_logs)

    dhcp_status = subparsers.add_parser(
        "dhcp-status", help="show whether dnsmasq is running, its config, and current leases"
    )
    dhcp_status.set_defaults(func=_cmd_dhcp_status)

    dhcp_restart = subparsers.add_parser(
        "dhcp-restart", help="kill the running dnsmasq - entrypoint.sh's loop respawns it"
    )
    dhcp_restart.set_defaults(func=_cmd_dhcp_restart)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
