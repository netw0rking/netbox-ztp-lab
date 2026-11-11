import json


def load_data_model(path):
    with open(path) as f:
        return json.load(f)


def find_device_by_serial(data, serial_number):
    for name, device in data.get("devices", {}).items():
        if device.get("serial_number") == serial_number:
            return name, device
    return None, None
