import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_path = tmp_path / "netbox-export.json"
    shutil.copy(FIXTURES / "valid_data_model.json", data_path)
    monkeypatch.setenv("DATA_MODEL_PATH", str(data_path))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))

    # Import after env var is set so main.py picks up the test path at module load.
    import importlib
    import ztp_server.main as main_module
    importlib.reload(main_module)

    return TestClient(main_module.app)


def test_get_config_for_known_serial(client):
    response = client.get("/ztp/config/CEOS-LEAF1-0001")
    assert response.status_code == 200
    assert "hostname leaf-01-us-east-01a" in response.text
    # USERS_YAML_PATH isn't set for this fixture, so it falls back to the
    # real default (/root/secrets/users.yaml) - a non-root test runner
    # can't read that path at all (PermissionError, handled the same way
    # as the earlier admin-key default), so no users are rendered.
    assert "netautobot" not in response.text


def test_get_config_includes_users_when_secrets_repo_present(tmp_path, monkeypatch):
    data_path = tmp_path / "netbox-export.json"
    shutil.copy(FIXTURES / "valid_data_model.json", data_path)
    monkeypatch.setenv("DATA_MODEL_PATH", str(data_path))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    users_yaml_path = tmp_path / "users.yaml"
    users_yaml_path.write_text(
        "users:\n"
        "  - username: netautobot\n"
        "    role: admin\n"
        "    auth: password\n"
        "    value: fake-password\n"
    )
    monkeypatch.setenv("USERS_YAML_PATH", str(users_yaml_path))

    import importlib
    import ztp_server.main as main_module
    importlib.reload(main_module)
    users_client = TestClient(main_module.app)

    response = users_client.get("/ztp/config/CEOS-LEAF1-0001")
    assert response.status_code == 200
    assert (
        "username netautobot privilege 15 role network-admin secret fake-password"
        in response.text
    )


def test_get_config_for_unknown_serial_returns_404(client):
    response = client.get("/ztp/config/NOT-A-REAL-SERIAL")
    assert response.status_code == 404
    assert "NOT-A-REAL-SERIAL" in response.json()["detail"]


def test_get_bootstrap_script_for_ceos(client):
    response = client.get("/ztp/script/ceos")
    assert response.status_code == 200
    assert "/ztp/config/" in response.text


def test_get_bootstrap_script_for_unknown_vendor_returns_404(client):
    response = client.get("/ztp/script/unknown-vendor")
    assert response.status_code == 404


def test_get_config_records_audit_entry(client):
    client.get("/ztp/config/CEOS-LEAF1-0001")

    response = client.get("/debug/renders/CEOS-LEAF1-0001")

    assert response.status_code == 200
    renders = response.json()
    assert len(renders) == 1
    assert renders[0]["device_name"] == "leaf-01-us-east-01a"
    assert renders[0]["platform"] == "ceos"
    assert "hostname leaf-01-us-east-01a" in renders[0]["config_text"]


def test_get_renders_for_serial_with_no_history_returns_empty_list(client):
    response = client.get("/debug/renders/NEVER-BOOTED-0001")

    assert response.status_code == 200
    assert response.json() == []


def test_debug_config_route_does_not_record_audit_entry(client):
    # /debug/config/ is a manual-verification convenience alias, not the
    # real ZTP path a device hits - it shouldn't pollute the audit trail
    # with renders no device ever actually received.
    client.get("/debug/config/leaf-01-us-east-01a")

    response = client.get("/debug/renders/CEOS-LEAF1-0001")

    assert response.json() == []


def test_get_debug_config_for_known_device_name(client):
    response = client.get("/debug/config/leaf-01-us-east-01a")
    assert response.status_code == 200
    assert "hostname leaf-01-us-east-01a" in response.text


def test_get_debug_config_for_unknown_device_name_returns_404(client):
    response = client.get("/debug/config/not-a-real-device")
    assert response.status_code == 404
    assert "not-a-real-device" in response.json()["detail"]


def test_get_data_model_returns_raw_json(client):
    response = client.get("/debug/data-model")
    assert response.status_code == 200
    body = response.json()
    assert "leaf-01-us-east-01a" in body["devices"]
    # Exactly the same file every real ZTP request reads from - not a
    # filtered or reshaped view.
    with open(FIXTURES / "valid_data_model.json") as f:
        assert body == json.load(f)


def test_get_validate_returns_valid_for_good_data_model(client):
    response = client.get("/debug/validate")
    assert response.status_code == 200
    assert response.json() == {"valid": True, "errors": []}


