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

# Default data model path; overridable via env var.
_REPO_ROOT = Path(__file__).parent.parent
DATA_MODEL_PATH = os.environ.get("DATA_MODEL_PATH", str(_REPO_ROOT / "data" / "netbox-export.json"))
# Audit DB of served configs; overridable via env var.
AUDIT_DB_PATH = os.environ.get("AUDIT_DB_PATH", str(_REPO_ROOT / "data" / "audit.db"))
# Optional device-login secrets, mounted from the secrets repo.
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
        # Missing or unreadable users file means no users, not an error.
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
    """Logins for FRR devices, which cannot take them in frr.conf."""
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
    """Config history for a serial, most recent first. Empty if none."""
    return list_renders(AUDIT_DB_PATH, serial_number=serial_number)


@app.get("/debug/data-model")
def get_data_model():
    """Raw view of the JSON data model currently loaded."""
    return load_data_model(DATA_MODEL_PATH)


@app.get("/debug/validate")
def get_data_model_validation():
    """Run validate_data_model() on the loaded data model. 422 if invalid."""
    data = load_data_model(DATA_MODEL_PATH)
    errors = validate_data_model(data)
    result = {"valid": len(errors) == 0, "errors": errors}
    if errors:
        return JSONResponse(status_code=422, content=result)
    return result


@app.get("/debug/config/{device_name}", response_class=PlainTextResponse)
def get_device_config_by_name(device_name: str):
    """Config for a device by name, for manual checks. Not used by ZTP."""
    data = load_data_model(DATA_MODEL_PATH)
    if device_name not in data.get("devices", {}):
        raise HTTPException(
            status_code=404,
            detail=f"no device named '{device_name}' in data/netbox-export.json",
        )
    return render_config(device_name, data, users=_load_users())


def _check_reachability(mgmt_ip):
    """Ping a device mgmt IP from the container bridge."""
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
    """Reachability check for every device in the loaded data model."""
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
