#!/bin/sh
set -e

if [ -f /root/.ssh_deploy_key ]; then
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    cp /root/.ssh_deploy_key /root/.ssh/id_ed25519
    chmod 600 /root/.ssh/id_ed25519
    cp /root/.ssh_known_hosts /root/.ssh/known_hosts

    git config --global core.sshCommand "ssh -i /root/.ssh/id_ed25519 -o UserKnownHostsFile=/root/.ssh/known_hosts"
    git remote set-url origin git@github.com:netw0rking/netbox-ztp-lab.git
fi

# Operator SSH access into ztp-server itself, key-only (see
# deploy/sshd_local.conf - PasswordAuthentication is disabled entirely).
# Every ssh_key-type entry in the same bind-mounted users.yaml used to
# authorize logins on cEOS/SRLinux devices (see ztp_server/users.py) gets
# added here too - one source of truth for "who can get in" across this
# whole lab, not just a single hardcoded key.
if [ -f /root/secrets/users.yaml ]; then
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    python3 -c "
import yaml
with open('/root/secrets/users.yaml') as f:
    data = yaml.safe_load(f) or {}
for user in data.get('users', []):
    if user.get('auth') == 'ssh_key':
        print(user['value'])
" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi
mkdir -p /run/sshd
ssh-keygen -A
/usr/sbin/sshd

cron

# Wait for containerlab to finish wiring eth1 before starting dnsmasq -
# confirmed live: this entrypoint runs before containerlab attaches this
# node's veth links, so an immediate dnsmasq start fails with "unknown
# interface eth1" and the process dies silently (backgrounded with &, so
# nothing here notices) - no DHCP is ever served, permanently, with no
# error visible short of checking the process table. A 30-second wait
# (the original fix) was not always enough - confirmed live, again,
# still failing the exact same way on a heavily loaded host well after
# that fix shipped. Widened to 120s, and - more importantly - wrapped in
# a restart loop rather than just a longer one-shot wait: dnsmasq is
# meant to run for the container's entire lifetime, so ANY unexpected
# exit (this race, or anything else) should self-heal a few seconds
# later instead of leaving DHCP permanently dead until the next
# redeploy.
(
    for i in $(seq 1 120); do
        [ -e /sys/class/net/eth1 ] && break
        sleep 1
    done
    while true; do
        # `|| true`: this whole subshell inherits `set -e` from the top
        # of this script, since backgrounding a compound command doesn't
        # disable it. Confirmed live: without this guard, the very first
        # time dnsmasq exited non-zero, `set -e` killed the entire
        # subshell right there (visible as a zombie `[entrypoint.sh]
        # <defunct>` process) instead of looping back around to retry -
        # completely defeating the point of this restart loop, on
        # exactly the same class of failure it was meant to survive.
        #
        # Per-site config rendered by generate_topology.py and
        # bind-mounted here when present; the static repo file is only
        # a fallback for a topology that doesn't bind one.
        conf=/app/dhcp/dnsmasq.conf
        [ -f /etc/dnsmasq.site.conf ] && conf=/etc/dnsmasq.site.conf
        dnsmasq --conf-file="$conf" --interface=eth1 --no-daemon || true
        sleep 5
    done
) &

# `ztp start` is a thin wrapper that execs this same uvicorn command
# (unchanged host/port); everything above this line (ssh, cron, dnsmasq)
# is unaffected by that change.
exec ztp start --host 0.0.0.0 --port 80