def test_get_validate_returns_422_for_bad_data_model(client, tmp_path, monkeypatch):
    # Same schema.validate.validate_data_model() check generate_topology.py
    # --check and CI already run locally - this proves it's reachable the
    # same way over HTTP, against whatever ztp-server currently has
    # loaded, not just from a local checkout.
    bad_data_path = tmp_path / "bad-netbox-export.json"
    shutil.copy(FIXTURES / "missing_asn.json", bad_data_path)
    monkeypatch.setenv("DATA_MODEL_PATH", str(bad_data_path))

    import importlib
    import ztp_server.main as main_module
    importlib.reload(main_module)
    bad_client = TestClient(main_module.app)

    response = bad_client.get("/debug/validate")
    assert response.status_code == 422
    body = response.json()
    assert body["valid"] is False
    assert len(body["errors"]) > 0


def test_check_reachability_unit(monkeypatch):
    # Direct unit test of the actual ping/port-22 logic against a real,
    # always-up loopback target - no mocking, so this is the one place
    # that proves the real subprocess/socket calls work at all, not just
    # that the endpoint plumbs a mocked result through correctly.
    import ztp_server.main as main_module

    result = main_module._check_reachability("127.0.0.1/32")
    assert result["ip"] == "127.0.0.1"
    assert result["ping"] is True
    # ssh_port_open is genuinely environment-dependent (whatever's
    # listening on the test runner's own :22, if anything) - not
    # asserted on.


def test_get_health_reports_reachability_and_observability_status(client, monkeypatch):
    # Mocked for determinism and speed - the real ping/port-22 logic
    # against 172.20.20.x addresses that don't exist in this test
    # environment is covered by test_check_reachability_unit instead.
    import ztp_server.main as main_module

    monkeypatch.setattr(
        main_module,
        "_check_reachability",
        lambda mgmt_ip: {"ip": mgmt_ip.split("/")[0], "ping": True, "ssh_port_open": True},
    )

    response = client.get("/debug/health")
    assert response.status_code == 200
    body = response.json()

    assert body["observability"] == {
        "implemented": False,
        "note": (
            "no metrics/logging pipeline exists yet - no Prometheus, "
            "Grafana, syslog collection, or gNMI telemetry anywhere in "
            "this project. The reachability checks above are the only "
            "live signal this endpoint can offer today."
        ),
    }

    leaf_result = body["devices"]["leaf-01-us-east-01a"]
    assert leaf_result["mgmt_ip"] == "172.20.20.11/24"
    assert leaf_result["reachability"] == {
        "ip": "172.20.20.11",
        "ping": True,
        "ssh_port_open": True,
    }
    assert set(body["devices"].keys()) == {
        "leaf-01-us-east-01a",
        "leaf-02-us-east-01a",
        "leaf-03-us-east-01a",
        "leaf-04-us-east-01a",
        "leaf-05-us-east-01a",
        "leaf-06-us-east-01a",
        "leaf-07-us-east-01a",
        "host-01-us-east-01a",
        "host-02-us-east-01a",
        "host-06-us-east-01a",
        "host-07-us-east-01a",
        "mgmt-switch-us-east-01a",
        "spine-01-us-east-01a",
        "spine-02-us-east-01a",
        "ztp-server-us-east-01a",
    }
    # mgmt-switch is now an ovs-bridge - no
    # container backs it, so it has no mgmt_ip to check reachability on.
    assert body["devices"]["mgmt-switch-us-east-01a"] == {"mgmt_ip": None, "reachability": None}


def _users_client(tmp_path, monkeypatch):
    data_path = tmp_path / "netbox-export.json"
    shutil.copy(FIXTURES / "valid_data_model.json", data_path)
    monkeypatch.setenv("DATA_MODEL_PATH", str(data_path))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    users_yaml_path = tmp_path / "users.yaml"
    users_yaml_path.write_text(
        "users:\n"
        "  - username: netops\n"
        "    role: admin\n"
        "    auth: ssh_key\n"
        "    value: ssh-rsa AAAAfake netops\n"
        "  - username: netautobot\n"
        "    role: admin\n"
        "    auth: password\n"
        "    value: fake-password\n"
    )
    monkeypatch.setenv("USERS_YAML_PATH", str(users_yaml_path))
    import importlib
    import ztp_server.main as main_module
    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_get_users_for_known_serial_returns_login_list(tmp_path, monkeypatch):
    # FRR has no config-file construct for local users (frr.conf is
    # routing config only), so its bootstrap fetches them separately and
    # creates Linux accounts itself.
    response = _users_client(tmp_path, monkeypatch).get("/ztp/users/FRR-LEAF2-0001")
    assert response.status_code == 200
    assert response.json() == [
        {"username": "netops", "auth": "ssh_key", "value": "ssh-rsa AAAAfake netops"},
        {"username": "netautobot", "auth": "password", "value": "fake-password"},
    ]


def test_get_users_for_unknown_serial_returns_404(tmp_path, monkeypatch):
    response = _users_client(tmp_path, monkeypatch).get("/ztp/users/NOT-A-REAL-SERIAL")
    assert response.status_code == 404
