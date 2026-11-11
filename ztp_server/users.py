from pathlib import Path

import yaml


def load_users(users_yaml_path):
    """Load the structured device-login credential list from the
    separate, private netbox-ztp-lab-secrets repo's users.yaml.

    Replaces the earlier one-off admin_ssh_pubkey/netautobot_password
    parameters - each entry here (username, role, auth type, value) gets
    rendered into every device's config the same way, so adding a third
    person's key is a data change in that other repo, not a template
    change here. Takes the direct path to users.yaml (not the secrets
    repo's directory) so it can be a single-file bind mount, the same
    pattern every other secret in this project already uses. Returns an
    empty list if the file doesn't exist at all (a fresh dev machine, or
    CI, where the secrets repo is never expected to be cloned) - same
    "optional, never referenced when absent" contract every secret in
    this project already follows; a missing users.yaml means every
    device simply renders with no local users at all.
    """
    users_path = Path(users_yaml_path)
    if not users_path.exists():
        return []
    data = yaml.safe_load(users_path.read_text()) or {}
    return data.get("users", [])
