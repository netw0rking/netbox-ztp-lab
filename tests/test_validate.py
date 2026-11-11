import json
from pathlib import Path

from schema.validate import validate_data_model

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    with open(FIXTURES / name) as f:
        return json.load(f)


def test_valid_data_model_has_no_errors():
    errors = validate_data_model(load("valid_data_model.json"))
    assert errors == []


def test_missing_bgp_asn_is_reported():
    errors = validate_data_model(load("missing_asn.json"))
    assert any("bgp_asn" in e and "spine-01-us-east-01a" in e for e in errors)


def test_bad_cable_reference_is_reported():
    errors = validate_data_model(load("bad_cable_ref.json"))
    assert any("Ethernet99" in e for e in errors)


def test_duplicate_ip_is_reported():
    errors = validate_data_model(load("duplicate_ip.json"))
    assert any("10.0.0.32/31" in e for e in errors)


def test_backbone_role_link_to_a_device_outside_this_fixture_is_not_an_error():
    # Ring peers may live outside this single-site fixture.
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "interfaces": {
                    "eth3": {
                        "role": "backbone",
                        "ip": "10.1.0.1/31",
                        "connects_to": {
                            "device": "bbr-01-us-south-01a",
                            "interface": "eth1",
                        },
                    }
                },
            }
        }
    }

    errors = validate_data_model(data)

    assert errors == []


def test_duplicate_sr_index_is_reported():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {"role": "bbr", "sr_index": 101},
            "bbr-01-us-west-01a": {"role": "bbr", "sr_index": 101},
        }
    }

    errors = validate_data_model(data)

    assert any(
        "sr_index" in e and "101" in e
        and "bbr-01-us-central-01a" in e and "bbr-01-us-west-01a" in e
        for e in errors
    )


def test_unique_sr_index_has_no_error():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {"role": "bbr", "sr_index": 101},
            "bbr-01-us-west-01a": {"role": "bbr", "sr_index": 102},
        }
    }

    assert validate_data_model(data) == []
