import threading

import ztp_cli.cli as cli_module
from ztp_cli.cli import build_parser, main


def test_generate_data_dispatches_to_export_data_model_main(monkeypatch):
    calls = []

    def fake_export_main(argv):
        calls.append(argv)
        return 0

    import netbox.export_data_model

    monkeypatch.setattr(netbox.export_data_model, "main", fake_export_main)

    exit_code = main(["generate-data"])
    assert exit_code == 0
    assert calls == [[]]


def test_start_defaults_to_localhost_for_local_dev():
    args = build_parser().parse_args(["start"])
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_start_spawns_uvicorn_with_given_host_and_port(monkeypatch):
    calls = []

    def fake_supervise(host, port):
        calls.append((host, port))
        return 0

    monkeypatch.setattr(cli_module, "_supervise", fake_supervise)

    exit_code = main(["start", "--host", "0.0.0.0", "--port", "80"])
    assert exit_code == 0
    assert calls == [("0.0.0.0", 80)]


class _FakeProc:
    """Fake subprocess.Popen; never spawns a real process."""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.returncode


def test_supervise_spawns_uvicorn_once_and_returns_its_exit_code(monkeypatch):
    spawned = []

    def fake_spawn(host, port):
        spawned.append((host, port))
        return _FakeProc(returncode=3)

    monkeypatch.setattr(cli_module, "_spawn_uvicorn", fake_spawn)

    exit_code = cli_module._supervise("127.0.0.1", 8000, poll_interval=0.01)

    assert exit_code == 3
    assert spawned == [("127.0.0.1", 8000)]


def test_supervise_restarts_uvicorn_in_place_on_soft_restart_request(monkeypatch):
    # Pre-set Event instead of a real OS signal.
    procs = [_FakeProc(returncode=0), _FakeProc(returncode=0)]
    spawned = []

    def fake_spawn(host, port):
        spawned.append((host, port))
        return procs[len(spawned) - 1]

    monkeypatch.setattr(cli_module, "_spawn_uvicorn", fake_spawn)

    restart_requested = threading.Event()
    restart_requested.set()

    exit_code = cli_module._supervise(
        "127.0.0.1", 8000, restart_requested=restart_requested, poll_interval=0.01
    )

    assert exit_code == 0
    # Spawned twice; the original is terminated.
    assert spawned == [("127.0.0.1", 8000), ("127.0.0.1", 8000)]
    assert procs[0].terminated is True
    assert procs[1].terminated is False


def test_rebuild_refuses_outside_a_container(monkeypatch, capsys):
    monkeypatch.delenv("ZTP_FORCE_REBUILD", raising=False)
    monkeypatch.setattr(cli_module.Path, "exists", lambda self: False)

    def fail_if_called(pid, sig):
        raise AssertionError("os.kill should not be called outside a container")

    monkeypatch.setattr(cli_module.os, "kill", fail_if_called)

    exit_code = main(["rebuild"])
    assert exit_code == 1
    assert "refusing" in capsys.readouterr().err


def test_rebuild_sends_sighup_to_pid_1_inside_a_container(monkeypatch):
    monkeypatch.setattr(cli_module.Path, "exists", lambda self: True)
    calls = []
    monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    exit_code = main(["rebuild"])
    assert exit_code == 0
    assert calls == [(1, cli_module.signal.SIGHUP)]


def test_rebuild_force_override_works_without_dockerenv(monkeypatch):
    monkeypatch.setattr(cli_module.Path, "exists", lambda self: False)
    monkeypatch.setenv("ZTP_FORCE_REBUILD", "1")
    calls = []
    monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    exit_code = main(["rebuild"])
    assert exit_code == 0
    assert calls == [(1, cli_module.signal.SIGHUP)]


