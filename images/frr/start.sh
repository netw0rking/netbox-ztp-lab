#!/bin/sh
# Host keys generated per container at boot, never baked into the image
# (a baked key would be identical on every node).
ssh-keygen -A
/usr/sbin/sshd -e
# Hand off to upstream's own FRR startup (watchfrr + daemons).
exec /usr/lib/frr/docker-start
