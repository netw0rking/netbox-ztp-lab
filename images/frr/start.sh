#!/bin/sh
# Host keys are generated at boot.
ssh-keygen -A
/usr/sbin/sshd -e
# Hand off to upstream FRR startup.
exec /usr/lib/frr/docker-start
