import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from schema.validate import validate_data_model
from ztp_server.audit_store import list_renders, record_render
from ztp_server.data_store import find_device_by_serial, load_data_model
from ztp_server.render import _env, render_config
from ztp_server.users import load_users

# Anchored on this file's own location (matching generate_topology.py's
# and export_data_model.py's own REPO_ROOT pattern), not a hardcoded
# "/app/..." - that assumed the container's own WORKDIR, silently
# breaking `ztp start` on a plain dev checkout (confirmed live: a local
# `uv run ztp start` failed its own startup check outright, since
# /app/data/netbox-export.json never exists outside the container).
# Still override-able via the env var for anyone who wants a different
# data model file than the checkout's own.
_REPO_ROOT = Path(__file__).parent.parent
DATA_MODEL_PATH = os.environ.get("DATA_MODEL_PATH", str(_REPO_ROOT / "data" / "netbox-export.json"))
# History of every config actually served over /ztp/config/ (not the
# /debug/config/ alias - see get_device_config_by_name), so a device that
# booted wrong can be diagnosed against what it was really handed, not
# just what render_config() would produce right now. Same repo-root-
# relative default as DATA_MODEL_PATH above, for the same reason.
AUDIT_DB_PATH = os.environ.get("AUDIT_DB_PATH", str(_REPO_ROOT / "data" / "audit.db"))
# Structured device-login credentials (SSH keys / passwords), bind-mounted
# in from the separate, private netbox-ztp-lab-secrets repo - never
# committed to this repo's own git history. Optional: absent on any
# deploy that doesn't mount it, in which case every device's rendered
# config simply has no local users at all (see ztp_server.users.load_users).
USERS_YAML_PATH = os.environ.get("USERS_YAML_PATH", "/root/secrets/users.yaml")

BOOTSTRAP_TEMPLATES = {
    "ceos": "ztp_ceos.sh.j2",
    "srlinux": "ztp_srlinux.py.j2",
    "frr": "ztp_frr.py.j2",
}

app = FastAPI()


@app.on_event("startup")
def check_data_model_exists():
    if not Path(DATA_MODEL_PATH).exists():
        raise RuntimeError(
            f"data model file not found at {DATA_MODEL_PATH} - "
            "has the ztp-server done its first git pull?"
        )


@app.get("/ztp/script/{vendor}", response_class=PlainTextResponse)
def get_bootstrap_script(vendor: str):
    template_name = BOOTSTRAP_TEMPLATES.get(vendor)
    if not template_name:
        raise HTTPException(status_code=404, detail=f"no bootstrap script for vendor '{vendor}'")
    template = _env.get_template(template_name)
    server_host = os.environ.get("ZTP_SERVER_HOST", "10.0.0.130")
    return template.render(server_host=server_host)


def _load_users():
    try:
        return load_users(USERS_YAML_PATH)
    except PermissionError:
        # Confirmed live for the analogous admin-key file this replaced:
        # outside the container (e.g. running tests as a non-root dev
        # user), stat()'ing a path under /root/ raises PermissionError
        # rather than returning False - the container itself runs as
        # root, where this never happens. Either way, "can't confirm the
        # file is there" should mean "no users rendered", not a 500.
        return []


@app.get("/ztp/config/{serial_number}", response_class=PlainTextResponse)
def get_device_config(serial_number: str):
    data = load_data_model(DATA_MODEL_PATH)
    device_name, device = find_device_by_serial(data, serial_number)
    if device_name is None:
        raise HTTPException(
            status_code=404,
            detail=f"no device found for serial_number {serial_number} in data/netbox-export.json",
        )
    config_text = render_config(device_name, data, users=_load_users())
    record_render(
        AUDIT_DB_PATH,
        serial_number=serial_number,
        device_name=device_name,
        platform=device.get("platform"),
        config_text=config_text,
    )
    return config_text


@app.get("/ztp/users/{serial_number}")
def get_device_users(serial_number: str):
    """users.yaml's logins for a device that can't take them in its
    config file - FRR's frr.conf is routing config only, so
    ztp_frr.py.j2 fetches this and creates the Linux accounts itself
    (login shell vtysh). Same exposure as /ztp/config, which already
    carries these users for cEOS/SR Linux: OOB-reachable, known serials
    only."""
    data = load_data_model(DATA_MODEL_PATH)
    device_name, _ = find_device_by_serial(data, serial_number)
    if device_name is None:
        raise HTTPException(
            status_code=404,
            detail=f"no device found for serial_number {serial_number} in data/netbox-export.json",
        )
    return [
        {"username": u["username"], "auth": u["auth"], "value": u["value"]}
        for u in _load_users()
    ]


