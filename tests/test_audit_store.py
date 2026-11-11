from ztp_server.audit_store import list_renders, record_render


def test_record_render_then_list_returns_it(tmp_path):
    db_path = tmp_path / "audit.db"

    record_render(
        db_path,
        serial_number="CEOS-LEAF1-0001",
        device_name="leaf-01-us-east-01a",
        platform="ceos",
        config_text="hostname leaf-01-us-east-01a\n",
    )

    renders = list_renders(db_path)

    assert len(renders) == 1
    assert renders[0]["serial_number"] == "CEOS-LEAF1-0001"
    assert renders[0]["device_name"] == "leaf-01-us-east-01a"
    assert renders[0]["platform"] == "ceos"
    assert renders[0]["config_text"] == "hostname leaf-01-us-east-01a\n"
    assert renders[0]["rendered_at"]  # non-empty timestamp, exact format not asserted


def test_list_renders_filters_by_serial_number(tmp_path):
    db_path = tmp_path / "audit.db"
    record_render(db_path, "SERIAL-A", "device-a", "ceos", "config a")
    record_render(db_path, "SERIAL-B", "device-b", "ceos", "config b")

    renders = list_renders(db_path, serial_number="SERIAL-B")

    assert len(renders) == 1
    assert renders[0]["serial_number"] == "SERIAL-B"


def test_list_renders_most_recent_first(tmp_path):
    db_path = tmp_path / "audit.db"
    record_render(db_path, "SERIAL-A", "device-a", "ceos", "config v1")
    record_render(db_path, "SERIAL-A", "device-a", "ceos", "config v2")

    renders = list_renders(db_path, serial_number="SERIAL-A")

    assert [r["config_text"] for r in renders] == ["config v2", "config v1"]


def test_list_renders_on_missing_db_returns_empty_list(tmp_path):
    db_path = tmp_path / "does-not-exist.db"

    assert list_renders(db_path) == []
