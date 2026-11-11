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
    """Convert e1-N endpoint names to SRLinux ethernet-1/N names."""
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
    # users defaults to an empty list, so templates always render.
    return template.render(hostname=device_name, users=users or [], **device)
