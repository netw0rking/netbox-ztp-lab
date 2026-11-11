from ztp_server.render import _env


def test_ceos_bootstrap_script_curls_config_endpoint():
    template = _env.get_template("ztp_ceos.sh.j2")
    output = template.render(server_host="10.0.0.130")
    assert "10.0.0.130/ztp/config/" in output


def test_srlinux_bootstrap_script_curls_config_endpoint():
    template = _env.get_template("ztp_srlinux.py.j2")
    output = template.render(server_host="10.0.0.130")
    assert "10.0.0.130/ztp/config/" in output


def _render_full_frr_bootstrap():
    # All optional branches enabled.
    return _env.get_template("ztp_frr.py.j2").render(
        server_host="10.1.1.254",
        serial="FRR-TEST-0001",
        oob_interface="eth1",
        oob_ip="10.1.1.133/25",
        fabric_interfaces=[("eth2", "10.1.1.36/31")],
        overlay_interfaces=["eth4"],
        access_interfaces=[("eth5", "10.99.0.1/31")],
        loopback="10.1.1.14/32",
        overlay_vni=10010,
        overlay_vlan_id=10,
        overlay_test_ip="10.99.100.1/24",
        l3vni=50001,
        vrf_name="tenant1",
        sr_policies=[{
            "color": 100, "endpoint": "fc00:200::1", "name": "via-south",
            "segments": [{"index": 10, "nai_prefix": "fc00:300::/48"}],
        }],
    )


def test_frr_bootstrap_renders_valid_python():
    compile(_render_full_frr_bootstrap(), "ztp_bootstrap.py", "exec")


def test_frr_bootstrap_is_idempotent_for_addresses_and_links():
    # Addresses use `ip addr replace`; links go through ensure_link().
    output = _render_full_frr_bootstrap()
    assert '"ip", "addr", "add"' not in output
    assert output.count('"ip", "link", "add"') == 1
    assert 'def ensure_link(' in output
    assert '"ip", "addr", "replace"' in output


def test_frr_bootstrap_only_restarts_pathd_when_dead():
    # A re-run must not start a second pathd.
    output = _render_full_frr_bootstrap()
    assert "if not _pathd_alive():" in output
    assert output.index("if not _pathd_alive():") < output.index("/usr/lib/frr/pathd -d")


def test_frr_bootstrap_dry_runs_config_before_applying():
    # vtysh -C must run and gate the real apply.
    output = _render_full_frr_bootstrap()
    dry_run = '["vtysh", "-C", "-f", "/etc/frr/frr.conf"], check=True'
    apply = '["vtysh", "-f", "/etc/frr/frr.conf"], check=True'
    assert '"/etc/frr/vtysh.conf"' in output
    assert output.index(dry_run) < output.index(apply)


def test_frr_bootstrap_provisions_vtysh_login_users():
    # ssh_key users need a '*' password, not a locked one.
    output = _render_full_frr_bootstrap()
    assert "/ztp/users/" in output
    assert '"-s", "/usr/bin/vtysh", "-G", "frrvty"' in output
    assert '"chpasswd"' in output
    assert "authorized_keys" in output
    assert ":*:" in output


def test_frr_bootstrap_waits_for_the_daemons_before_applying_config():
    # Wait until vtysh can reach the daemons before applying.
    output = _render_full_frr_bootstrap()
    wait = output.index('["vtysh", "-c", "show version"]')
    assert wait < output.index('["vtysh", "-f", "/etc/frr/frr.conf"], check=True')
