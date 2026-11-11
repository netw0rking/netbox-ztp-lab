import json
from pathlib import Path

from ztp_server.data_store import find_device_by_serial, load_data_model

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_data_model_reads_json(tmp_path):
    sample = {"site": "x", "devices": {}}
    path = tmp_path / "data.json"
    path.write_text(json.dumps(sample))

    assert load_data_model(path) == sample


def test_find_device_by_serial_returns_match():
    data = load_data_model(FIXTURES / "valid_data_model.json")

    name, device = find_device_by_serial(data, "CEOS-LEAF3-0001")

    assert name == "leaf-03-us-east-01a"
    assert device["bgp_asn"] == 65013


def test_find_device_by_serial_returns_none_for_unknown_serial():
    data = load_data_model(FIXTURES / "valid_data_model.json")

    name, device = find_device_by_serial(data, "NOT-A-REAL-SERIAL")

    assert name is None
    assert device is None
