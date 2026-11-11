#!/bin/sh
set -e

if [ -f /root/.ssh_deploy_key ]; then
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    cp /root/.ssh_deploy_key /root/.ssh/id_ed25519
    chmod 600 /root/.ssh/id_ed25519
    cp /root/.ssh_known_hosts /root/.ssh/known_hosts

    git config --global core.sshCommand "ssh -i /root/.ssh/id_ed25519 -o UserKnownHostsFile=/root/.ssh/known_hosts"
fi

# The repo is public, so the cron pull uses anonymous HTTPS.
git remote set-url origin https://github.com/netw0rking/netbox-ztp-lab.git

# Operator SSH keys come from users.yaml ssh_key entries.
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

# Wait for eth1, then run dnsmasq in a restart loop.
(
    for i in $(seq 1 120); do
        [ -e /sys/class/net/eth1 ] && break
        sleep 1
    done
    while true; do
        # || true: survive set -e. Per-site conf if mounted.
        conf=/app/dhcp/dnsmasq.conf
        [ -f /etc/dnsmasq.site.conf ] && conf=/etc/dnsmasq.site.conf
        dnsmasq --conf-file="$conf" --interface=eth1 --no-daemon || true
        sleep 5
    done
) &

# Hand off to ztp start (uvicorn on port 80).
exec ztp start --host 0.0.0.0 --port 80
