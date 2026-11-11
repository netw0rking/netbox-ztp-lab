from ztp_server.users import load_users


def test_load_users_parses_users_yaml(tmp_path):
    users_path = tmp_path / "users.yaml"
    users_path.write_text(
        "users:\n"
        "  - username: netops\n"
        "    role: admin\n"
        "    auth: ssh_key\n"
        "    value: ssh-rsa AAAAtest\n"
        "  - username: netautobot\n"
        "    role: admin\n"
        "    auth: password\n"
        "    value: fake-password\n"
    )
    users = load_users(users_path)
    assert users == [
        {"username": "netops", "role": "admin", "auth": "ssh_key", "value": "ssh-rsa AAAAtest"},
        {"username": "netautobot", "role": "admin", "auth": "password", "value": "fake-password"},
    ]


def test_load_users_returns_empty_list_for_missing_file(tmp_path):
    assert load_users(tmp_path / "does-not-exist.yaml") == []


def test_load_users_returns_empty_list_for_empty_file(tmp_path):
    users_path = tmp_path / "users.yaml"
    users_path.write_text("")
    assert load_users(users_path) == []


def test_load_users_returns_empty_list_when_users_key_absent(tmp_path):
    users_path = tmp_path / "users.yaml"
    users_path.write_text("some_other_key: true\n")
    assert load_users(users_path) == []
