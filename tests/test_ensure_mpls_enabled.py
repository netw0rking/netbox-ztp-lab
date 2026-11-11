from scripts.ensure_mpls_enabled import (
    ensure_mpls_modules,
    ensure_mpls_sysctls,
    find_backbone_interfaces,
)


def test_find_backbone_interfaces_returns_only_bbr_backbone_role_interfaces():
    data = {
        "devices": {
            "bbr-01-us-central-01a": {
                "role": "bbr",
                "interfaces": {
                    "eth1": {"role": "oob"},
                    "eth3": {"role": "backbone"},
                    "eth4": {"role": "backbone"},
                },
            },
            "esr-01-us-central-01a": {
                "role": "leaf",
                "interfaces": {"eth3": {"role": "access"}},
            },
        }
    }

    assert find_backbone_interfaces(data) == {
        "bbr-01-us-central-01a": ["eth3", "eth4"],
    }


def test_find_backbone_interfaces_returns_empty_dict_when_no_bbrs():
    data = {"devices": {"esr-01-us-central-01a": {"role": "leaf", "interfaces": {}}}}

    assert find_backbone_interfaces(data) == {}


def test_ensure_mpls_modules_skips_modprobe_when_already_loaded():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            stdout = "mpls_router 45056 1\nmpls_iptunnel 16384 0\n"
            returncode = 0

        return Result()

    ensure_mpls_modules(run=fake_run)

    # modprobe must not run again once both modules are already present.
    assert ["sudo", "modprobe", "mpls_router"] not in calls
    assert ["sudo", "modprobe", "mpls_iptunnel"] not in calls


def test_ensure_mpls_modules_loads_when_missing():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            stdout = ""  # lsmod-equivalent: nothing loaded
            returncode = 0

        return Result()

    ensure_mpls_modules(run=fake_run)

    assert ["sudo", "modprobe", "mpls_router"] in calls
    assert ["sudo", "modprobe", "mpls_iptunnel"] in calls


def test_ensure_mpls_sysctls_sets_platform_labels_and_per_interface_input():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            # sysctl -n (read) returns the current value; this fake
            # always reports "unset" so every write actually happens.
            stdout = "0\n"
            returncode = 0

        return Result()

    ensure_mpls_sysctls({"bbr-01-us-central-01a": ["eth3", "eth4"]}, run=fake_run)

    assert [
        "docker", "exec", "bbr-01-us-central-01a",
        "sysctl", "-w", "net.mpls.platform_labels=1048575",
    ] in calls
    assert [
        "docker", "exec", "bbr-01-us-central-01a",
        "sysctl", "-w", "net.mpls.conf.eth3.input=1",
    ] in calls
    assert [
        "docker", "exec", "bbr-01-us-central-01a",
        "sysctl", "-w", "net.mpls.conf.eth4.input=1",
    ] in calls


def test_ensure_mpls_sysctls_skips_write_when_already_set():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            stdout = "1048575\n" if "platform_labels" in " ".join(cmd) else "1\n"
            returncode = 0

        return Result()

    ensure_mpls_sysctls({"bbr-01-us-central-01a": ["eth3"]}, run=fake_run)

    assert not any(c[-2:-1] == ["-w"] for c in calls)