def test_stop_refuses_outside_a_container(monkeypatch, capsys):
    monkeypatch.delenv("ZTP_FORCE_REBUILD", raising=False)
    monkeypatch.setattr(cli_module.Path, "exists", lambda self: False)

    def fail_if_called(pid, sig):
        raise AssertionError("os.kill should not be called outside a container")

    monkeypatch.setattr(cli_module.os, "kill", fail_if_called)

    exit_code = main(["stop"])
    assert exit_code == 1
    assert "refusing" in capsys.readouterr().err


def test_stop_sends_sigterm_to_pid_1_inside_a_container(monkeypatch):
    monkeypatch.setattr(cli_module.Path, "exists", lambda self: True)
    calls = []
    monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    exit_code = main(["stop"])
    assert exit_code == 0
    assert calls == [(1, cli_module.signal.SIGTERM)]


def test_stop_force_override_works_without_dockerenv(monkeypatch):
    monkeypatch.setattr(cli_module.Path, "exists", lambda self: False)
    monkeypatch.setenv("ZTP_FORCE_REBUILD", "1")
    calls = []
    monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    exit_code = main(["stop"])
    assert exit_code == 0
    assert calls == [(1, cli_module.signal.SIGTERM)]


def test_supervise_stops_without_respawning_on_stop_request(monkeypatch):
    proc = _FakeProc(returncode=0)
    spawned = []

    def fake_spawn(host, port):
        spawned.append((host, port))
        return proc

    monkeypatch.setattr(cli_module, "_spawn_uvicorn", fake_spawn)

    stop_requested = threading.Event()
    stop_requested.set()

    exit_code = cli_module._supervise(
        "127.0.0.1", 8000, stop_requested=stop_requested, poll_interval=0.01
    )

    assert exit_code == 0
    # Spawned once only - a stop request must not trigger a respawn.
    assert spawned == [("127.0.0.1", 8000)]
    assert proc.terminated is True


def test_spawn_uvicorn_redirects_output_to_the_log_file(monkeypatch, tmp_path):
    log_path = tmp_path / "ztp-app.log"
    monkeypatch.setattr(cli_module, "LOG_PATH", log_path)

    captured = {}

    def fake_popen(cmd, stdout=None, stderr=None):
        captured["cmd"] = cmd
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return "a-fake-proc"

    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)

    result = cli_module._spawn_uvicorn("127.0.0.1", 8000)

    assert result == "a-fake-proc"
    assert captured["stderr"] == cli_module.subprocess.STDOUT
    # stdout opened in append mode on the log path.
    assert captured["stdout"].name == str(log_path)
    assert captured["stdout"].mode == "a"
    captured["stdout"].close()


def test_logs_defaults_to_50_lines_no_follow():
    args = build_parser().parse_args(["logs"])
    assert args.lines == 50
    assert args.follow is False


def test_logs_tails_the_log_file(monkeypatch, tmp_path, capfd):
    # capfd, since `ztp logs` writes to the inherited fd 1.
    log_path = tmp_path / "ztp-app.log"
    log_path.write_text("line one\nline two\nline three\n")
    monkeypatch.setattr(cli_module, "LOG_PATH", log_path)

    exit_code = main(["logs", "--lines", "2"])

    assert exit_code == 0
    out = capfd.readouterr().out
    assert "line two" in out
    assert "line three" in out
    assert "line one" not in out


def test_logs_reports_friendly_message_when_log_file_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli_module, "LOG_PATH", tmp_path / "does-not-exist.log")

    exit_code = main(["logs"])

    assert exit_code == 1
    assert "No logs yet" in capsys.readouterr().err


def _write_fake_proc_entry(proc_dir, pid, argv0):
    entry = proc_dir / str(pid)
    entry.mkdir()
    (entry / "cmdline").write_bytes(argv0.encode() + b"\x00--conf-file=/app/dhcp/dnsmasq.conf\x00")


