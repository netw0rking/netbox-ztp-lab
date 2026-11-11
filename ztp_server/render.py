import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    trim_blocks=True,
    lstrip_blocks=True,
)

CONFIG_TEMPLATES = {
    "ceos": "ceos_config.j2",
    "srlinux": "srlinux_config.j2",
    "frr": "frr_config.j2",
}

_SRL_E1_RE = re.compile(r"^e1-(\d+)$")


def srl_interface_name(if_name):
    """Translate containerlab's srl endpoint convention (e1-N) to
    SRLinux's own real CLI interface name (ethernet-1/N).

    Confirmed live: `sr_cli` rejects "e1-1" outright as an invalid
    interface name - "Failed to parse value 'e1-1' for key 'name'
    (node 'interface')" - its own naming pattern requires the
    "ethernet-<slot>/<port>" form. containerlab's own topology-level
    endpoint convention for this kind ("e1-1", "e1-2", ...) is correct
    for wiring links, but was never meant to be used as a literal CLI
    interface identifier - that's a second, separate naming convention
    this project's rendered config never accounted for until this was
    hit live while getting SRLinux's config-apply step working at all.
    """
    match = _SRL_E1_RE.match(if_name)
    if match:
        return f"ethernet-1/{match.group(1)}"
    return if_name


_env.filters["srl_interface_name"] = srl_interface_name


def render_config(device_name, data, users=None):
    devices = data["devices"]
    if device_name not in devices:
        raise KeyError(f"no device named '{device_name}' in data model")

    device = devices[device_name]
    platform = device.get("platform")
    template_name = CONFIG_TEMPLATES.get(platform)
    if not template_name:
        raise ValueError(f"no config template for platform '{platform}'")

    template = _env.get_template(template_name)
    # `users` is a list of device-login credentials (see
    # ztp_server.users.load_users), a deployment secret from the
    # separate, private netbox-ztp-lab-secrets repo - not NetBox network
    # data, so it never lives in `device`, and is never committed to
    # this repo's own git history. Defaults to an empty list (not None)
    # so every template's `{% for user in users %}` works unconditionally
    # whether or not the secrets repo is even cloned on this machine.
    return template.render(hostname=device_name, users=users or [], **device)
