"""The `ztp` console script for managing the ztp-server container."""

import argparse
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

# Web service output goes to a dedicated log file.
LOG_PATH = Path("/var/log/ztp-app.log")

# dnsmasq is restarted by entrypoint.sh's respawn loop.
DHCP_CONF_PATH = Path("/app/dhcp/dnsmasq.conf")
DHCP_LEASE_PATH = Path("/var/lib/misc/dnsmasq.leases")


def _cmd_generate_data(args):
    from netbox.export_data_model import main as export_data_main

    return export_data_main([])


def _inside_a_container():
    return Path("/.dockerenv").exists() or bool(os.environ.get("ZTP_FORCE_REBUILD"))


def _signal_pid_1(sig, action_description):
    # Shared by rebuild and stop; signals PID 1 (the supervisor).
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
    # Soft reset: restart only the FastAPI process.
    return _signal_pid_1(
        signal.SIGHUP, "Soft reset: sending SIGHUP to PID 1 to restart the FastAPI process"
    )


def _cmd_stop(args):
    # Full stop: exit the supervisor so the container stops.
    return _signal_pid_1(signal.SIGTERM, "Stopping: sending SIGTERM to PID 1 to stop the container")


def _spawn_uvicorn(host, port):
    log_file = open(LOG_PATH, "a")
    proc = subprocess.Popen(
        ["uvicorn", "ztp_server.main:app", "--host", host, "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    # The child process holds its own copy of the fd.
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
    # Scan /proc directly; no pgrep dependency.
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
    """Run uvicorn as a child process so this process stays PID 1."""
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