@app.get("/debug/renders/{serial_number}")
def get_renders_for_serial(serial_number: str):
    """History of configs actually served to this serial over the real
    /ztp/config/ path, most recent first. Empty list (not 404) if the
    serial has never booted against this ztp-server - that's the normal
    state for a device that hasn't shown up yet, not an error."""
    return list_renders(AUDIT_DB_PATH, serial_number=serial_number)


@app.get("/debug/data-model")
def get_data_model():
    """Raw view of the JSON data model ztp-server currently has loaded -
    exactly what every real ZTP request reads from (the same file the
    5-minute cron `git pull` keeps in sync with NetBox's own export).
    Read-only, no side effects, not used by any ZTP path."""
    return load_data_model(DATA_MODEL_PATH)


@app.get("/debug/validate")
def get_data_model_validation():
    """Runs the same schema.validate.validate_data_model() check used by
    generate_topology.py --check and this project's CI, against the data
    model ztp-server currently has loaded - a live, structured
    Infrastructure-as-Code compliance check reachable over HTTP, not
    just from a local checkout. 422 (not 200) when invalid, so this is
    programmatically checkable, not just human-readable. Read-only, no
    side effects, not used by any ZTP path."""
    data = load_data_model(DATA_MODEL_PATH)
    errors = validate_data_model(data)
    result = {"valid": len(errors) == 0, "errors": errors}
    if errors:
        return JSONResponse(status_code=422, content=result)
    return result


@app.get("/debug/config/{device_name}", response_class=PlainTextResponse)
def get_device_config_by_name(device_name: str):
    """Manual-verification convenience route, keyed by device name
    instead of serial_number - the real /ztp/config/ route stays
    serial-keyed since that's what a booting device actually provides,
    but a human checking output shouldn't have to look a serial up
    first just to curl it. Read-only, same rendering logic as the real
    route, no side effects, not used by any ZTP path."""
    data = load_data_model(DATA_MODEL_PATH)
    if device_name not in data.get("devices", {}):
        raise HTTPException(
            status_code=404,
            detail=f"no device named '{device_name}' in data/netbox-export.json",
        )
    return render_config(device_name, data, users=_load_users())


def _check_reachability(mgmt_ip):
    """mgmt_ip is CIDR (e.g. "172.20.20.11/24") - the containerlab Docker
    mgmt network address, the only reachability target present on every
    device role uniformly (the fabric OOB "ip" field is only ever
    populated for spines/ztp-server, since leafs DHCP theirs
    dynamically and it's never recorded back to the data model).
    ztp-server's own eth0 sits on this same Docker bridge (its own
    mgmt_ip), so every other device's mgmt_ip is directly reachable from
    here without crossing into the fabric OOB network at all."""
    host = mgmt_ip.split("/")[0]
    try:
        ping_ok = (
            subprocess.run(
                ["ping", "-c", "1", "-W", "1", host],
                capture_output=True,
                timeout=3,
            ).returncode
            == 0
        )
    except (subprocess.TimeoutExpired, OSError):
        ping_ok = False
    ssh_port_open = False
    try:
        with socket.create_connection((host, 22), timeout=1):
            ssh_port_open = True
    except OSError:
        ssh_port_open = False
    return {"ip": host, "ping": ping_ok, "ssh_port_open": ssh_port_open}


@app.get("/debug/health")
def get_health():
    """Quick reachability check for every device in the currently-loaded
    data model, plus an honest observability status - this project has
    no metrics/logging pipeline of any kind, so that block says so
    rather than pretending to check something that doesn't exist.
    Checks run in parallel (a sequential ping/port-22 pair per device,
    up to 9 devices, would otherwise make this "quick" check anything
    but). Read-only, no side effects, not used by any ZTP path."""
    data = load_data_model(DATA_MODEL_PATH)
    devices = data.get("devices", {})

    def check_one(item):
        name, device = item
        mgmt_ip = device.get("mgmt_ip")
        if not mgmt_ip:
            return name, {"mgmt_ip": None, "reachability": None}
        return name, {"mgmt_ip": mgmt_ip, "reachability": _check_reachability(mgmt_ip)}

    with ThreadPoolExecutor(max_workers=len(devices) or 1) as pool:
        results = dict(pool.map(check_one, devices.items()))

    return {
        "devices": results,
        "observability": {
            "implemented": False,
            "note": (
                "no metrics/logging pipeline exists yet - no Prometheus, "
                "Grafana, syslog collection, or gNMI telemetry anywhere in "
                "this project. The reachability checks above are the only "
                "live signal this endpoint can offer today."
            ),
        },
    }