def test_find_dnsmasq_pids_finds_only_dnsmasq_among_real_proc_entries(tmp_path):
    _write_fake_proc_entry(tmp_path, 123, "dnsmasq")
    _write_fake_proc_entry(tmp_path, 456, "/usr/sbin/sshd")
    (tmp_path / "self").mkdir()  # non-numeric, must be skipped

    pids = cli_module._find_dnsmasq_pids(str(tmp_path))

    assert pids == [123]


def test_find_dnsmasq_pids_empty_when_none_running(tmp_path):
    _write_fake_proc_entry(tmp_path, 456, "/usr/sbin/sshd")

    assert cli_module._find_dnsmasq_pids(str(tmp_path)) == []


def test_parse_dhcp_conf_reads_interface_and_range(tmp_path):
    conf = tmp_path / "dnsmasq.conf"
    conf.write_text(
        "interface=eth1\n"
        "bind-interfaces\n"
        "dhcp-range=10.0.0.140,10.0.0.199,255.255.255.128,12h\n"
    )

    interface, dhcp_range = cli_module._parse_dhcp_conf(conf)

    assert interface == "eth1"
    assert dhcp_range == "10.0.0.140,10.0.0.199,255.255.255.128,12h"


def test_read_dhcp_leases_returns_lease_lines(tmp_path):
    lease_file = tmp_path / "dnsmasq.leases"
    lease_file.write_text(
        "1234567890 aa:bb:cc:dd:ee:ff 10.0.0.141 leaf-01 *\n"
        "1234567891 11:22:33:44:55:66 10.0.0.142 leaf-02 *\n"
    )

    leases = cli_module._read_dhcp_leases(lease_file)

    assert len(leases) == 2
    assert "10.0.0.141" in leases[0]


def test_read_dhcp_leases_empty_when_file_missing(tmp_path):
    assert cli_module._read_dhcp_leases(tmp_path / "no-such-file") == []


def test_dhcp_status_reports_running_interface_range_and_leases(monkeypatch, tmp_path, capsys):
    conf = tmp_path / "dnsmasq.conf"
    conf.write_text("interface=eth1\ndhcp-range=10.0.0.140,10.0.0.199,255.255.255.128,12h\n")
    lease_file = tmp_path / "dnsmasq.leases"
    lease_file.write_text("1234567890 aa:bb:cc:dd:ee:ff 10.0.0.141 leaf-01 *\n")

    monkeypatch.setattr(cli_module, "DHCP_CONF_PATH", conf)
    monkeypatch.setattr(cli_module, "DHCP_LEASE_PATH", lease_file)
    monkeypatch.setattr(cli_module, "_find_dnsmasq_pids", lambda: [26])

    exit_code = main(["dhcp-status"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "running" in out
    assert "26" in out
    assert "eth1" in out
    assert "10.0.0.140,10.0.0.199,255.255.255.128,12h" in out
    assert "10.0.0.141" in out


def test_dhcp_status_reports_not_running(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli_module, "DHCP_CONF_PATH", tmp_path / "dnsmasq.conf")
    monkeypatch.setattr(cli_module, "DHCP_LEASE_PATH", tmp_path / "dnsmasq.leases")
    monkeypatch.setattr(cli_module, "_find_dnsmasq_pids", lambda: [])

    exit_code = main(["dhcp-status"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "NOT running" in out


def test_dhcp_restart_kills_running_dnsmasq_for_the_self_healing_loop_to_respawn(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli_module, "_find_dnsmasq_pids", lambda: [26])
    calls = []
    monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    exit_code = main(["dhcp-restart"])

    assert exit_code == 0
    assert calls == [(26, cli_module.signal.SIGTERM)]


def test_dhcp_restart_reports_nothing_to_do_when_not_running(monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "_find_dnsmasq_pids", lambda: [])

    def fail_if_called(pid, sig):
        raise AssertionError("os.kill should not be called when dnsmasq isn't running")

    monkeypatch.setattr(cli_module.os, "kill", fail_if_called)

    exit_code = main(["dhcp-restart"])

    assert exit_code == 1
    assert "not running" in capsys.readouterr().err
